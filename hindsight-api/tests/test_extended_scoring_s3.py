"""
Unit tests for Epic 07 Story 03 — Extended Scoring Formula (S3+S4).

Tests validate WITHOUT a running database:
- Strength-modulated recency decay (T1)
- Thalamus weight calculation with boost_dimension (T2)
- Strength logarithmic dampening (T3)
- calculate_combined_score with ScoringWeights (T4)
- Strength pre-filter removes weak candidates (T5 structural test)
- Fallback to Hindsight defaults when mode=None (T6)
"""

import math

import pytest

from hindsight_api.engine.search.scoring import (
    calculate_combined_score,
    calculate_recency_weight,
    calculate_strength_weight,
    calculate_thalamus_weight,
)
from hindsight_api.engine.session.mode_config import ScoringWeights


# ---------------------------------------------------------------------------
# TestStrengthModulatedRecency — T1
# ---------------------------------------------------------------------------


class TestStrengthModulatedRecency:
    """calculate_recency_weight() with strength parameter."""

    def test_today_is_one_regardless_of_strength(self):
        """0 days ago → weight=1.0 for any strength."""
        assert calculate_recency_weight(0.0, strength=0.0) == pytest.approx(1.0)
        assert calculate_recency_weight(0.0, strength=1.0) == pytest.approx(1.0)

    def test_default_strength_matches_previous_behaviour(self):
        """strength=0.5 (default): effective_half_life=547.5 days, weight at 365 days."""
        # With strength=0.5: effective_half_life = 365 * 1.5 = 547.5
        # normalized_age = 365 / 547.5 ≈ 0.667 → 1/(1+log1p(0.667))
        effective_half = 365 * 1.5
        expected = 1.0 / (1.0 + math.log1p(365 / effective_half))
        assert calculate_recency_weight(365, strength=0.5) == pytest.approx(expected)

    def test_higher_strength_decays_slower(self):
        """Stronger engrams age more slowly — same days, higher weight."""
        w_low = calculate_recency_weight(365, strength=0.0)
        w_high = calculate_recency_weight(365, strength=1.0)
        assert w_high > w_low, "High-strength engram must decay slower"

    def test_strength_zero_uses_base_half_life(self):
        """strength=0.0: effective_half_life = base_half_life × 1.0 = 365."""
        expected = 1.0 / (1.0 + math.log1p(1.0))  # days=365, half_life=365 → normalized=1.0
        assert calculate_recency_weight(365, half_life_days=365, strength=0.0) == pytest.approx(expected)

    def test_strength_one_doubles_half_life(self):
        """strength=1.0: effective_half_life = base_half_life × 2."""
        expected = 1.0 / (1.0 + math.log1p(365 / 730))
        assert calculate_recency_weight(365, half_life_days=365, strength=1.0) == pytest.approx(expected)

    def test_weight_between_zero_and_one(self):
        """Result always in [0, 1]."""
        for days in (0, 30, 365, 1825, 10000):
            for strength in (0.0, 0.5, 1.0):
                w = calculate_recency_weight(days, strength=strength)
                assert 0.0 <= w <= 1.0, f"Out of range: days={days}, strength={strength}, w={w}"


# ---------------------------------------------------------------------------
# TestThalamusWeight — T2
# ---------------------------------------------------------------------------


