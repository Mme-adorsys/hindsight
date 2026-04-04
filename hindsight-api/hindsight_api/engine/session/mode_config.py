"""
Mode Configuration Profiles — static parameter sets per RetrievalMode.

Each mode (Precision / Exploration / Analogy / Validation) carries a full
ModeConfig that flows through the entire pipeline: retain thresholds,
retrieval patterns, traversal depth, construction style, reconsolidation
aggressiveness, and scoring weights.

Bio mapping: PFC top-down attention modulating hippocampal strategy selection —
different cognitive demands require different memory access patterns.

Concept reference: docs/engram/concept.md — Chapter 7 (Session Layer) + Chapter 8 (Scoring)
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass

from ..response_models import RetrievalMode

# ---------------------------------------------------------------------------
# Env-var names for strength_pre_filter overrides (T4)
# ---------------------------------------------------------------------------
ENV_MODE_PRECISION_STRENGTH = "ENGRAM_PRECISION_STRENGTH_THRESHOLD"
ENV_MODE_EXPLORATION_STRENGTH = "ENGRAM_EXPLORATION_STRENGTH_THRESHOLD"
ENV_MODE_ANALOGY_STRENGTH = "ENGRAM_ANALOGY_STRENGTH_THRESHOLD"
ENV_MODE_VALIDATION_STRENGTH = "ENGRAM_VALIDATION_STRENGTH_THRESHOLD"


# ---------------------------------------------------------------------------
# ScoringWeights — T2
# Weights for the extended scoring formula (concept.md § 8):
#   w1×CE + w2×RRF + w3×Temporal + w4×Recency + w5×Engram_Strength + w6×Thalamus_Weighted
# Each row sums to 1.0.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoringWeights:
    """
    Mode-specific weights for the Engram scoring formula.

    All 6 weights should sum to 1.0 for a given mode.
    Frozen to ensure profiles remain immutable after construction.
    """

    ce: float
    """Cross-Encoder reranker score weight."""

    rrf: float
    """Reciprocal Rank Fusion weight."""

    temporal: float
    """Temporal proximity match weight."""

    recency: float
    """Recency decay weight (modulated by Engram Strength)."""

    engram_strength: float
    """Engram Strength (consolidation level) weight."""

    thalamus_weighted: float
    """Thalamus composite score weight (novelty/surprise/relevance blend)."""


# ---------------------------------------------------------------------------
# ModeConfig — T1
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModeConfig:
    """
    Full configuration profile for a single RetrievalMode.

    Immutable — use with_overrides() to produce a modified copy.
    All downstream systems (Retain, Search, Construction, Reconsolidation)
    read their parameters from this object.
    """

    strength_pre_filter: float
    """Minimum Engram Strength required to pass the pre-filter stage."""

    thalamus_boost_dimension: str | None
    """Thalamus dimension to boost during scoring (novelty/surprise/task_relevance), or None."""

    weak_link_policy: Literal["ignore", "follow", "prefer"]
    """How to handle weak (co_activated / temporal_proximity) Neo4j links during traversal."""

    traversal_depth: Literal["shallow", "medium", "deep"]
    """Graph traversal depth in Neo4j (shallow=1 hop, medium=2, deep=3+)."""

    construction_style: Literal["conservative", "creative", "cross_domain", "evidence_based"]
    """Answer construction strategy during Constructive Memory (Epic 11)."""

    reconsolidation_level: Literal["minimal", "moderate", "schema_update", "aggressive"]
    """How aggressively the NCR reconsolidation phase updates Engrams (Epic 10)."""

    scoring_weights: ScoringWeights
    """Mode-specific weights for the 6-term scoring formula."""

    def with_overrides(self, **kwargs: object) -> "ModeConfig":
        """
        Return a new ModeConfig with the specified fields replaced.

        The original instance is not mutated (frozen dataclass).

        Example:
            custom = MODE_PROFILES[RetrievalMode.PRECISION].with_overrides(strength_pre_filter=0.6)
        """
        return dataclasses.replace(self, **kwargs)


# ---------------------------------------------------------------------------
# Default ScoringWeights per mode (T2)
# Precision:   high CE weight — stay focused on the query
# Exploration: lower CE, higher Thalamus — surface novel / unexpected items
# Analogy:     balanced RRF + Thalamus — favour structural similarity
# Validation:  moderate CE + high Thalamus — surface surprises / contradictions
# ---------------------------------------------------------------------------
_WEIGHTS_PRECISION = ScoringWeights(
    ce=0.60,
    rrf=0.15,
    temporal=0.05,
    recency=0.05,
    engram_strength=0.05,
    thalamus_weighted=0.10,
)

_WEIGHTS_EXPLORATION = ScoringWeights(
    ce=0.20,
    rrf=0.15,
    temporal=0.10,
    recency=0.10,
    engram_strength=0.15,
    thalamus_weighted=0.30,
)

_WEIGHTS_ANALOGY = ScoringWeights(
    ce=0.30,
    rrf=0.25,
    temporal=0.05,
    recency=0.05,
    engram_strength=0.10,
    thalamus_weighted=0.25,
)

_WEIGHTS_VALIDATION = ScoringWeights(
    ce=0.35,
    rrf=0.10,
    temporal=0.10,
    recency=0.05,
    engram_strength=0.10,
    thalamus_weighted=0.30,
)


# ---------------------------------------------------------------------------
# MODE_PROFILES Registry — T3
# Default values from concept.md § 7 table.
# ---------------------------------------------------------------------------
MODE_PROFILES: dict[RetrievalMode, ModeConfig] = {
    RetrievalMode.PRECISION: ModeConfig(
        strength_pre_filter=0.5,
        thalamus_boost_dimension="task_relevance",
        weak_link_policy="ignore",
        traversal_depth="shallow",
        construction_style="conservative",
        reconsolidation_level="minimal",
        scoring_weights=_WEIGHTS_PRECISION,
    ),
    RetrievalMode.EXPLORATION: ModeConfig(
        strength_pre_filter=0.1,
        thalamus_boost_dimension="novelty",
        weak_link_policy="follow",
        traversal_depth="deep",
        construction_style="creative",
        reconsolidation_level="moderate",
        scoring_weights=_WEIGHTS_EXPLORATION,
    ),
    RetrievalMode.ANALOGY: ModeConfig(
        strength_pre_filter=0.3,
        thalamus_boost_dimension=None,
        weak_link_policy="prefer",
        traversal_depth="medium",
        construction_style="cross_domain",
        reconsolidation_level="schema_update",
        scoring_weights=_WEIGHTS_ANALOGY,
    ),
    RetrievalMode.VALIDATION: ModeConfig(
        strength_pre_filter=0.3,
        thalamus_boost_dimension="surprise",
        weak_link_policy="ignore",
        traversal_depth="medium",
        construction_style="evidence_based",
        reconsolidation_level="aggressive",
        scoring_weights=_WEIGHTS_VALIDATION,
    ),
}


# ---------------------------------------------------------------------------
# get_mode_config — T5
# Resolves overrides from environment variables and applies them on top of
# the default profile.  Consistent with thalamus.py override pattern.
# ---------------------------------------------------------------------------
_STRENGTH_ENV_MAP: dict[RetrievalMode, str] = {
    RetrievalMode.PRECISION: ENV_MODE_PRECISION_STRENGTH,
    RetrievalMode.EXPLORATION: ENV_MODE_EXPLORATION_STRENGTH,
    RetrievalMode.ANALOGY: ENV_MODE_ANALOGY_STRENGTH,
    RetrievalMode.VALIDATION: ENV_MODE_VALIDATION_STRENGTH,
}


def get_mode_config(mode: RetrievalMode) -> ModeConfig:
    """
    Return the ModeConfig for the given mode, applying any Env-Var overrides.

    Override check order:
      1. Environment variable (e.g. ENGRAM_PRECISION_STRENGTH_THRESHOLD)
      2. Default profile from MODE_PROFILES

    Only strength_pre_filter is currently overridable via Env-Var.
    Additional overrides can be added here as the system matures.

    Args:
        mode: The RetrievalMode to resolve config for.

    Returns:
        ModeConfig instance (possibly with overridden strength_pre_filter).
    """
    base = MODE_PROFILES[mode]
    env_key = _STRENGTH_ENV_MAP[mode]
    raw = os.getenv(env_key)
    if raw is not None:
        return base.with_overrides(strength_pre_filter=float(raw))
    return base
