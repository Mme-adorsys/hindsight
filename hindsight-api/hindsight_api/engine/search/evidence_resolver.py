"""Top-N Evidence resolution for Schema hits (Epic 25 Story 16).

A Schema's ``evidence_engram_ids`` is a Top-N pointer array baked at C2 write
time (``select_top_n_evidence`` — concept §4.2, Teyler & DiScenna 1986
indexing theory). At recall time we resolve those pointers back to actual
engrams so Reflect/Constructive Memory has concrete examples grounding the
Schema's generalisation.

Default ``RECALL_DEFAULT_EVIDENCE_N=3`` is intentionally lower than the
write-time ``SCHEMA_TOP_N_EVIDENCE=5``: 5 keeps the audit trail rich, 3 is
usually enough to anchor an LLM answer and saves two PG rows per schema hit.

Order is preserved as stored — C2 sorted by strength descending and the
schema property keeps that order; we want the strongest evidence first.
Archived engrams (``status='archived'``) are skipped; the resolver returns
fewer results when active engrams are scarce rather than padding with
archives.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ..consolidation.constants import RECALL_DEFAULT_EVIDENCE_N
from ..db_utils import acquire_with_retry
from ..utils import fq_table
from .hybrid_retriever import RetrievalHit

logger = logging.getLogger(__name__)


class EvidenceEngram(BaseModel):
    """Slim engram snapshot used as Schema-grounding evidence."""

    id: UUID
    text: str
    fact_type: str | None = None
    context: str | None = None
    strength: float | None = None
    tags: list[str] = Field(default_factory=list)


class EvidenceResolverError(Exception):
    """Raised when ``resolve_schema_evidence`` is called on a non-schema hit."""


async def fetch_active_engrams_by_ids(
    pool: Any,
    engram_ids: list[UUID],
    bank_id: str,
) -> dict[UUID, dict[str, Any]]:
    """Batch-fetch active engrams by id, bank-scoped, joining dictionary metadata.

    Returns a dict keyed by engram id so the caller can preserve the input
    order. Archived engrams are filtered server-side via ``ed.status``.
    """
    if not engram_ids:
        return {}
    sql = (
        f"SELECT mu.id::text AS id, mu.text, mu.fact_type, mu.context, "
        f"       ed.strength, ed.tags "
        f"FROM {fq_table('memory_units')} mu "
        f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id "
        f"WHERE mu.id = ANY($1::uuid[]) "
        f"  AND mu.bank_id = $2 "
        f"  AND ed.status = 'active'"
    )
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(sql, [str(eid) for eid in engram_ids], bank_id)
    out: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        out[UUID(r["id"])] = {
            "text": r["text"],
            "fact_type": r["fact_type"],
            "context": r["context"],
            "strength": r["strength"],
            "tags": list(r["tags"] or []),
        }
    return out


async def resolve_schema_evidence(
    hit: RetrievalHit,
    *,
    pool: Any,
    bank_id: str,
    max_n: int = RECALL_DEFAULT_EVIDENCE_N,
) -> list[EvidenceEngram]:
    """Resolve a Schema/HyperSchema hit's Top-N evidence engrams.

    Args:
        hit: ``RetrievalHit`` produced by :class:`HybridRetriever`. Must have
            ``kind="schema"``.
        pool: asyncpg connection pool.
        bank_id: target memory bank — buffer engrams are bank-scoped.
        max_n: cap on returned engrams. Defaults to
            ``RECALL_DEFAULT_EVIDENCE_N``.

    Returns:
        List of :class:`EvidenceEngram` in the order they appear in
        ``hit.evidence_engram_ids`` (i.e. by strength descending). Archived
        engrams are silently skipped — the list may be shorter than ``max_n``.

    Raises:
        EvidenceResolverError: when called on a non-schema hit. We don't want
        to hide the wiring bug; engram hits should never reach this resolver.
    """
    if hit.kind != "schema":
        raise EvidenceResolverError(f"Expected kind='schema' hit, got kind={hit.kind!r}")
    if not hit.evidence_engram_ids:
        return []
    if max_n <= 0:
        return []

    candidates = list(hit.evidence_engram_ids)
    rows = await fetch_active_engrams_by_ids(pool, candidates, bank_id)

    out: list[EvidenceEngram] = []
    for eid in candidates:
        if len(out) >= max_n:
            break
        row = rows.get(eid)
        if row is None:
            continue
        out.append(
            EvidenceEngram(
                id=eid,
                text=row["text"] or "",
                fact_type=row["fact_type"],
                context=row["context"],
                strength=row["strength"],
                tags=row["tags"],
            )
        )
    if len(out) < min(len(candidates), max_n):
        logger.debug(
            "[EvidenceResolver] Schema %s: %d/%d evidence engrams resolved (rest archived or missing)",
            hit.id,
            len(out),
            min(len(candidates), max_n),
        )
    return out


async def resolve_all_schema_evidence(
    hits: list[RetrievalHit],
    *,
    pool: Any,
    bank_id: str,
    max_n: int = RECALL_DEFAULT_EVIDENCE_N,
) -> list[tuple[RetrievalHit, list[EvidenceEngram]]]:
    """Compose helper used by the Recall path.

    Walks the HybridRetriever output and resolves Top-N evidence for every
    Schema/HyperSchema hit, leaving Engram hits paired with an empty list.
    Story 17 sits between this and the LLM step to apply mode weighting; the
    full orchestrator wiring lands with Story 18 once the legacy 4-way path
    is retired.
    """
    out: list[tuple[RetrievalHit, list[EvidenceEngram]]] = []
    for hit in hits:
        if hit.kind == "schema":
            evidence = await resolve_schema_evidence(hit, pool=pool, bank_id=bank_id, max_n=max_n)
            out.append((hit, evidence))
        else:
            out.append((hit, []))
    return out
