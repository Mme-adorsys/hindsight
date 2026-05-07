"""HybridRetriever — single-pass mixed Engram/Schema vector search (Epic 25 Story 15).

In the new CLS architecture both individual engram embeddings (Buffer) and
schema centroids (Cortex) live in the same Qdrant collection, distinguished
by ``payload.kind ∈ {"engram", "schema"}``. A single vector search returns a
mixed result list; this module then dispatches enrichment per kind:

  - ``kind="engram"``  → text + metadata from PostgreSQL ``memory_units`` /
                         ``engram_dictionary``.
  - ``kind="schema"``  → description, properties, evidence_engram_ids from a
                         Neo4j ``:Schema`` or ``:HyperSchema`` node.

The returned ``RetrievalHit`` is intentionally simple — Story 16 will use
``schema`` hits to resolve Top-N evidence engrams, and Story 17 layers
mode-dependent re-weighting on top of the same hit list.

Concept reference: docs/engram/concept.md § 3 (Storage-Architektur,
Retrieval-Fluss) and § 8 (Search & Retrieval).

Replaces the legacy ``EngramRetriever`` for the Recall path; the old class is
kept around until Story 18 cleanup so the existing Reflect/Reconsolidation
plumbing keeps working.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ..db_utils import acquire_with_retry
from ..qdrant_client import QdrantEngineClient
from ..response_models import RetrievalMode
from ..schema.schema_repository import get_schema as get_schema_node
from ..session.mode_config import MODE_PROFILES
from ..utils import fq_table

logger = logging.getLogger(__name__)

HitKind = Literal["engram", "schema"]


class RetrievalHit(BaseModel):
    """Mixed-kind hit produced by :class:`HybridRetriever`.

    Story 15 keeps the shape minimal: ``score`` is the raw Qdrant cosine
    similarity, ``payload`` is whatever Qdrant stored on the point, and the
    enrichment fields below are only populated for the matching ``kind``.
    """

    kind: HitKind
    id: UUID
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)

    # ---- engram-only enrichment ------------------------------------------------
    text: str | None = None
    fact_type: str | None = None
    context: str | None = None
    tags: list[str] = Field(default_factory=list)

    # ---- schema-only enrichment ------------------------------------------------
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence_engram_ids: list[UUID] = Field(default_factory=list)
    evidence_count: int | None = None
    schema_label: Literal["Schema", "HyperSchema"] | None = None


# Type aliases for the optional injected lookup callables — keeps the
# class itself decoupled from the concrete Neo4j / asyncpg shapes so it
# can be unit-tested with simple stubs.
SchemaLookup = Callable[[UUID, str], Awaitable[Any | None]]
EngramLookup = Callable[[list[UUID], str], Awaitable[dict[UUID, dict[str, Any]]]]


class HybridRetriever:
    """Vector-search-first recall over the mixed Qdrant collection."""

    def __init__(
        self,
        qdrant: QdrantEngineClient,
        neo4j: Any | None = None,
        pg_pool: Any | None = None,
        *,
        schema_lookup: SchemaLookup | None = None,
        engram_lookup: EngramLookup | None = None,
    ) -> None:
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._pg_pool = pg_pool
        self._schema_lookup = schema_lookup
        self._engram_lookup = engram_lookup

    async def retrieve(
        self,
        query_embedding: list[float],
        bank_id: str,
        *,
        k: int = 10,
        tags: list[str] | None = None,
        mode: RetrievalMode | None = None,
    ) -> list[RetrievalHit]:
        """Run one vector search across both kinds, then enrich per kind.

        Args:
            query_embedding: 384-dim query vector.
            bank_id: target memory bank.
            k: top-K hits to return.
            tags: optional Qdrant payload filter — applied to engram hits and,
                if a schema centroid carries the same tag rollup, also to
                schemas.
            mode: optional ``RetrievalMode``. When set, the raw cosine score
                is multiplied by ``w_schema``/``w_engram`` from the mode's
                profile (Story 17), and the hit list is re-sorted by the
                resulting weighted score before being returned.
        """
        must_filters: list[dict[str, Any]] = [{"key": "bank_id", "match": {"value": bank_id}}]
        if tags:
            for tag in tags:
                must_filters.append({"key": "tags", "match": {"value": tag}})

        raw_hits = await self._qdrant.search_similar(
            embedding=query_embedding,
            limit=k,
            filters={"must": must_filters},
            kind=None,  # mixed Engram + Schema hits
        )

        hits: list[RetrievalHit] = []
        engram_ids: list[UUID] = []
        schema_payloads: list[tuple[UUID, str]] = []  # (id, label) — label inferred from payload

        for raw in raw_hits:
            payload = raw.get("payload") or {}
            kind = payload.get("kind", "engram")
            try:
                hit_id = self._extract_id(payload, raw, kind)
            except (KeyError, ValueError) as exc:
                logger.warning("[HybridRetriever] Skipping hit with bad id (%s): %r", exc, payload)
                continue

            hit = RetrievalHit(
                kind=kind,
                id=hit_id,
                score=float(raw.get("score", 0.0)),
                payload=dict(payload),
            )
            if kind == "engram":
                engram_ids.append(hit_id)
            else:
                label = payload.get("schema_label", "Schema")
                if label not in ("Schema", "HyperSchema"):
                    label = "Schema"
                hit.schema_label = label  # type: ignore[assignment]
                schema_payloads.append((hit_id, label))
            hits.append(hit)

        await self._enrich_engrams(hits, engram_ids, bank_id)
        await self._enrich_schemas(hits, schema_payloads)

        if mode is not None:
            self._apply_mode_weighting(hits, mode)
        return hits

    @staticmethod
    def _apply_mode_weighting(hits: list[RetrievalHit], mode: RetrievalMode) -> None:
        """Multiply each hit's score by the mode's per-kind bias and re-sort.

        In-place to keep callers' references valid. Stable sort means ties
        preserve the Qdrant order — useful for tests and reproducibility.
        Unknown modes silently fall back to (1.0, 1.0) and only re-sort.
        """
        profile = MODE_PROFILES.get(mode)
        w_schema = profile.w_schema if profile is not None else 1.0
        w_engram = profile.w_engram if profile is not None else 1.0
        for hit in hits:
            mult = w_schema if hit.kind == "schema" else w_engram
            hit.score = hit.score * mult
        hits.sort(key=lambda h: h.score, reverse=True)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _extract_id(payload: dict[str, Any], raw: dict[str, Any], kind: HitKind) -> UUID:
        candidate = payload.get("schema_id") if kind == "schema" else payload.get("engram_id")
        if not candidate:
            candidate = raw.get("engram_id")  # search_similar fallback
        if not candidate:
            raise KeyError("payload missing engram_id/schema_id")
        return candidate if isinstance(candidate, UUID) else UUID(str(candidate))

    async def _enrich_engrams(
        self,
        hits: list[RetrievalHit],
        engram_ids: list[UUID],
        bank_id: str,
    ) -> None:
        if not engram_ids:
            return
        rows: dict[UUID, dict[str, Any]]
        if self._engram_lookup is not None:
            rows = await self._engram_lookup(engram_ids, bank_id)
        elif self._pg_pool is not None:
            rows = await self._fetch_engrams(engram_ids, bank_id)
        else:
            logger.debug("[HybridRetriever] No PG pool / engram_lookup wired — skipping engram enrichment")
            return

        for hit in hits:
            if hit.kind != "engram":
                continue
            row = rows.get(hit.id)
            if row is None:
                continue
            hit.text = row.get("text")
            hit.fact_type = row.get("fact_type")
            hit.context = row.get("context")
            tags = row.get("tags") or []
            hit.tags = list(tags)

    async def _fetch_engrams(
        self,
        engram_ids: list[UUID],
        bank_id: str,
    ) -> dict[UUID, dict[str, Any]]:
        sql = (
            f"SELECT mu.id::text AS id, mu.text, mu.fact_type, mu.context, ed.tags "
            f"FROM {fq_table('memory_units')} mu "
            f"LEFT JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id "
            f"WHERE mu.id = ANY($1::uuid[]) AND mu.bank_id = $2"
        )
        async with acquire_with_retry(self._pg_pool) as conn:
            rows = await conn.fetch(sql, [str(e) for e in engram_ids], bank_id)
        out: dict[UUID, dict[str, Any]] = {}
        for r in rows:
            out[UUID(r["id"])] = {
                "text": r["text"],
                "fact_type": r["fact_type"],
                "context": r["context"],
                "tags": r["tags"] or [],
            }
        return out

    async def _enrich_schemas(
        self,
        hits: list[RetrievalHit],
        schema_payloads: list[tuple[UUID, str]],
    ) -> None:
        if not schema_payloads:
            return
        if self._schema_lookup is None and self._neo4j is None:
            logger.debug("[HybridRetriever] No Neo4j / schema_lookup wired — skipping schema enrichment")
            return

        for hit in hits:
            if hit.kind != "schema":
                continue
            label = hit.schema_label or "Schema"
            try:
                if self._schema_lookup is not None:
                    schema = await self._schema_lookup(hit.id, label)
                else:
                    schema = await get_schema_node(self._neo4j, hit.id, label=label)
            except Exception as exc:  # best-effort enrichment
                logger.warning("[HybridRetriever] schema lookup failed for %s/%s: %s", label, hit.id, exc)
                continue
            if schema is None:
                continue
            hit.description = getattr(schema, "description", None)
            props = getattr(schema, "properties", None)
            hit.properties = dict(props) if props else {}
            evidence = getattr(schema, "evidence_engram_ids", None) or []
            hit.evidence_engram_ids = list(evidence)
            hit.evidence_count = getattr(schema, "evidence_count", None)


def build_default_hybrid_retriever(pg_pool: Any) -> HybridRetriever | None:
    """Construct a :class:`HybridRetriever` from the live default registry.

    Returns ``None`` when the global graph retriever isn't an ``EngramRetriever``
    (e.g. unit-test default, MPFP-only setups). Caller decides the fallback —
    typically continuing with the legacy 4-way retrieval pipeline. Stories 16
    and 17 layer top-N evidence resolution and mode weighting on top of this
    factory; Story 18 retires the legacy path once those land.
    """
    from .engram_retrieval import EngramRetriever
    from .retrieval import get_default_graph_retriever

    retriever = get_default_graph_retriever()
    if not isinstance(retriever, EngramRetriever):
        return None
    return HybridRetriever(qdrant=retriever.qdrant_client, neo4j=retriever.neo4j_client, pg_pool=pg_pool)
