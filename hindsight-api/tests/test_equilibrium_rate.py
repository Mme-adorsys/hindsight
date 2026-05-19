"""
Unit tests for the Equilibrium Rate `r` (Epic 24 Story 02).

Covers:
- ``compute_bank_factor`` — log ratio + empty-bank guard.
- ``compute_equilibrium_rate`` — demand/protection asymmetry, mode fallback,
  lower guard, and the concept.md §5.3 reference values.
"""

from __future__ import annotations

import math

import pytest

from hindsight_api.engine.consolidation.scoring import (
    DEFAULT_R_BASE,
    DEFAULT_R_BASE_ANALOGY,
    DEFAULT_R_BASE_EXPLORATION,
    DEFAULT_R_BASE_PRECISION,
    DEFAULT_R_BASE_VALIDATION,
    MIN_EQUILIBRIUM_RATE,
    MODE_R_BASE,
    REFERENCE_BANK_SIZE,
    compute_bank_factor,
    compute_equilibrium_rate,
)
from hindsight_api.engine.engram_types import ThalamusScores

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scores(
    *,
    novelty: float = 0.0,
    surprise: float = 0.0,
    task_relevance: float = 0.0,
    emotional_valence: float = 0.0,
    overall: float = 0.0,
) -> ThalamusScores:
    return ThalamusScores(
        novelty=novelty,
        surprise=surprise,
        task_relevance=task_relevance,
        emotional_valence=emotional_valence,
        overall=overall,
    )


# ---------------------------------------------------------------------------
# Constants sanity — catches accidental drift in defaults
# ---------------------------------------------------------------------------


class TestConstants:
    def test_mode_r_base_has_four_modes(self) -> None:
        assert set(MODE_R_BASE.keys()) == {"precision", "validation", "analogy", "exploration"}

    def test_default_r_base_values(self) -> None:
        assert MODE_R_BASE["precision"] == DEFAULT_R_BASE_PRECISION == 0.8
        assert MODE_R_BASE["validation"] == DEFAULT_R_BASE_VALIDATION == 0.6
        assert MODE_R_BASE["analogy"] == DEFAULT_R_BASE_ANALOGY == 0.4
        assert MODE_R_BASE["exploration"] == DEFAULT_R_BASE_EXPLORATION == 0.3

    def test_default_r_base_fallback(self) -> None:
        assert DEFAULT_R_BASE == 0.5


# ---------------------------------------------------------------------------
# compute_bank_factor
# ---------------------------------------------------------------------------


class TestBankFactor:
    def test_reference_size_returns_unity(self) -> None:
        assert compute_bank_factor(REFERENCE_BANK_SIZE) == pytest.approx(1.0)

    def test_small_bank_capped(self) -> None:
        # Raw log-ratio for size=50 is ≈ 1.7572 (>2× BASE_MIN_ACCESS),
        # which used to lock C1 out on dev banks. The new small-bank cap
        # (SMALL_BANK_FACTOR_CAP=1.5 when size < SMALL_BANK_THRESHOLD)
        # keeps the inflation but bounds it. See concept §5.3 + the
        # post-Epic-25 smoke fix.
        assert compute_bank_factor(50) == pytest.approx(1.5, abs=1e-3)

    def test_at_threshold_uses_raw_ratio(self) -> None:
        # SMALL_BANK_THRESHOLD == 100 → at exactly that size the cap stops
        # applying. Raw value: log(1001)/log(101) ≈ 6.9088 / 4.6151 ≈ 1.4970.
        assert compute_bank_factor(100) == pytest.approx(1.4970, abs=1e-3)

    def test_large_bank_deflates(self) -> None:
        # log(1001)/log(50001) ≈ 6.9088 / 10.8198 ≈ 0.6386
        assert compute_bank_factor(50_000) == pytest.approx(0.6386, abs=1e-3)

    def test_empty_bank_returns_small_bank_cap(self) -> None:
        # Used to be 2.0 (max compensation). Now capped to the small-bank
        # value so fresh dev banks aren't immediately locked out of C1.
        assert compute_bank_factor(0) == 1.5

    def test_negative_bank_returns_small_bank_cap(self) -> None:
        # Defensive — negative sizes should never happen but must not crash.
        assert compute_bank_factor(-1) == 1.5

    def test_custom_reference_size(self) -> None:
        # reference=100, size=100 → ratio = 1.0
        assert compute_bank_factor(100, reference_size=100) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_equilibrium_rate
# ---------------------------------------------------------------------------


