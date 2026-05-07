"""PostgreSQL repository for C2 cluster fingerprints (Epic 25 Story 05, R2).

Persistence layer for the maturation step of Game-of-Life schema emergence
(concept §13 R2): a candidate cluster found by R1 (Story 04) is matched
against fingerprints from prior C2 runs. A match (cosine ≥ 0.85) increments
the survival counter; otherwise a fresh row is created with
``cycles_survived = 1``. Schemas are only born once a fingerprint reaches
``cycles_survived >= 2`` — one-shot clusters die quietly.

Why PostgreSQL: the fingerprint store is a transient hippocampal working
state, not a cortical schema. pgvector's ``<=>`` operator gives us cheap
cosine distance against the bank's existing fingerprints without a Qdrant
round-trip — the same database that already holds the engram dictionary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from ..db_utils import acquire_with_retry

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# concept §13: same cosine threshold the R4 incremental schema-fit-check uses.
# Tuning either site means tuning both — keep the constant authoritative here.
MATCH_COSINE_THRESHOLD: float = 0.85
DEFAULT_STALE_MAX_AGE_DAYS: int = 7


@dataclass(frozen=True)
class FingerprintMatch:
    """Outcome of ``match_or_create`` — what happened plus the row's identity."""

    fingerprint_id: UUID
    cycles_survived: int
    matched_existing: bool
    cosine: float | None


def _format_vector_literal(vec: list[float]) -> str:
    """pgvector accepts ``'[v1,v2,...]'::vector`` as a textual literal.

    asyncpg has no built-in pgvector codec, so we serialise floats ourselves.
    Using a JSON-style array is cheap and matches what the pgvector docs show.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def match_or_create(
    pool: "asyncpg.Pool",
    bank_id: str,
    centroid: list[float],
    dominant_tags: list[str],
    *,
    threshold: float = MATCH_COSINE_THRESHOLD,
) -> FingerprintMatch:
    """Match ``centroid`` against existing bank fingerprints or insert a fresh row.

    Returns the row's id, its updated ``cycles_survived``, and whether the
    write was a match (``True``) or a brand-new fingerprint (``False``).
    """
    centroid_literal = _format_vector_literal(centroid)
    tags_json = json.dumps(list(dominant_tags))
    distance_cutoff = 1.0 - threshold

    select_query = """
        SELECT id, cycles_survived, 1 - (centroid <=> $2::vector) AS cosine
        FROM c2_cluster_fingerprints
        WHERE bank_id = $1
        ORDER BY centroid <=> $2::vector ASC
        LIMIT 1
    """
    update_query = """
        UPDATE c2_cluster_fingerprints
        SET cycles_survived = cycles_survived + 1,
            last_seen_at = now()
        WHERE id = $1
        RETURNING id, cycles_survived
    """
    insert_query = """
        INSERT INTO c2_cluster_fingerprints (bank_id, centroid, dominant_tags, cycles_survived)
        VALUES ($1, $2::vector, $3::jsonb, 1)
        RETURNING id, cycles_survived
    """

    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(select_query, bank_id, centroid_literal)
        if row is not None and float(row["cosine"]) >= threshold and (1.0 - float(row["cosine"])) <= distance_cutoff:
            updated = await conn.fetchrow(update_query, row["id"])
            return FingerprintMatch(
                fingerprint_id=updated["id"],
                cycles_survived=updated["cycles_survived"],
                matched_existing=True,
                cosine=float(row["cosine"]),
            )
        inserted = await conn.fetchrow(insert_query, bank_id, centroid_literal, tags_json)
        return FingerprintMatch(
            fingerprint_id=inserted["id"],
            cycles_survived=inserted["cycles_survived"],
            matched_existing=False,
            cosine=float(row["cosine"]) if row is not None else None,
        )


async def prune_stale(
    pool: "asyncpg.Pool",
    bank_id: str,
    *,
    max_age_days: int = DEFAULT_STALE_MAX_AGE_DAYS,
) -> int:
    """Delete fingerprints not seen for ``max_age_days`` (concept §13 R5 pendant).

    Returns the count of pruned rows; logs a single info line per call.
    """
    query = """
        DELETE FROM c2_cluster_fingerprints
        WHERE bank_id = $1
          AND last_seen_at < now() - ($2::int * interval '1 day')
    """
    async with acquire_with_retry(pool) as conn:
        result = await conn.execute(query, bank_id, max_age_days)
    deleted = int(result.split()[-1]) if result.startswith("DELETE") else 0
    if deleted:
        logger.info("c2_cluster_fingerprints pruned %d stale rows for bank=%s", deleted, bank_id)
    return deleted
