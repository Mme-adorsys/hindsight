"""
Unit tests for Natural Decay + Composite Score (Epic 24 Story 03).

Covers:
- ``compute_decay`` edge cases (fresh, unused, amplification, decay, guards).
- ``compute_composite`` product behavior, overflow clamp, monotonicity.
- concept.md §5.3 reference values.
"""

from __future__ import annotations

import math

import pytest

from hindsight_api.engine.consolidation.scoring import (
    COMPOSITE_MAX,
    compute_composite,
    compute_decay,
)


# ---------------------------------------------------------------------------
# compute_decay
# ---------------------------------------------------------------------------


class TestDecayEdgeCases:
    def test_fresh_engram_returns_unity(self) -> None:
        # sessions_alive=0 → 1.0 regardless of access_count / r
        assert compute_decay(access_count=5, sessions_alive=0, r=0.5) == 1.0
        assert compute_decay(access_count=0, sessions_alive=0, r=0.5) == 1.0

    def test_never_accessed_returns_zero(self) -> None:
        assert compute_decay(access_count=0, sessions_alive=100, r=0.5) == 0.0

    def test_negative_sessions_clamps_to_unity(self) -> None:
        # Defensive — sessions_alive() already clamps, but guard here too.
        assert compute_decay(access_count=5, sessions_alive=-1, r=0.5) == 1.0

    def test_zero_rate_is_guarded(self) -> None:
        assert compute_decay(access_count=5, sessions_alive=10, r=0.0) == 1.0

    def test_negative_rate_is_guarded(self) -> None:
        assert compute_decay(access_count=5, sessions_alive=10, r=-1.0) == 1.0

    def test_negative_access_treated_as_zero(self) -> None:
        assert compute_decay(access_count=-1, sessions_alive=10, r=0.5) == 0.0


class TestDecayFormulaRegimes:
    def test_amplification_above_expected_access(self) -> None:
        # access=5, sessions=5, r=0.5 → expected=2.5 → log(6)/log(3.5)
        # ≈ 1.7918 / 1.2528 ≈ 1.4302
        result = compute_decay(access_count=5, sessions_alive=5, r=0.5)
        expected = math.log(6) / math.log(3.5)
        assert result == pytest.approx(expected, abs=1e-9)
        assert result > 1.0

    def test_decay_below_expected_access(self) -> None:
        # access=1, sessions=100, r=0.5 → expected=50 → log(2)/log(51)
        # ≈ 0.6931 / 3.9318 ≈ 0.1763
        result = compute_decay(access_count=1, sessions_alive=100, r=0.5)
        expected = math.log(2) / math.log(51)
        assert result == pytest.approx(expected, abs=1e-9)
        assert result < 1.0

    def test_decay_neutral_at_balanced_access(self) -> None:
        # access == expected → log(1+N)/log(1+N) = 1.0
        # Pick sessions=10, r=1.0 → expected=10 → access=10 → exactly 1.0
        result = compute_decay(access_count=10, sessions_alive=10, r=1.0)
        assert result == pytest.approx(1.0, abs=1e-9)

    def test_decay_monotonic_in_access_count(self) -> None:
        # More accesses → higher decay (amplification), all else equal.
        prev = -1.0
        for access in (1, 2, 5, 10, 20, 50, 100):
            d = compute_decay(access_count=access, sessions_alive=10, r=0.5)
            assert d > prev, f"decay must grow monotonically with access_count, broke at {access}"
            prev = d

    def test_decay_monotonic_in_sessions_alive(self) -> None:
        # More sessions with same access → lower decay (aging without use).
        prev = math.inf
        for sa in (1, 2, 5, 10, 50, 100, 500):
            d = compute_decay(access_count=3, sessions_alive=sa, r=0.5)
            assert d < prev, f"decay must shrink with sessions_alive, broke at {sa}"
            prev = d


# ---------------------------------------------------------------------------
# compute_composite
# ---------------------------------------------------------------------------


class TestCompositeBehavior:
    def test_fresh_engram_equals_thalamus_overall(self) -> None:
        # sessions_alive=0 → decay=1.0 → composite == thalamus_overall
        assert compute_composite(0.9, access_count=0, sessions_alive=0, r=0.5) == pytest.approx(0.9)
        assert compute_composite(0.42, access_count=3, sessions_alive=0, r=0.5) == pytest.approx(0.42)

    def test_amplification_can_exceed_birth_value(self) -> None:
        # High access relative to expected → decay > 1.0 → composite > thalamus_overall
        result = compute_composite(0.6, access_count=10, sessions_alive=5, r=0.5)
        assert result > 0.6

    def test_decay_below_birth_value(self) -> None:
        # Long-lived but rarely accessed → composite < thalamus_overall
        result = compute_composite(0.9, access_count=1, sessions_alive=100, r=0.5)
        assert result < 0.9
        assert result > 0.0

    def test_never_accessed_stale_engram_collapses_to_zero(self) -> None:
        assert compute_composite(0.9, access_count=0, sessions_alive=100, r=0.5) == 0.0

    def test_composite_clamped_to_overflow_cap(self) -> None:
        # Push decay sky-high via enormous access_count with small expected.
        # access=10^9, sessions=1, r=0.001 → expected=0.001 → huge decay.
        result = compute_composite(5.0, access_count=10**9, sessions_alive=1, r=0.001)
        assert result == COMPOSITE_MAX  # clamped from above

    def test_negative_thalamus_clamped_to_zero(self) -> None:
        # Guard against pathological input from callers; final bound at 0.
        assert compute_composite(-0.5, access_count=5, sessions_alive=2, r=0.5) == 0.0

    def test_composite_matches_thalamus_times_decay(self) -> None:
        # End-to-end hand-computation.
        decay = compute_decay(access_count=8, sessions_alive=4, r=0.4)
        assert compute_composite(0.75, 8, 4, 0.4) == pytest.approx(0.75 * decay, abs=1e-9)

    def test_fresh_engram_matches_thalamus_overall_exactly(self) -> None:
        # Initial strength at creation (decay=1.0) → composite == thalamus_overall.
        for overall in (0.0, 0.1, 0.5, 0.9, 1.0):
            assert compute_composite(overall, 0, 0, 0.5) == pytest.approx(overall)


# ---------------------------------------------------------------------------
# concept.md §5.3 reference table
# ---------------------------------------------------------------------------


class TestConceptReferenceTable:
    """Spot-checks from concept.md / engram-lifecycle-scoring.md worked examples."""

    def test_reference_amplification_5x_over_small_expected(self) -> None:
        # 5 recalls, 5 sessions, r=0.5 → log(6)/log(3.5) ≈ 1.43
        assert compute_decay(5, 5, 0.5) == pytest.approx(1.4302, abs=1e-3)

    def test_reference_strong_decay_1_access_over_many_sessions(self) -> None:
        # 1 recall, 100 sessions, r=0.5 → log(2)/log(51) ≈ 0.18
        assert compute_decay(1, 100, 0.5) == pytest.approx(0.1763, abs=1e-3)

    def test_reference_composite_amplified(self) -> None:
        # thalamus=0.9, high access, short lifetime, r=0.5
        value = compute_composite(0.9, 10, 5, 0.5)
        expected = 0.9 * (math.log(11) / math.log(3.5))
        assert value == pytest.approx(expected, abs=1e-9)
        assert value > 0.9  # confirms amplification regime
