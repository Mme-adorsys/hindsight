"""
Unit tests for Session Layer — Mode Configuration Profiles (Epic 06 Story 01).

Tests validate:
- All 4 default profiles match concept.md § 7 table
- ScoringWeights per mode are consistent with concept.md § 8
- ModeConfig is immutable (frozen dataclass)
- with_overrides() creates new instance, leaves original unchanged
- get_mode_config() returns default without env override
- get_mode_config() applies env override to strength_pre_filter
"""

import dataclasses

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.session.mode_config import (
    MODE_PROFILES,
    ModeConfig,
    ScoringWeights,
    get_mode_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weights_sum(w: ScoringWeights) -> float:
    return w.ce + w.rrf + w.temporal + w.recency + w.engram_strength + w.thalamus_weighted


# ---------------------------------------------------------------------------
# T1 / T2 — ModeConfig and ScoringWeights structure
# ---------------------------------------------------------------------------


class TestModeConfigStructure:
    def test_mode_config_is_frozen(self):
        config = MODE_PROFILES[RetrievalMode.PRECISION]
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            config.strength_pre_filter = 0.9  # type: ignore[misc]

    def test_scoring_weights_is_frozen(self):
        weights = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            weights.ce = 0.9  # type: ignore[misc]

    def test_all_modes_present(self):
        assert set(MODE_PROFILES.keys()) == {
            RetrievalMode.PRECISION,
            RetrievalMode.EXPLORATION,
            RetrievalMode.ANALOGY,
            RetrievalMode.VALIDATION,
        }

    def test_scoring_weights_sum_to_one(self):
        for mode, config in MODE_PROFILES.items():
            total = _weights_sum(config.scoring_weights)
            assert abs(total - 1.0) < 1e-9, f"{mode}: weights sum to {total}, expected 1.0"

    def test_invalid_weights_sum_raises(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringWeights(ce=0.5, rrf=0.5, temporal=0.5, recency=0.0, engram_strength=0.0, thalamus_weighted=0.0)


# ---------------------------------------------------------------------------
# T3 — Default profiles match concept.md § 7
# ---------------------------------------------------------------------------


class TestDefaultProfiles:
    # Strength pre-filter values lowered on 2026-04-09 during post-setup
    # hardening. Fresh Engrams are initialised at strength ≈ 0.1 by
    # Consolidation 1; the previous defaults (0.5/0.1/0.3/0.3) filtered every
    # buffer Engram out between RRF and Cross-Encoder, leaving recall blind to
    # anything not yet promoted to neocortex. The scoring stage (w5 ×
    # strength_weight) still surfaces stronger engrams first.
    def test_precision_profile(self):
        cfg = MODE_PROFILES[RetrievalMode.PRECISION]
        assert cfg.strength_pre_filter == 0.05
        assert cfg.thalamus_boost_dimension == "task_relevance"
        assert cfg.weak_link_policy == "ignore"
        assert cfg.traversal_depth == "shallow"
        assert cfg.construction_style == "conservative"
        assert cfg.reconsolidation_level == "minimal"

    def test_exploration_profile(self):
        cfg = MODE_PROFILES[RetrievalMode.EXPLORATION]
        assert cfg.strength_pre_filter == 0.0
        assert cfg.thalamus_boost_dimension == "novelty"
        assert cfg.weak_link_policy == "follow"
        assert cfg.traversal_depth == "deep"
        assert cfg.construction_style == "creative"
        assert cfg.reconsolidation_level == "moderate"

    def test_analogy_profile(self):
        cfg = MODE_PROFILES[RetrievalMode.ANALOGY]
        assert cfg.strength_pre_filter == 0.05
        assert cfg.thalamus_boost_dimension is None
        assert cfg.weak_link_policy == "prefer"
        assert cfg.traversal_depth == "medium"
        assert cfg.construction_style == "cross_domain"
        assert cfg.reconsolidation_level == "schema_update"

    def test_validation_profile(self):
        cfg = MODE_PROFILES[RetrievalMode.VALIDATION]
        assert cfg.strength_pre_filter == 0.1
        assert cfg.thalamus_boost_dimension == "surprise"
        assert cfg.weak_link_policy == "ignore"
        assert cfg.traversal_depth == "medium"
        assert cfg.construction_style == "evidence_based"
        assert cfg.reconsolidation_level == "aggressive"


# ---------------------------------------------------------------------------
# T2 — ScoringWeights direction checks (concept.md § 8)
# ---------------------------------------------------------------------------


class TestScoringWeightsDirections:
    def test_precision_high_ce(self):
        """Precision mode should emphasise CE (stay on task)."""
        w = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights
        assert w.ce > 0.5, "Precision CE weight should be dominant"

    def test_exploration_high_thalamus(self):
        """Exploration mode should boost Thalamus (novelty capture)."""
        w = MODE_PROFILES[RetrievalMode.EXPLORATION].scoring_weights
        assert w.thalamus_weighted >= w.ce, "Exploration: Thalamus >= CE"

    def test_validation_high_thalamus(self):
        """Validation mode should surface surprises via Thalamus boost."""
        w = MODE_PROFILES[RetrievalMode.VALIDATION].scoring_weights
        assert w.thalamus_weighted >= 0.25

    def test_precision_lower_thalamus_than_exploration(self):
        precision_w = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights
        exploration_w = MODE_PROFILES[RetrievalMode.EXPLORATION].scoring_weights
        assert precision_w.thalamus_weighted < exploration_w.thalamus_weighted


# ---------------------------------------------------------------------------
# T4 — with_overrides immutability
# ---------------------------------------------------------------------------


class TestWithOverrides:
    def test_with_overrides_returns_new_instance(self):
        original = MODE_PROFILES[RetrievalMode.PRECISION]
        modified = original.with_overrides(strength_pre_filter=0.8)
        assert modified is not original

    def test_with_overrides_changes_only_specified_field(self):
        original = MODE_PROFILES[RetrievalMode.PRECISION]
        modified = original.with_overrides(strength_pre_filter=0.8)
        assert modified.strength_pre_filter == 0.8
        # All other fields unchanged
        assert modified.thalamus_boost_dimension == original.thalamus_boost_dimension
        assert modified.weak_link_policy == original.weak_link_policy
        assert modified.traversal_depth == original.traversal_depth
        assert modified.construction_style == original.construction_style
        assert modified.reconsolidation_level == original.reconsolidation_level
        assert modified.scoring_weights == original.scoring_weights

    def test_with_overrides_does_not_mutate_original(self):
        original = MODE_PROFILES[RetrievalMode.PRECISION]
        original_strength = original.strength_pre_filter
        _ = original.with_overrides(strength_pre_filter=0.99)
        assert original.strength_pre_filter == original_strength

    def test_with_overrides_multiple_fields(self):
        original = MODE_PROFILES[RetrievalMode.EXPLORATION]
        modified = original.with_overrides(strength_pre_filter=0.4, traversal_depth="medium")
        assert modified.strength_pre_filter == 0.4
        assert modified.traversal_depth == "medium"
        assert modified.construction_style == original.construction_style


# ---------------------------------------------------------------------------
# T5 — get_mode_config with and without env overrides
# ---------------------------------------------------------------------------


class TestGetModeConfig:
    def test_returns_default_without_env(self, monkeypatch):
        monkeypatch.delenv("ENGRAM_PRECISION_STRENGTH_THRESHOLD", raising=False)
        cfg = get_mode_config(RetrievalMode.PRECISION)
        assert cfg == MODE_PROFILES[RetrievalMode.PRECISION]

    def test_applies_strength_override_from_env(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_PRECISION_STRENGTH_THRESHOLD", "0.7")
        cfg = get_mode_config(RetrievalMode.PRECISION)
        assert cfg.strength_pre_filter == pytest.approx(0.7)

    def test_override_does_not_affect_other_modes(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_PRECISION_STRENGTH_THRESHOLD", "0.9")
        exploration_cfg = get_mode_config(RetrievalMode.EXPLORATION)
        assert exploration_cfg == MODE_PROFILES[RetrievalMode.EXPLORATION]

    def test_all_modes_resolvable(self, monkeypatch):
        for env_var in (
            "ENGRAM_PRECISION_STRENGTH_THRESHOLD",
            "ENGRAM_EXPLORATION_STRENGTH_THRESHOLD",
            "ENGRAM_ANALOGY_STRENGTH_THRESHOLD",
            "ENGRAM_VALIDATION_STRENGTH_THRESHOLD",
        ):
            monkeypatch.delenv(env_var, raising=False)

        for mode in RetrievalMode:
            cfg = get_mode_config(mode)
            assert isinstance(cfg, ModeConfig)

    def test_exploration_env_override(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_EXPLORATION_STRENGTH_THRESHOLD", "0.05")
        cfg = get_mode_config(RetrievalMode.EXPLORATION)
        assert cfg.strength_pre_filter == pytest.approx(0.05)
        assert cfg.thalamus_boost_dimension == "novelty"  # unchanged
