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

# Kill switch for Context-Tag-Overlap scoring. When false the retrieval
# pipeline passes tag_overlap=0.0 into calculate_combined_score, which
# makes the 7th term vanish without having to rebalance the profiles
# back to 6 terms — rollback is a one-env-var flip.
ENV_TAG_OVERLAP_ENABLED = "HINDSIGHT_API_TAG_OVERLAP_ENABLED"


def tag_overlap_enabled() -> bool:
    """Read the Context-Tag-Overlap kill switch from the environment."""
    raw = os.getenv(ENV_TAG_OVERLAP_ENABLED, "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# ScoringWeights — T2
# Weights for the extended scoring formula (concept.md § 8):
#   w1×CE + w2×RRF + w3×Temporal + w4×Recency + w5×Engram_Strength
# + w6×Thalamus_Weighted + w7×Tag_Overlap
# Each row sums to 1.0.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoringWeights:
    """
    Mode-specific weights for the Engram scoring formula.

    All 7 weights should sum to 1.0 for a given mode.
    Frozen to ensure profiles remain immutable after construction.

    ``tag_overlap`` defaults to 0.0 so older call sites that pass only the
    six original weights continue to construct a valid, tag-unaware
    profile — useful for tests and transitional code.
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

    tag_overlap: float = 0.0
    """Context-Tag-Overlap (Jaccard) between query tokens and engram tags."""

    def __post_init__(self) -> None:
        total = (
            self.ce
            + self.rrf
            + self.temporal
            + self.recency
            + self.engram_strength
            + self.thalamus_weighted
            + self.tag_overlap
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ScoringWeights must sum to 1.0, got {total:.6f} "
                f"(ce={self.ce}, rrf={self.rrf}, temporal={self.temporal}, "
                f"recency={self.recency}, engram_strength={self.engram_strength}, "
                f"thalamus_weighted={self.thalamus_weighted}, "
                f"tag_overlap={self.tag_overlap})"
            )


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

    # ------------------------------------------------------------------
    # Hybrid Retriever — Schema vs Engram re-weighting (Epic 25 Story 17)
    # ------------------------------------------------------------------
    # Multiplied onto the raw Qdrant cosine score per RetrievalHit kind,
    # then the hit list is re-sorted by the weighted score. Defaults are
    # bias multipliers, not probabilities — they don't have to sum to a
    # constant. Concept §7 (Mode → cognitive strategy): Precision prefers
    # the cortex generalisation (Schema-Allgemeinheit), Exploration
    # prefers the buffer episodes (Engram-Spezifik).
    w_schema: float = 1.0
    w_engram: float = 1.0

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
# Tag-overlap weight per mode (higher for Precision/Validation where exact
# tag matches are the strongest signal). The budget for the new 7th term
# was taken proportionally from CE and RRF so no other dimension shifts
# in relative importance — initial values are meant to be tuned
# empirically once benchmarks surface regressions.
_WEIGHTS_PRECISION = ScoringWeights(
    ce=0.48,
    rrf=0.12,
    temporal=0.05,
    recency=0.05,
    engram_strength=0.05,
    thalamus_weighted=0.10,
    tag_overlap=0.15,
)

_WEIGHTS_EXPLORATION = ScoringWeights(
    ce=0.17,
    rrf=0.13,
    temporal=0.10,
    recency=0.10,
    engram_strength=0.15,
    thalamus_weighted=0.30,
    tag_overlap=0.05,
)

_WEIGHTS_ANALOGY = ScoringWeights(
    ce=0.27,
    rrf=0.23,
    temporal=0.05,
    recency=0.05,
    engram_strength=0.10,
    thalamus_weighted=0.25,
    tag_overlap=0.05,
)

_WEIGHTS_VALIDATION = ScoringWeights(
    ce=0.27,
    rrf=0.08,
    temporal=0.10,
    recency=0.05,
    engram_strength=0.10,
    thalamus_weighted=0.30,
    tag_overlap=0.10,
)


# ---------------------------------------------------------------------------
# MODE_PROFILES Registry — T3
# Default values from concept.md § 7 table.
#
# Note on strength_pre_filter (2026-04-09 hardening):
#   Consolidation 1 initializes fresh Engrams with strength ≈ _BASE_STRENGTH
#   (0.1) unless thalamus_overall is high. The previous defaults (Precision
#   0.5, Analogy/Validation 0.3) would filter out every buffer Engram between
#   RRF and Cross-Encoder, leaving the user blind to anything not yet promoted
#   to neocortex. Lowered defaults so fresh buffer Engrams have a chance in
#   retrieval while the scoring stage (w5×strength_weight with log dampening)
#   still surfaces stronger engrams first. See plan: post-setup hardening pass.
# ---------------------------------------------------------------------------
MODE_PROFILES: dict[RetrievalMode, ModeConfig] = {
    RetrievalMode.PRECISION: ModeConfig(
        strength_pre_filter=0.05,
        thalamus_boost_dimension="task_relevance",
        weak_link_policy="ignore",
        traversal_depth="shallow",
        construction_style="conservative",
        reconsolidation_level="minimal",
        scoring_weights=_WEIGHTS_PRECISION,
        w_schema=1.2,
        w_engram=0.9,
    ),
    RetrievalMode.EXPLORATION: ModeConfig(
        strength_pre_filter=0.0,
        thalamus_boost_dimension="novelty",
        weak_link_policy="follow",
        traversal_depth="deep",
        construction_style="creative",
        reconsolidation_level="moderate",
        scoring_weights=_WEIGHTS_EXPLORATION,
        w_schema=0.8,
        w_engram=1.2,
    ),
    RetrievalMode.ANALOGY: ModeConfig(
        strength_pre_filter=0.05,
        thalamus_boost_dimension=None,
        weak_link_policy="prefer",
        traversal_depth="medium",
        construction_style="cross_domain",
        reconsolidation_level="schema_update",
        scoring_weights=_WEIGHTS_ANALOGY,
        w_schema=1.1,
        w_engram=1.0,
    ),
    RetrievalMode.VALIDATION: ModeConfig(
        strength_pre_filter=0.1,
        thalamus_boost_dimension="surprise",
        weak_link_policy="ignore",
        traversal_depth="medium",
        construction_style="evidence_based",
        reconsolidation_level="aggressive",
        scoring_weights=_WEIGHTS_VALIDATION,
        w_schema=1.0,
        w_engram=1.0,
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