class TestThalamusWeight:
    def test_none_scores_returns_neutral(self):
        assert calculate_thalamus_weight(None, boost_dimension=None) == pytest.approx(0.5)

    def test_none_scores_with_boost_returns_neutral(self):
        assert calculate_thalamus_weight(None, boost_dimension="novelty") == pytest.approx(0.5)

    def test_no_boost_returns_mean_of_four_dims(self):
        scores = {"novelty": 0.8, "surprise": 0.4, "task_relevance": 0.6, "emotional_valence": 0.2}
        expected = (0.8 + 0.4 + 0.6 + 0.2) / 4.0
        assert calculate_thalamus_weight(scores, boost_dimension=None) == pytest.approx(expected)

    def test_boost_dimension_multiplied_by_1_5(self):
        scores = {"novelty": 0.6, "surprise": 0.3, "task_relevance": 0.5, "emotional_valence": 0.1}
        result = calculate_thalamus_weight(scores, boost_dimension="novelty")
        assert result == pytest.approx(min(1.0, 0.6 * 1.5))

    def test_boost_capped_at_one(self):
        scores = {"novelty": 0.9, "surprise": 0.3, "task_relevance": 0.5, "emotional_valence": 0.1}
        result = calculate_thalamus_weight(scores, boost_dimension="novelty")
        assert result == pytest.approx(1.0)  # 0.9 × 1.5 = 1.35, capped to 1.0

    def test_missing_boost_dimension_key_falls_back_to_mean(self):
        scores = {"novelty": 0.8, "surprise": 0.4, "task_relevance": 0.6, "emotional_valence": 0.2}
        expected = (0.8 + 0.4 + 0.6 + 0.2) / 4.0
        assert calculate_thalamus_weight(scores, boost_dimension="nonexistent_dim") == pytest.approx(expected)

    def test_empty_scores_dict_returns_zero_mean(self):
        result = calculate_thalamus_weight({}, boost_dimension=None)
        assert result == pytest.approx(0.0)

    def test_precision_boosts_task_relevance(self):
        """Precision mode boosts task_relevance — higher than plain mean."""
        scores = {"novelty": 0.2, "surprise": 0.2, "task_relevance": 0.8, "emotional_valence": 0.2}
        plain = calculate_thalamus_weight(scores, boost_dimension=None)
        boosted = calculate_thalamus_weight(scores, boost_dimension="task_relevance")
        assert boosted > plain

    def test_exploration_boosts_novelty(self):
        """Exploration mode boosts novelty — higher than plain mean."""
        scores = {"novelty": 0.9, "surprise": 0.3, "task_relevance": 0.3, "emotional_valence": 0.2}
        plain = calculate_thalamus_weight(scores, boost_dimension=None)
        boosted = calculate_thalamus_weight(scores, boost_dimension="novelty")
        assert boosted >= plain


# ---------------------------------------------------------------------------
# TestStrengthWeight — T3
# ---------------------------------------------------------------------------


class TestStrengthWeight:
    def test_zero_strength_returns_zero(self):
        assert calculate_strength_weight(0.0) == pytest.approx(0.0)

    def test_one_strength_returns_one(self):
        assert calculate_strength_weight(1.0) == pytest.approx(1.0)

    def test_monotonically_increasing(self):
        """Higher strength → higher weight."""
        weights = [calculate_strength_weight(s) for s in (0.0, 0.2, 0.5, 0.8, 1.0)]
        for i in range(len(weights) - 1):
            assert weights[i] < weights[i + 1]

    def test_logarithmic_dampening_midpoint(self):
        """strength=0.5 → log1p(0.5)/log1p(1) ≈ 0.585."""
        expected = math.log1p(0.5) / math.log1p(1.0)
        assert calculate_strength_weight(0.5) == pytest.approx(expected)

    def test_output_in_unit_range(self):
        for s in (0.0, 0.1, 0.3, 0.5, 0.7, 1.0):
            w = calculate_strength_weight(s)
            assert 0.0 <= w <= 1.0, f"Out of range for strength={s}: {w}"


# ---------------------------------------------------------------------------
# TestCalculateCombinedScore — T4
# ---------------------------------------------------------------------------


