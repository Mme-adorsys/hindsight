"""C2 Schema-Fingerprint-Match (Epic 25 Story 06).

Before C2 conjures a brand-new schema for a matured cluster, this module
asks: does a semantically equivalent schema already exist for this bank?
A Qdrant vector search restricted to ``kind="schema"`` and the cluster's
bank yields the closest schema centroid; if cosine ≥ ``SCHEMA_MATCH_THRESHOLD``
(concept §13 R4) the cluster routes into the reinforcement pipeline
instead of duplicating the schema.

Bio mapping: schema-consistent consolidation (Tse et al. 2007) — new
information that fits the cortical landscape strengthens the existing
schema rather than building a parallel one. Without this gate, repeated
similar clusters would inflate the schema graph and dilute the reinforcement
signal (concept §13 R4 + R5 work in tandem to keep the cortex tidy).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qdrant_client.http import models as qdrant_models

from .constants import SCHEMA_MATCH_THRESHOLD

if TYPE_CHECKING:
    from ..qdrant_client import QdrantEngineClient
    from ..schema.models import SchemaModel
    from ..schema.schema_repository import _SchemaBase

logger = logging.getLogger(__name__)


async def match_existing_schema(
    qdrant: "QdrantEngineClient",
    schema_lookup,
    centroid: list[float],
    bank_id: str,
    *,
    threshold: float = SCHEMA_MATCH_THRESHOLD,
) -> tuple["SchemaModel | None", float]:
    """Find the closest existing :Schema for ``centroid`` within ``bank_id``.

    Args:
        qdrant: connected QdrantEngineClient.
        schema_lookup: awaitable ``(schema_id) -> SchemaModel | None``. The
            caller injects this so we don't pull a Neo4j client (and its
            dependencies) into this module — typical wire-up is
            ``functools.partial(schema_repository.get_schema, neo4j_client)``.
        centroid: cluster centroid (already L2-normalised — see
            ``engine.schema.compute_centroid``).
        bank_id: Schema candidates are bank-scoped; cross-bank matches would
            violate the multi-bank isolation contract from concept §15.
        threshold: cosine cutoff. Default ``SCHEMA_MATCH_THRESHOLD`` (0.85).

    Returns:
        ``(SchemaModel, cosine_score)`` if the nearest schema clears the
        threshold; otherwise ``(None, best_score)`` (or ``(None, 0.0)`` when
        the bank has no schemas at all). The best score is exposed even on a
        miss so callers can log diagnostics.
    """
    bank_filter = {
        "must": [
            qdrant_models.FieldCondition(
                key="bank_id",
                match=qdrant_models.MatchValue(value=bank_id),
            )
        ]
    }
    hits = await qdrant.search_similar(
        embedding=centroid,
        limit=1,
        filters=bank_filter,
        kind="schema",
    )
    if not hits:
        logger.debug("c2_schema_match no schema candidates in bank=%s", bank_id)
        return None, 0.0

    top = hits[0]
    score = float(top.get("score") or 0.0)
    schema_id = top.get("payload", {}).get("schema_id") or top.get("engram_id")

    if score < threshold:
        logger.debug(
            "c2_schema_match bank=%s best_score=%.4f below threshold=%.2f — creation path",
            bank_id,
            score,
            threshold,
        )
        return None, score

    if schema_id is None:
        # Defensive: a kind=schema point without schema_id is a write-side bug
        # (see qdrant_client.upsert_schema_centroid which always sets it).
        logger.warning("c2_schema_match payload missing schema_id for bank=%s — treating as miss", bank_id)
        return None, score

    schema = await schema_lookup(schema_id)
    if schema is None:
        # Cross-DB drift: Qdrant point exists but the Neo4j node was
        # deleted/archived. Treat as miss; cleanup happens elsewhere.
        logger.warning(
            "c2_schema_match qdrant→neo4j drift: schema_id=%s missing in cortex for bank=%s",
            schema_id,
            bank_id,
        )
        return None, score

    return _coerce_to_schema(schema), score


def _coerce_to_schema(schema: "_SchemaBase") -> "SchemaModel":
    """Help the type-checker: ``schema_repository.get_schema`` returns the
    base class (``SchemaModel | HyperSchemaModel``). Schema-match only ever
    targets ``:Schema`` nodes; if a HyperSchema slipped in we still pass it
    through — the caller is responsible for the label semantics.
    """
    return schema  # type: ignore[return-value]
