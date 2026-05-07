"""Incremental R4 schema-fit-check at retain time (Epic 25 Story 12).

Counterpart to the batch R4 in C2 (Story 10). When a fresh engram is
persisted, this hook asks Qdrant whether its embedding sits within
``SCHEMA_MATCH_THRESHOLD`` cosine of an existing schema centroid for the
same bank. On a hit the schema is reinforced **immediately** via
:func:`reinforce_schema_single_engram` instead of waiting for the next
C2 batch run.

Bio mapping: Tse et al. (2007) — schema-consistent memories consolidate
in hours, not weeks; the cortex doesn't make a fresh storage decision
for every new fact. Our retain hook models that fast-path. The new
engram still lands in the buffer normally; this is a **side effect**
that strengthens the matching schema.

Wiring into the retain orchestrator is intentionally deferred: the
function is callable standalone so a follow-up plumbing task can drop it
in at the right point in ``engine/retain/orchestrator.py`` without
gating Story 12 on a much larger refactor.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..consolidation.c2_schema_match import match_existing_schema
from ..consolidation.c2_schema_writer import reinforce_schema_single_engram
from ..consolidation.constants import R4_INCREMENTAL_ENABLED, SCHEMA_MATCH_THRESHOLD

if TYPE_CHECKING:
    import asyncpg

    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient
    from ..schema.models import SchemaModel

logger = logging.getLogger(__name__)


async def incremental_schema_fit(
    *,
    engram_id: str,
    embedding: list[float],
    bank_id: str,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
    schema_lookup,
    enabled: bool | None = None,
) -> "SchemaModel | None":
    """Match a freshly retained engram against bank schemas; reinforce on hit.

    Args:
        engram_id: UUID string of the just-persisted engram.
        embedding: Engram's L2-normalised embedding (already computed
            during retain).
        bank_id: Bank scope — schemas are bank-isolated (concept §15).
        neo4j / qdrant / pool: storage clients.
        schema_lookup: awaitable ``(schema_id) -> SchemaModel | None`` —
            same injection pattern as Story 06.
        enabled: optional override; defaults to module-level
            ``R4_INCREMENTAL_ENABLED``.

    Returns the reinforced schema, or ``None`` when no match is found
    (or the feature flag is off, or any failure occurs — best-effort).
    """
    if enabled is False or (enabled is None and not R4_INCREMENTAL_ENABLED):
        return None

    try:
        schema, score = await match_existing_schema(
            qdrant=qdrant,
            schema_lookup=schema_lookup,
            centroid=embedding,
            bank_id=bank_id,
        )
    except Exception:
        logger.warning(
            "incremental_schema_fit match failed bank=%s engram_id=%s — best-effort no-op",
            bank_id,
            engram_id,
        )
        return None

    if schema is None:
        logger.debug(
            "incremental_schema_fit no schema hit bank=%s engram_id=%s best_score=%.4f",
            bank_id,
            engram_id,
            score,
        )
        return None

    try:
        reinforced = await reinforce_schema_single_engram(
            schema,
            bank_id,
            engram_id=engram_id,
            embedding=embedding,
            neo4j=neo4j,
            qdrant=qdrant,
            pool=pool,
        )
    except Exception:
        logger.warning(
            "incremental_schema_fit reinforce failed bank=%s schema_id=%s engram_id=%s — leaving schema as-is",
            bank_id,
            schema.id,
            engram_id,
        )
        return None

    logger.info(
        "incremental_schema_fit reinforced bank=%s schema_id=%s engram_id=%s cosine=%.4f threshold=%.2f",
        bank_id,
        schema.id,
        engram_id,
        score,
        SCHEMA_MATCH_THRESHOLD,
    )
    return reinforced