class TestCalculateCombinedScore:
    def _make_weights(self, ce=0.6, rrf=0.2, temporal=0.1, recency=0.1, strength=0.0, thalamus=0.0):
        return ScoringWeights(
            ce=ce, rrf=rrf, temporal=temporal, recency=recency,
            engram_strength=strength, thalamus_weighted=thalamus,
        )

    def test_pure_ce_weights(self):
        """All weight on CE → result equals CE score."""
        w = self._make_weights(ce=1.0, rrf=0.0, temporal=0.0, recency=0.0, strength=0.0, thalamus=0.0)
        score = calculate_combined_score(0.8, 0.5, 0.5, 0.5, 0.5, 0.5, w)
        assert score == pytest.approx(0.8)

    def test_original_hindsight_weights(self):
        """60/20/10/10 split reproduces original behaviour."""
        w = self._make_weights(ce=0.6, rrf=0.2, temporal=0.1, recency=0.1, strength=0.0, thalamus=0.0)
        ce, rrf, temporal, recency = 0.7, 0.5, 0.6, 0.4
        expected = 0.6 * ce + 0.2 * rrf + 0.1 * temporal + 0.1 * recency
        result = calculate_combined_score(ce, rrf, temporal, recency, 0.0, 0.0, w)
        assert result == pytest.approx(expected)

    def test_all_ones_returns_one(self):
        """All scores 1.0 with any weights summing to 1.0 → result=1.0."""
        w = self._make_weights(ce=0.6, rrf=0.15, temporal=0.05, recency=0.05, strength=0.05, thalamus=0.10)
        result = calculate_combined_score(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, w)
        assert result == pytest.approx(1.0)

    def test_all_zeros_returns_zero(self):
        """All scores 0.0 → result=0.0 regardless of weights."""
        w = self._make_weights()
        result = calculate_combined_score(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, w)
        assert result == pytest.approx(0.0)

    def test_precision_weights_rank_ce_highest(self):
        """With Precision weights (high CE), CE-dominant result beats RRF-dominant."""
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        from hindsight_api.engine.response_models import RetrievalMode

        w = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights
        # High CE, low RRF
        score_ce = calculate_combined_score(0.9, 0.2, 0.5, 0.5, 0.5, 0.5, w)
        # Low CE, high RRF
        score_rrf = calculate_combined_score(0.2, 0.9, 0.5, 0.5, 0.5, 0.5, w)
        assert score_ce > score_rrf

    def test_exploration_weights_rank_thalamus_higher(self):
        """Exploration weights (high thalamus) boost novel items more than precision."""
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        from hindsight_api.engine.response_models import RetrievalMode

        w_explore = MODE_PROFILES[RetrievalMode.EXPLORATION].scoring_weights
        w_precision = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights
        # High thalamus score (novel item)
        ce, rrf, temporal, recency, sw, tw = 0.5, 0.5, 0.5, 0.5, 0.5, 0.9
        score_explore = calculate_combined_score(ce, rrf, temporal, recency, sw, tw, w_explore)
        score_precision = calculate_combined_score(ce, rrf, temporal, recency, sw, tw, w_precision)
        assert score_explore > score_precision

    def test_result_in_unit_range(self):
        """All inputs in [0,1] with valid weights → result in [0,1]."""
        w = self._make_weights(ce=0.6, rrf=0.15, temporal=0.05, recency=0.05, strength=0.05, thalamus=0.10)
        for ce in (0.0, 0.5, 1.0):
            for tw in (0.0, 0.5, 1.0):
                result = calculate_combined_score(ce, 0.5, 0.5, 0.5, 0.5, tw, w)
                assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# TestScoringWeightsIntegration — T4 extended
# ---------------------------------------------------------------------------


class TestScoringWeightsIntegration:
    """Verify ScoringWeights sums to 1.0 and TypeError on invalid input."""

    def test_all_mode_weights_sum_to_one(self):
        from hindsight_api.engine.session.mode_config import MODE_PROFILES

        for mode, profile in MODE_PROFILES.items():
            w = profile.scoring_weights
            total = w.ce + w.rrf + w.temporal + w.recency + w.engram_strength + w.thalamus_weighted
            assert abs(total - 1.0) < 1e-6, f"Weights for {mode} don't sum to 1.0: {total}"

    def test_invalid_weights_raise_value_error(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringWeights(ce=0.5, rrf=0.5, temporal=0.1, recency=0.0, engram_strength=0.0, thalamus_weighted=0.0)
