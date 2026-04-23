"""
Scoring functions for memory search and retrieval.

Includes recency weighting, frequency weighting, temporal proximity,
and similarity calculations used in memory activation and ranking.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hindsight_api.engine.session.mode_config import ScoringWeights


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Calculate cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score between 0 and 1
    """
    if len(vec1) != len(vec2):
        raise ValueError("Vectors must have same dimension")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = sum(a * a for a in vec1) ** 0.5
    magnitude2 = sum(b * b for b in vec2) ** 0.5

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)


def calculate_recency_weight(days_since: float, half_life_days: float = 365.0, strength: float = 0.5) -> float:
    """
    Calculate recency weight using logarithmic decay, modulated by Engram Strength.

    Concept bio-mapping: well-consolidated Engrams (high strength) decay slower —
    analogous to LTP Late stabilization in neocortex (concept.md § S4 Recency-Decay).

    `effective_half_life = base_half_life × (1 + strength)`
    - strength=0.0 → half_life unchanged (365 days)
    - strength=1.0 → half_life doubled (730 days, decays half as fast)

    Examples (with default base_half_life=365):
        - Today (0 days): 1.0 regardless of strength
        - 1 year, strength=0.0: ~0.5
        - 1 year, strength=1.0: ~0.59 (slower decay due to consolidation)

    Args:
        days_since: Number of days since the memory was created
        half_life_days: Base half-life in days (default: 1 year)
        strength: Engram consolidation level [0, 1] — modulates decay rate

    Returns:
        Weight between 0 and 1
    """
    effective_half_life = half_life_days * (1.0 + strength)
    normalized_age = days_since / effective_half_life
    return 1.0 / (1.0 + math.log1p(normalized_age))


def calculate_thalamus_weight(
    thalamus_scores: dict[str, float] | None,
    boost_dimension: str | None,
) -> float:
    """
    Compute a normalized thalamus composite weight from 4-dimensional scores.

    Bio-mapping: Thalamus Filter gates relevance via novelty (CA1 mismatch),
    surprise (noradrenaline), task_relevance (PFC attention), emotional_valence
    (amygdala). The Session Mode boosts the dimension most relevant to the
    current cognitive task (concept.md § 5, § 8).

    Args:
        thalamus_scores: Dict with keys novelty, surprise, task_relevance,
                         emotional_valence (each 0.0–1.0). None → neutral 0.5.
        boost_dimension: Dimension to boost (×1.5, capped at 1.0) per ModeConfig.
                         None → mean of all 4 dimensions.

    Returns:
        Composite weight in [0, 1].
    """
    if thalamus_scores is None:
        return 0.5
    if boost_dimension is not None:
        raw = thalamus_scores.get(boost_dimension)
        if raw is not None:
            return min(1.0, raw * 1.5)
    keys = ("novelty", "surprise", "task_relevance", "emotional_valence")
    vals = [thalamus_scores.get(k) or 0.0 for k in keys]
    return sum(vals) / len(keys)


def calculate_strength_weight(strength: float) -> float:
    """
    Map Engram Strength [0, 1] to a normalized scoring weight via logarithmic dampening.

    Bio-mapping: Dopamine-gated LTP — stronger engrams have higher activation
    probability, but logarithmic scaling prevents very strong engrams from
    dominating retrieval entirely (concept.md § 8 Extended Scoring).

    strength=0.0 → 0.0, strength=0.5 → ~0.585, strength=1.0 → 1.0

    Args:
        strength: Engram consolidation level [0, 1]

    Returns:
        Normalized weight in [0, 1]
    """
    return math.log1p(strength) / math.log1p(1.0)