class TestEquilibriumRate:
    def test_precision_mode_with_neutral_scores_returns_r_base_times_bank_factor(self) -> None:
        # All zeros → demand = protection = 1.0 → r = r_base × bank_factor
        r = compute_equilibrium_rate(_scores(), mode="precision", bank_size=REFERENCE_BANK_SIZE)
        assert r == pytest.approx(DEFAULT_R_BASE_PRECISION, abs=1e-6)

    def test_mode_fallback_on_unknown_mode(self) -> None:
        r = compute_equilibrium_rate(_scores(), mode="nonsense", bank_size=REFERENCE_BANK_SIZE)
        assert r == pytest.approx(DEFAULT_R_BASE, abs=1e-6)

    def test_mode_fallback_on_none(self) -> None:
        r = compute_equilibrium_rate(_scores(), mode=None, bank_size=REFERENCE_BANK_SIZE)
        assert r == pytest.approx(DEFAULT_R_BASE, abs=1e-6)

    def test_task_relevant_engram_raises_r(self) -> None:
        # task_rel=0.8, rest 0 → demand=1.4, protection=1.0 → r = 0.8 × 1.4 = 1.12
        r = compute_equilibrium_rate(
            _scores(task_relevance=0.8),
            mode="precision",
            bank_size=REFERENCE_BANK_SIZE,
        )
        assert r == pytest.approx(0.8 * 1.4, abs=1e-6)
        assert r > DEFAULT_R_BASE_PRECISION  # demand tightens the rate

    def test_surprising_emotional_engram_lowers_r(self) -> None:
        # nov=0.9, sur=0.8, val=0.7 → avg=0.8 → protection=1.4 → r = 0.8 / 1.4 ≈ 0.571
        r = compute_equilibrium_rate(
            _scores(novelty=0.9, surprise=0.8, emotional_valence=0.7),
            mode="precision",
            bank_size=REFERENCE_BANK_SIZE,
        )
        expected = DEFAULT_R_BASE_PRECISION / 1.4
        assert r == pytest.approx(expected, abs=1e-3)
        assert r < DEFAULT_R_BASE_PRECISION  # protection loosens the rate

    def test_routine_engram_near_r_base(self) -> None:
        # Balanced small scores → demand ≈ 1.1, protection ≈ 1.1 → ratio ≈ 1.0
        r = compute_equilibrium_rate(
            _scores(novelty=0.2, surprise=0.2, task_relevance=0.2, emotional_valence=0.2),
            mode="validation",
            bank_size=REFERENCE_BANK_SIZE,
        )
        assert r == pytest.approx(DEFAULT_R_BASE_VALIDATION, abs=1e-3)

    def test_small_bank_inflates_r(self) -> None:
        # bank_factor(50) ≈ 1.757 → r = 0.8 × 1.0 / 1.0 × 1.757 ≈ 1.406
        r = compute_equilibrium_rate(_scores(), mode="precision", bank_size=50)
        assert r == pytest.approx(DEFAULT_R_BASE_PRECISION * compute_bank_factor(50), abs=1e-6)
        assert r > DEFAULT_R_BASE_PRECISION

    def test_large_bank_deflates_r(self) -> None:
        # bank_factor(50000) ≈ 0.639 → r = 0.8 × 0.639 ≈ 0.511
        r = compute_equilibrium_rate(_scores(), mode="precision", bank_size=50_000)
        assert r == pytest.approx(DEFAULT_R_BASE_PRECISION * compute_bank_factor(50_000), abs=1e-6)
        assert r < DEFAULT_R_BASE_PRECISION

    def test_rate_is_always_positive(self) -> None:
        # Worst realistic case: exploration mode (lowest r_base), huge bank,
        # max protection, zero demand → should still clear the lower guard.
        r = compute_equilibrium_rate(
            _scores(novelty=1.0, surprise=1.0, emotional_valence=1.0),
            mode="exploration",
            bank_size=10**9,
        )
        assert r >= MIN_EQUILIBRIUM_RATE
        assert r > 0.0

    def test_lower_guard_clamps_pathological_input(self) -> None:
        # bank_factor(10^9) ≈ log(1001)/log(1+10^9) ≈ 6.9088/20.7233 ≈ 0.333
        # r = 0.3 × 1.0 / (1 + 0.5 × 1.0) × 0.333 = 0.0667 → above guard
        r = compute_equilibrium_rate(
            _scores(novelty=1.0, surprise=1.0, emotional_valence=1.0),
            mode="exploration",
            bank_size=10**9,
        )
        # Sanity: our guard value is a true lower bound, not an actual hit here.
        assert r > MIN_EQUILIBRIUM_RATE

    def test_bank_factor_contribution_matches_formula(self) -> None:
        # Hand-compute end-to-end for a realistic Engram.
        scores = _scores(novelty=0.5, surprise=0.3, task_relevance=0.6, emotional_valence=0.4)
        demand = 1.0 + 0.5 * 0.6
        protection = 1.0 + 0.5 * ((0.5 + 0.3 + 0.4) / 3.0)
        bf = math.log(1001) / math.log(201)
        expected = DEFAULT_R_BASE_VALIDATION * demand / protection * bf
        r = compute_equilibrium_rate(scores, mode="validation", bank_size=200)
        assert r == pytest.approx(expected, abs=1e-6)
