"""
Unit tests for the composite strength scoring formula (Epic 24, revised).

Verifies the recall-driven scoring, saliency boost, mode-dependent thresholds,
and the calibration table from the plan.
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.consolidation.scoring import (
    ARCHIVE_THRESHOLD_WM,
    DEFAULT_PROMOTE_THRESHOLD,
    MODE_PROMOTE_THRESHOLDS,
    SALIENCY_WEIGHT,
    compute_composite_strength,
    compute_recount_score,
    get_promote_threshold,
)

# ---------------------------------------------------------------------------
# compute_recount_score
# ---------------------------------------------------------------------------


def test_recount_zero_access_is_zero():
    assert compute_recount_score(0, 10) == 0.0


def test_recount_zero_cycles_bounded():
    # cycles_alive=0 → log(2+0) = log(2) ≈ 0.693
    # access_count=5 → log(6) ≈ 1.791
    score = compute_recount_score(5, 0)
    assert score > 1.0  # can exceed 1 when accesses >> cycles at start


def test_recount_many_cycles_decays():
    # Same access count, increasing cycles → score decreases
    scores = [compute_recount_score(3, c) for c in [1, 5, 10, 50, 100]]
    for i in range(len(scores) - 1):
        assert scores[i] > scores[i + 1], f"Expected decay at cycle {i}"


def test_recount_logarithmic_not_linear():
    # Doubling cycles should NOT halve the score (logarithmic, not linear)
    score_10 = compute_recount_score(5, 10)
    score_20 = compute_recount_score(5, 20)
    ratio = score_20 / score_10
    assert ratio > 0.5, "Logarithmic decay should be gentler than linear"


# ---------------------------------------------------------------------------
# compute_composite_strength — new formula: recall_score + 0.3 * saliency
# ---------------------------------------------------------------------------


def test_zero_access_score_is_saliency_only():
    # No access → recall_score=0, composite = 0.3 * max(0.8, 0.3) = 0.24
    score = compute_composite_strength(0.8, 0.3, 0, 10)
    assert abs(score - SALIENCY_WEIGHT * 0.8) < 0.01


def test_high_saliency_5_access_cycles10():
    # emotional=0.8, surprise=0.7, access=5, cycles=10
    # recall_score = log(6)/log(12) ≈ 0.722
    # saliency = max(0.8, 0.7) = 0.8
    # composite = 0.722 + 0.3*0.8 = 0.962
    score = compute_composite_strength(0.8, 0.7, 5, 10)
    assert score > 0.9
    assert score < 1.1


def test_low_saliency_5_access_cycles10():
    # emotional=0.1, surprise=0.1, access=5, cycles=10
    # recall_score ≈ 0.722
    # saliency = 0.1
    # composite = 0.722 + 0.3*0.1 = 0.752
    score = compute_composite_strength(0.1, 0.1, 5, 10)
    assert score > 0.7
    assert score < 0.8


def test_low_saliency_30_access_promoted():
    # emotional=0.1, surprise=0.1, access=30, cycles=10
    # recall_score = log(31)/log(12) ≈ 1.383
    # composite = 1.383 + 0.03 = 1.413
    score = compute_composite_strength(0.1, 0.1, 30, 10)
    assert score > 1.3


def test_high_saliency_old_memory_decays():
    # Same saliency and access, more cycles → lower score
    score_10 = compute_composite_strength(0.8, 0.7, 5, 10)
    score_20 = compute_composite_strength(0.8, 0.7, 5, 20)
    assert score_10 > score_20


def test_none_emotional_treated_as_zero():
    score = compute_composite_strength(None, 0.5, 5, 10)
    expected = compute_composite_strength(0.0, 0.5, 5, 10)
    assert score == expected


def test_none_surprise_treated_as_zero():
    score = compute_composite_strength(0.5, None, 5, 10)
    expected = compute_composite_strength(0.5, 0.0, 5, 10)
    assert score == expected


def test_both_none_treated_as_zero():
    score = compute_composite_strength(None, None, 0, 10)
    assert score == 0.0


def test_saliency_uses_max_not_sum():
    # max(0.8, 0.2) = 0.8, not 1.0
    score = compute_composite_strength(0.8, 0.2, 5, 10)
    score_reversed = compute_composite_strength(0.2, 0.8, 5, 10)
    assert score == score_reversed  # max is symmetric


# ---------------------------------------------------------------------------
# Asymmetric recovery — the key bio property (unchanged)
# ---------------------------------------------------------------------------


def test_old_memory_reactivated_recovers():
    """A single access on an old memory should lift the score measurably."""
    score_before = compute_composite_strength(0.3, 0.3, 0, 50)
    score_after = compute_composite_strength(0.3, 0.3, 3, 50)
    assert score_after > score_before
    assert (score_after - score_before) > 0.05


def test_asymmetric_boost_vs_decay():
    """One access should boost more than one additional cycle decays."""
    base = compute_composite_strength(0.3, 0.3, 5, 20)

    # One more cycle without access
    decayed = compute_composite_strength(0.3, 0.3, 5, 21)
    decay_delta = base - decayed

    # One more access without cycle
    boosted = compute_composite_strength(0.3, 0.3, 6, 20)
    boost_delta = boosted - base

    assert boost_delta > decay_delta, (
        f"Boost ({boost_delta:.4f}) should exceed decay ({decay_delta:.4f}) "
        f"for asymmetric recovery"
    )


# ---------------------------------------------------------------------------
# Decay curve — monotonically decreasing without additional access
# ---------------------------------------------------------------------------


def test_decay_with_fixed_access_over_time():
    """Score with fixed access_count decreases as cycles grow."""
    scores = [compute_composite_strength(0.3, 0.3, 3, c) for c in [1, 5, 10, 20, 50]]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"Expected score to decrease or stay flat from cycle "
            f"{[1,5,10,20,50][i]} to {[1,5,10,20,50][i+1]}"
        )


def test_zero_access_score_flat_across_cycles():
    """Without access, recall_score is 0 — only saliency contributes (constant)."""
    scores = [compute_composite_strength(0.5, 0.3, 0, c) for c in range(50)]
    for s in scores:
        assert abs(s - SALIENCY_WEIGHT * 0.5) < 0.001


# ---------------------------------------------------------------------------
# get_promote_threshold
# ---------------------------------------------------------------------------


def test_promote_threshold_precision():
    assert get_promote_threshold("precision") == 0.8


def test_promote_threshold_exploration():
    assert get_promote_threshold("exploration") == 0.5


def test_promote_threshold_validation():
    assert get_promote_threshold("validation") == 0.7


def test_promote_threshold_analogy():
    assert get_promote_threshold("analogy") == 0.6


def test_promote_threshold_none_uses_default():
    assert get_promote_threshold(None) == DEFAULT_PROMOTE_THRESHOLD


def test_promote_threshold_unknown_mode_uses_default():
    assert get_promote_threshold("unknown_mode") == DEFAULT_PROMOTE_THRESHOLD


# ---------------------------------------------------------------------------
# Calibration table from plan (cycles_alive=10)
# ---------------------------------------------------------------------------


class TestCalibrationTable:
    """Verify the example scenarios from the plan."""

    def test_important_5x_recalled(self):
        # emotional=0.8, surprise=0.7, access=5, cycles=10 → 0.96
        score = compute_composite_strength(0.8, 0.7, 5, 10)
        assert score >= MODE_PROMOTE_THRESHOLDS["precision"]  # 0.8

    def test_important_5x_old(self):
        # emotional=0.8, surprise=0.7, access=5, cycles=20
        # recall_score = log(6)/log(22) ≈ 0.58, saliency=0.8
        # composite ≈ 0.58 + 0.24 = 0.82 — still above precision
        score = compute_composite_strength(0.8, 0.7, 5, 20)
        assert score >= MODE_PROMOTE_THRESHOLDS["precision"]  # >= 0.8

    def test_important_5x_very_old(self):
        # emotional=0.8, surprise=0.7, access=5, cycles=50
        # recall_score = log(6)/log(52) ≈ 0.45, saliency=0.8
        # composite ≈ 0.45 + 0.24 = 0.69 — below precision, above exploration
        score = compute_composite_strength(0.8, 0.7, 5, 50)
        assert score < MODE_PROMOTE_THRESHOLDS["precision"]
        assert score >= MODE_PROMOTE_THRESHOLDS["exploration"]

    def test_unimportant_30x_recalled(self):
        # emotional=0.1, surprise=0.1, access=30, cycles=10 → 1.41
        score = compute_composite_strength(0.1, 0.1, 30, 10)
        assert score >= MODE_PROMOTE_THRESHOLDS["precision"]

    def test_unimportant_5x_recalled(self):
        # emotional=0.1, surprise=0.1, access=5, cycles=10 → 0.75
        score = compute_composite_strength(0.1, 0.1, 5, 10)
        assert score < MODE_PROMOTE_THRESHOLDS["precision"]
        assert score >= MODE_PROMOTE_THRESHOLDS["exploration"]

    def test_unimportant_5x_old(self):
        # emotional=0.1, surprise=0.1, access=5, cycles=20 → 0.57
        score = compute_composite_strength(0.1, 0.1, 5, 20)
        assert score < MODE_PROMOTE_THRESHOLDS["precision"]
        assert score >= MODE_PROMOTE_THRESHOLDS["exploration"]


# ---------------------------------------------------------------------------
# Custom saliency weight
# ---------------------------------------------------------------------------


def test_custom_saliency_weight():
    score_default = compute_composite_strength(0.8, 0.5, 5, 10)
    score_higher = compute_composite_strength(0.8, 0.5, 5, 10, saliency_weight=0.5)
    assert score_higher > score_default
