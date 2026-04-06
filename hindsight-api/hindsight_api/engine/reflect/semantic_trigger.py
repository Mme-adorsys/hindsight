"""
Semantic Trigger — Qdrant-based reconsolidation candidate discovery (RF2 + RF3).

Replaces the 12-query SQL approach (4 methods × 3 fact_types) with a single
Qdrant similarity search. Any Engram with cosine similarity ≥ 0.6 to the
current query is a reconsolidation candidate.

Entity-match remains as a secondary trigger (fallback for Engrams not yet in
Qdrant or with low embedding quality). Results are merged and deduplicated;
Qdrant hits receive a score bonus to reflect richer contextual relevance.

Bio mapping:
- Qdrant similarity search    → Hippocampal pattern completion — "similar enough"
                                 activations trigger the reconsolidation window
- Cosine ≥ 0.6 threshold      → Partial-cue threshold for memory reactivation
                                 (below this: insufficient overlap to open lability window)
- Entity-match fallback        → Direct associative recall via named-entity binding
- Qdrant score bonus           → Semantic resonance → higher plasticity weight
- Deduplicated merge           → Single lability window per Engram regardless of
                                 how many triggers fired

Concept reference: docs/engram/concept.md — Chapter 10 (RF2 Retrieval-Cost, RF3 Semantic Trigger)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncpg

    from ..qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# Cosine similarity threshold for Qdrant-triggered reconsolidation
DEFAULT_SIMILARITY_THRESHOLD: float = 0.6

# Maximum candidates fetched from Qdrant per reconsolidation cycle
DEFAULT_QDRANT_LIMIT: int = 50

# Score bonus added to Qdrant-sourced candidates before priority queue
QDRANT_SCORE_BONUS: float = 0.1


@dataclass
class CandidateEngram:
    """
    A single reconsolidation candidate — an Engram selected as potentially
    relevant to the current query context.

    The score_bonus is added to the Engram's strength when determining
    priority queue order, giving semantically matched Engrams slightly
    higher reconsolidation priority.
    """

    engram_id: str
    similarity_score: float
    source: Literal["qdrant", "entity_match"]
    score_bonus: float = 0.0


async def find_reconsolidation_candidates(
    qdrant_client: QdrantMemoryClient,
    query_embedding: list[float],
    bank_id: str,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    limit: int = DEFAULT_QDRANT_LIMIT,
) -> list[CandidateEngram]:
    """
    Find Engrams semantically similar to the query embedding via Qdrant.

    Performs a single vector search and filters to similarity ≥ threshold.
    This replaces the 12-query SQL approach (RF2: retrieval cost optimisation).

    Args:
        qdrant_client:   Initialised QdrantMemoryClient (from Epic 01).
        query_embedding: Query vector (384-dim).
        bank_id:         Bank scope — only Engrams from this bank are returned.
        threshold:       Minimum cosine similarity (default: 0.6).
        limit:           Maximum number of Qdrant results to fetch before filtering.

    Returns:
        List of CandidateEngram with source="qdrant" and score_bonus=QDRANT_SCORE_BONUS.
        Sorted by similarity_score descending.
    """
    try:
        bank_filter = {"must": [{"key": "bank_id", "match": {"value": bank_id}}]}
        results = await qdrant_client.search_similar(
            embedding=query_embedding,
            limit=limit,
            filters=bank_filter,
        )
    except Exception as e:
        logger.warning("[SEMANTIC_TRIGGER] Qdrant search failed: %s", e)
        return []

    candidates = [
        CandidateEngram(
            engram_id=r["engram_id"],
            similarity_score=r["score"],
            source="qdrant",
            score_bonus=QDRANT_SCORE_BONUS,
        )
        for r in results
        if r["score"] >= threshold
    ]

    logger.debug(
        "[SEMANTIC_TRIGGER] Qdrant: fetched=%d above_threshold=%d (threshold=%.2f)",
        len(results),
        len(candidates),
        threshold,
    )
    return sorted(candidates, key=lambda c: c.similarity_score, reverse=True)


async def find_entity_match_candidates(
    pool: asyncpg.Pool,
    bank_id: str,
    entity_names: list[str],
    fq_table_fn,  # callable: (table_name) -> fully-qualified table name
) -> list[CandidateEngram]:
    """
    Find Engrams linked to the given entity names via SQL join (fallback trigger).

    This is the secondary trigger: used when Qdrant returns no candidates or
    when an Engram has no embedding yet (entity-binding is always available).

    Args:
        pool:          asyncpg connection pool.
        bank_id:       Bank scope.
        entity_names:  List of canonical entity names from newly retained content.
        fq_table_fn:   Function to produce fully-qualified table names (handles tenancy).

    Returns:
        List of CandidateEngram with source="entity_match" and score_bonus=0.0.
    """
    if not entity_names:
        return []

    from ..db_utils import acquire_with_retry

    try:
        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT DISTINCT mu.id::text AS engram_id
                FROM {fq_table_fn("memory_units")} mu
                JOIN {fq_table_fn("unit_entities")} ue ON mu.id = ue.unit_id
                JOIN {fq_table_fn("entities")} e ON ue.entity_id = e.id
                WHERE mu.bank_id = $1
                  AND e.canonical_name = ANY($2::text[])
                  AND mu.status != 'archived'
                """,
                bank_id,
                entity_names,
            )
    except Exception as e:
        logger.warning("[SEMANTIC_TRIGGER] Entity-match query failed: %s", e)
        return []

    candidates = [
        CandidateEngram(
            engram_id=row["engram_id"],
            similarity_score=0.0,  # No similarity score for entity matches
            source="entity_match",
            score_bonus=0.0,
        )
        for row in rows
    ]
    logger.debug(
        "[SEMANTIC_TRIGGER] Entity-match: found=%d for %d entities",
        len(candidates),
        len(entity_names),
    )
    return candidates


def merge_candidates(
    qdrant_candidates: list[CandidateEngram],
    entity_candidates: list[CandidateEngram],
) -> list[CandidateEngram]:
    """
    Merge Qdrant and entity-match candidates, deduplicating by engram_id.

    When an Engram appears in both sources, the Qdrant entry wins (higher
    score + bonus). Entity-only candidates are appended after Qdrant hits.

    Args:
        qdrant_candidates:  Results from find_reconsolidation_candidates().
        entity_candidates:  Results from find_entity_match_candidates().

    Returns:
        Deduplicated list — Qdrant entries first, entity-only entries appended.
    """
    seen: set[str] = set()
    merged: list[CandidateEngram] = []

    for c in qdrant_candidates:
        if c.engram_id not in seen:
            seen.add(c.engram_id)
            merged.append(c)

    for c in entity_candidates:
        if c.engram_id not in seen:
            seen.add(c.engram_id)
            merged.append(c)

    logger.debug(
        "[SEMANTIC_TRIGGER] Merged: qdrant=%d entity=%d total_unique=%d",
        len(qdrant_candidates),
        len(entity_candidates),
        len(merged),
    )
    return merged
