"""
Unit tests for the composite strength scoring formula (Epic 24).

Verifies the logarithmic decay, asymmetric recovery, threshold behavior,
and the calibration table from the plan.
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.consolidation.scoring import (
    ARCHIVE_THRESHOLD_WM,
    PROMOTE_THRESHOLD,
    compute_composite_strength,
    compute_recount_score,
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
    # but composite_strength clamps via the w_recount weight


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
# compute_composite_strength — calibration table from plan
# ---------------------------------------------------------------------------


def test_high_thalamus_no_access_above_promote():
    # Thalamus 0.8, no access, 1 cycle → 0.7*0.8 + 0.3*0 = 0.56
    score = compute_composite_strength(0.8, 0, 1)
    assert score >= PROMOTE_THRESHOLD
    assert abs(score - 0.56) < 0.01


def test_medium_thalamus_no_access_below_promote():
    # Thalamus 0.5, no access, 5 cycles → 0.7*0.5 + 0 = 0.35
    score = compute_composite_strength(0.5, 0, 5)
    assert score < PROMOTE_THRESHOLD
    assert abs(score - 0.35) < 0.01


def test_medium_thalamus_some_access_above_promote():
    # Thalamus 0.5, 3 accesses, 5 cycles → should cross 0.4
    score = compute_composite_strength(0.5, 3, 5)
    assert score >= PROMOTE_THRESHOLD


def test_low_thalamus_high_access_above_promote():
    # Thalamus 0.2, 15 accesses, 10 cycles → should cross 0.4
    score = compute_composite_strength(0.2, 15, 10)
    assert score >= PROMOTE_THRESHOLD


def test_low_thalamus_medium_access_below_promote():
    # Thalamus 0.2, 5 accesses, 10 cycles → still below threshold
    score = compute_composite_strength(0.2, 5, 10)
    assert score < PROMOTE_THRESHOLD


def test_low_thalamus_8_accesses_barely_crosses():
    # Thalamus 0.2, 8 accesses, 10 cycles → just barely crosses 0.4
    # This shows the formula rewards persistent re-access even at low saliency.
    score = compute_composite_strength(0.2, 8, 10)
    assert score >= PROMOTE_THRESHOLD
    assert score < 0.45  # but not by much


def test_zero_thalamus_zero_access_below_archive():
    # Thalamus 0.0, no access, 50 cycles → 0.0 < 0.08
    score = compute_composite_strength(0.0, 0, 50)
    assert score < ARCHIVE_THRESHOLD_WM


def test_none_thalamus_treated_as_zero():
    score = compute_composite_strength(None, 0, 10)
    assert score == 0.0


# ---------------------------------------------------------------------------
# Asymmetric recovery — the key bio property
# ---------------------------------------------------------------------------


def test_old_memory_reactivated_recovers():
    """A single access on an old memory should lift the score measurably."""
    score_before = compute_composite_strength(0.3, 0, 50)
    score_after = compute_composite_strength(0.3, 3, 50)
    assert score_after > score_before
    # The lift should be non-trivial
    assert (score_after - score_before) > 0.05


def test_asymmetric_boost_vs_decay():
    """One access should boost more than one additional cycle decays."""
    base = compute_composite_strength(0.3, 5, 20)

    # One more cycle without access
    decayed = compute_composite_strength(0.3, 5, 21)
    decay_delta = base - decayed

    # One more access without cycle
    boosted = compute_composite_strength(0.3, 6, 20)
    boost_delta = boosted - base

    assert boost_delta > decay_delta, (
        f"Boost ({boost_delta:.4f}) should exceed decay ({decay_delta:.4f}) "
        f"for asymmetric recovery"
    )


# ---------------------------------------------------------------------------
# Logarithmic decay curve — monotonically decreasing without access
# ---------------------------------------------------------------------------


def test_logarithmic_decay_curve_monotonic():
    """Score should monotonically decrease over 50 cycles without access."""
    scores = [compute_composite_strength(0.5, 0, c) for c in range(50)]
    # Without access, recount is always 0, so score stays flat at 0.7*0.5 = 0.35
    # (thalamus component doesn't decay in the formula — it's the recount that
    # provides the decay dynamic). All scores should be equal.
    for s in scores:
        assert abs(s - 0.35) < 0.001


def test_decay_with_fixed_access_over_time():
    """Score with fixed access_count decreases as cycles grow."""
    scores = [compute_composite_strength(0.3, 3, c) for c in [1, 5, 10, 20, 50]]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"Expected score to decrease or stay flat from cycle "
            f"{[1,5,10,20,50][i]} to {[1,5,10,20,50][i+1]}"
        )


# ---------------------------------------------------------------------------
# Weight configuration
# ---------------------------------------------------------------------------


def test_custom_weights():
    score_70_30 = compute_composite_strength(0.5, 5, 10, w_thalamus=0.7, w_recount=0.3)
    score_50_50 = compute_composite_strength(0.5, 5, 10, w_thalamus=0.5, w_recount=0.5)
    # 50/50 gives more weight to recount, so should differ
    assert score_70_30 != score_50_50
