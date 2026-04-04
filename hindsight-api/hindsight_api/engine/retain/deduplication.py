"""
Deduplication logic for retain pipeline.

Checks for duplicate facts using semantic similarity and temporal proximity.
Score-aware resolution: the fact with the higher Thalamus overall score wins.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from enum import Enum

from .types import ProcessedFact

logger = logging.getLogger(__name__)


class DuplicateResolution(Enum):
    KEEP = "keep"  # Genuinely new fact (not a duplicate)
    DROP = "drop"  # Duplicate — existing Engram wins
    REPLACE = "replace"  # Duplicate — new fact wins, update existing


@dataclass
class DuplicateResult:
    """Rich result from duplicate_checker_fn — replaces bare bool."""

    is_duplicate: bool
    existing_unit_id: str | None = None
    existing_score: float | None = None  # thalamus_overall from engram_dictionary
    existing_strength: float | None = None  # strength from engram_dictionary
    similarity: float = 0.0


@dataclass
class ReplacementAction:
    """Describes an existing Engram that should be overwritten by a new fact."""

    existing_unit_id: str
    new_fact: ProcessedFact


async def check_duplicates_batch(
    conn, bank_id: str, facts: list[ProcessedFact], duplicate_checker_fn
) -> list[DuplicateResult]:
    """
    Check which facts are duplicates using batched time-window queries.

    Groups facts by 12-hour time buckets to efficiently check for duplicates
    within a 24-hour window.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        facts: List of ProcessedFact objects to check
        duplicate_checker_fn: Async function(conn, bank_id, texts, embeddings, date, time_window_hours)
                              that returns list[DuplicateResult]

    Returns:
        List of DuplicateResult objects (same length as facts)
    """
    if not facts:
        return []

    # Group facts by event_date (rounded to 12-hour buckets) for efficient batching
    time_buckets = defaultdict(list)
    for idx, fact in enumerate(facts):
        fact_date = fact.occurred_start if fact.occurred_start is not None else fact.mentioned_at

        if fact_date is None:
            from datetime import datetime

            fact_date = datetime.now(UTC)

        bucket_key = fact_date.replace(hour=(fact_date.hour // 12) * 12, minute=0, second=0, microsecond=0)
        time_buckets[bucket_key].append((idx, fact))

    all_results: list[DuplicateResult] = [DuplicateResult(is_duplicate=False)] * len(facts)

    for bucket_date, bucket_items in time_buckets.items():
        indices = [item[0] for item in bucket_items]
        texts = [item[1].fact_text for item in bucket_items]
        embeddings = [item[1].embedding for item in bucket_items]

        dup_results = await duplicate_checker_fn(conn, bank_id, texts, embeddings, bucket_date, time_window_hours=24)

        for idx, dup_result in zip(indices, dup_results):
            all_results[idx] = dup_result

    return all_results


# Strength bonus multiplier for existing Engrams during deduplication.
# A consolidated Engram (strength=1.0) gets +0.1 added to its effective score,
# making it harder to replace. This models the biological principle that
# well-consolidated memories (LTP Late) are more resistant to overwriting.
# Bio mapping: reconsolidation resistance ∝ consolidation state.
STRENGTH_BONUS_MULTIPLIER: float = 0.1


def resolve_duplicate(new_fact: ProcessedFact, dup_result: DuplicateResult) -> DuplicateResolution:
    """Determine whether a duplicate fact should be kept, dropped, or replace the existing one.

    Score comparison: new_score >= effective_existing → REPLACE (frischer gewinnt bei Gleichstand).
    effective_existing = thalamus_overall + strength * STRENGTH_BONUS_MULTIPLIER (protects consolidated Engrams).

    Args:
        new_fact: The incoming fact candidate.
        dup_result: Rich duplicate check result including existing scores.

    Returns:
        DuplicateResolution enum value.
    """
    if not dup_result.is_duplicate:
        return DuplicateResolution.KEEP

    new_score = new_fact.thalamus_scores.overall if new_fact.thalamus_scores else 0.0
    strength_bonus = (dup_result.existing_strength or 0.0) * STRENGTH_BONUS_MULTIPLIER
    effective_existing = (dup_result.existing_score or 0.0) + strength_bonus

    if new_score >= effective_existing:
        logger.info(f"Thalamus dedup: replacing existing (score={effective_existing:.3f} < {new_score:.3f})")
        return DuplicateResolution.REPLACE

    logger.info(f"Thalamus dedup: kept existing (score={effective_existing:.3f} >= {new_score:.3f})")
    return DuplicateResolution.DROP


def resolve_duplicates_batch(
    facts: list[ProcessedFact],
    dup_results: list[DuplicateResult],
) -> tuple[list[ProcessedFact], list[ReplacementAction]]:
    """Apply score-aware resolution to a batch of duplicate check results.

    Args:
        facts: ProcessedFact objects (same order as dup_results).
        dup_results: One DuplicateResult per fact.

    Returns:
        Tuple of (facts_to_store, replacement_actions).
        facts_to_store contains KEEP facts (new inserts) + REPLACE facts (updates).
        replacement_actions describes which existing Engrams should be overwritten.
    """
    facts_to_store: list[ProcessedFact] = []
    replacement_actions: list[ReplacementAction] = []

    for fact, dr in zip(facts, dup_results):
        resolution = resolve_duplicate(fact, dr)
        if resolution == DuplicateResolution.KEEP:
            facts_to_store.append(fact)
        elif resolution == DuplicateResolution.REPLACE:
            facts_to_store.append(fact)
            replacement_actions.append(ReplacementAction(dr.existing_unit_id, fact))
        # DROP → discard silently

    return facts_to_store, replacement_actions


def filter_duplicates(facts: list[ProcessedFact], is_duplicate_flags: list[bool]) -> list[ProcessedFact]:
    """
    Filter out duplicate facts based on duplicate flags.

    Kept for backward compatibility. New callers should use resolve_duplicates_batch.

    Args:
        facts: List of ProcessedFact objects
        is_duplicate_flags: Boolean flags indicating which facts are duplicates

    Returns:
        List of non-duplicate facts
    """
    if len(facts) != len(is_duplicate_flags):
        raise ValueError(f"Mismatch between facts ({len(facts)}) and flags ({len(is_duplicate_flags)})")

    return [fact for fact, is_dup in zip(facts, is_duplicate_flags) if not is_dup]