def calculate_combined_score(
    ce: float,
    rrf: float,
    temporal: float,
    recency: float,
    strength_weight: float,
    thalamus_weight: float,
    weights: ScoringWeights,
    tag_overlap: float = 0.0,
) -> float:
    """
    Compute the extended 7-term scoring formula with mode-specific weights.

    Formula:
        w1×CE + w2×RRF + w3×Temporal + w4×Recency + w5×Strength
        + w6×Thalamus + w7×TagOverlap

    Weights sum to 1.0 (enforced by ``ScoringWeights.__post_init__``).

    Bio-mapping: PFC top-down attention (Session Mode) sets the weight profile —
    Precision boosts CE and Tag-Overlap, Exploration boosts Thalamus
    (concept.md § 8, docs/engram/11_retrieval_architecture.md §3.2).

    Args:
        ce: Cross-encoder normalized score [0, 1]
        rrf: RRF score normalized [0, 1]
        temporal: Temporal proximity score [0, 1]
        recency: Recency weight (strength-modulated) [0, 1]
        strength_weight: Logarithmically dampened Engram Strength [0, 1]
        thalamus_weight: Thalamus composite (possibly boosted) [0, 1]
        weights: Mode-specific ScoringWeights (7 floats summing to 1.0)
        tag_overlap: Jaccard overlap between query tags and engram tags
            [0, 1]. Defaults to 0.0 so legacy call sites that do not yet
            compute tag overlap continue to produce valid scores
            (contribution collapses to zero regardless of the weight).

    Returns:
        Combined score in [0, 1]
    """
    return (
        weights.ce * ce
        + weights.rrf * rrf
        + weights.temporal * temporal
        + weights.recency * recency
        + weights.engram_strength * strength_weight
        + weights.thalamus_weighted * thalamus_weight
        + weights.tag_overlap * tag_overlap
    )


def calculate_frequency_weight(access_count: int, max_boost: float = 2.0) -> float:
    """
    Calculate frequency weight based on access count.

    Frequently accessed memories are weighted higher.
    Uses logarithmic scaling to avoid over-weighting.

    Args:
        access_count: Number of times the memory was accessed
        max_boost: Maximum multiplier for frequently accessed memories

    Returns:
        Weight between 1.0 and max_boost
    """
    import math

    if access_count <= 0:
        return 1.0

    # Logarithmic scaling: log(access_count + 1) / log(10)
    # This gives: 0 accesses = 1.0, 9 accesses ~= 1.5, 99 accesses ~= 2.0
    normalized = math.log(access_count + 1) / math.log(10)
    return 1.0 + min(normalized, max_boost - 1.0)


def calculate_temporal_anchor(occurred_start: datetime, occurred_end: datetime) -> datetime:
    """
    Calculate a single temporal anchor point from a temporal range.

    Used for spreading activation - we need a single representative date
    to calculate temporal proximity between facts. This simplifies the
    range-to-range distance problem.

    Strategy: Use midpoint of the range for balanced representation.

    Args:
        occurred_start: Start of temporal range
        occurred_end: End of temporal range

    Returns:
        Single datetime representing the temporal anchor (midpoint)

    Examples:
        - Point event (July 14): start=July 14, end=July 14 → anchor=July 14
        - Month range (February): start=Feb 1, end=Feb 28 → anchor=Feb 14
        - Year range (2023): start=Jan 1, end=Dec 31 → anchor=July 1
    """
    # Calculate midpoint
    time_delta = occurred_end - occurred_start
    midpoint = occurred_start + (time_delta / 2)
    return midpoint


def calculate_temporal_proximity(anchor_a: datetime, anchor_b: datetime, half_life_days: float = 30.0) -> float:
    """
    Calculate temporal proximity between two temporal anchors.

    Used for spreading activation to determine how "close" two facts are
    in time. Uses logarithmic decay so that temporal similarity doesn't
    drop off too quickly.

    Args:
        anchor_a: Temporal anchor of first fact
        anchor_b: Temporal anchor of second fact
        half_life_days: Number of days for proximity to reach 0.5
                       (default: 30 days = 1 month)

    Returns:
        Proximity score in [0, 1] where:
        - 1.0 = same day
        - 0.5 = ~half_life days apart
        - 0.0 = very distant in time

    Examples:
        - Same day: 1.0
        - 1 week apart (half_life=30): ~0.7
        - 1 month apart (half_life=30): ~0.5
        - 1 year apart (half_life=30): ~0.2
    """
    import math

    days_apart = abs((anchor_a - anchor_b).days)

    if days_apart == 0:
        return 1.0

    # Logarithmic decay: 1 / (1 + log(1 + days_apart/half_life))
    # Similar to calculate_recency_weight but for proximity between events
    normalized_distance = days_apart / half_life_days
    proximity = 1.0 / (1.0 + math.log1p(normalized_distance))

    return proximity
