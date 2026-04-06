"""
Unit tests for Epic 10 Story 03 — Disposition-aware Reconsolidation (RF4).

Covers:
- DispositionProfile presets: NEUTRAL, ANALYTICAL, OPTIMISTIC, CONSERVATIVE
- get_disposition_profile: mapping from DispositionTraits
- apply_modulated_strength_delta: evidence_weight, update_bias, contradiction_tolerance
- Contradiction suppression when similarity < contradiction_tolerance
- Neutral fallback when no traits provided
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.reflect.disposition_profile import (
    ANALYTICAL,
    CONSERVATIVE,
    NEUTRAL,
    OPTIMISTIC,
    apply_modulated_strength_delta,
    get_disposition_profile,
)
from hindsight_api.engine.response_models import DispositionTraits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _traits(skepticism: int = 3, literalism: int = 3, empathy: int = 3) -> DispositionTraits:
    return DispositionTraits(skepticism=skepticism, literalism=literalism, empathy=empathy)


# ---------------------------------------------------------------------------
# Preset values
# ---------------------------------------------------------------------------


class TestDispositionProfilePresets:
    def test_neutral_no_modulation(self):
        assert NEUTRAL.evidence_weight == pytest.approx(1.0)
        assert NEUTRAL.update_bias == pytest.approx(0.0)
        assert NEUTRAL.contradiction_tolerance == pytest.approx(0.0)

    def test_analytical_higher_evidence_weight(self):
        assert ANALYTICAL.evidence_weight > 1.0
        assert ANALYTICAL.contradiction_tolerance < CONSERVATIVE.contradiction_tolerance

    def test_optimistic_positive_bias(self):
        assert OPTIMISTIC.update_bias > 0.0

    def test_conservative_higher_tolerance_than_analytical(self):
        assert CONSERVATIVE.contradiction_tolerance > ANALYTICAL.contradiction_tolerance

    def test_all_profiles_have_names(self):
        for profile in [NEUTRAL, ANALYTICAL, OPTIMISTIC, CONSERVATIVE]:
            assert profile.name in {"neutral", "analytical", "optimistic", "conservative"}


# ---------------------------------------------------------------------------
# get_disposition_profile
# ---------------------------------------------------------------------------


class TestGetDispositionProfile:
    def test_none_traits_returns_neutral(self):
        assert get_disposition_profile(None) is NEUTRAL

    def test_all_mid_traits_returns_neutral(self):
        result = get_disposition_profile(_traits(3, 3, 3))
        assert result is NEUTRAL

    def test_high_skepticism_returns_analytical(self):
        result = get_disposition_profile(_traits(skepticism=4))
        assert result is ANALYTICAL

    def test_very_high_skepticism_returns_analytical(self):
        result = get_disposition_profile(_traits(skepticism=5))
        assert result is ANALYTICAL

    def test_high_empathy_low_skepticism_returns_optimistic(self):
        result = get_disposition_profile(_traits(skepticism=2, empathy=4))
        assert result is OPTIMISTIC

    def test_high_literalism_returns_conservative(self):
        result = get_disposition_profile(_traits(literalism=4))
        assert result is CONSERVATIVE

    def test_skepticism_dominates_empathy(self):
        # Both skepticism=4 and empathy=4: skepticism wins
        result = get_disposition_profile(_traits(skepticism=4, empathy=4))
        assert result is ANALYTICAL

    def test_all_low_traits_returns_neutral(self):
        result = get_disposition_profile(_traits(1, 1, 1))
        assert result is NEUTRAL


# ---------------------------------------------------------------------------
# apply_modulated_strength_delta
# ---------------------------------------------------------------------------


class TestApplyModulatedStrengthDelta:
    # --- NEUTRAL: same as apply_strength_delta ---

    def test_neutral_confirmed_plus_01(self):
        result = apply_modulated_strength_delta(0.5, +0.1, True, 0.8, NEUTRAL)
        assert result == pytest.approx(0.6)

    def test_neutral_contradiction_minus_02(self):
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.8, NEUTRAL)
        assert result == pytest.approx(0.3)

    def test_neutral_modified_no_change(self):
        result = apply_modulated_strength_delta(0.5, 0.0, False, 0.8, NEUTRAL)
        assert result == pytest.approx(0.5)

    # --- ANALYTICAL: evidence_weight=1.2 ---

    def test_analytical_confirmed_larger_delta(self):
        # 0.5 + (0.1 * 1.2) = 0.62
        result = apply_modulated_strength_delta(0.5, +0.1, True, 0.8, ANALYTICAL)
        assert result == pytest.approx(0.62)

    def test_analytical_contradiction_larger_reduction(self):
        # 0.5 + (-0.2 * 1.2) = 0.26
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.8, ANALYTICAL)
        assert result == pytest.approx(0.26)

    # --- OPTIMISTIC: evidence_weight=0.8, update_bias=+0.2 ---

    def test_optimistic_confirmed_has_positive_bias(self):
        # 0.5 + (0.1 * 0.8) + 0.2 = 0.78
        result = apply_modulated_strength_delta(0.5, +0.1, True, 0.8, OPTIMISTIC)
        assert result == pytest.approx(0.78)

    def test_optimistic_contradiction_softer(self):
        # 0.5 + (-0.2 * 0.8) = 0.34  (no bias for contradiction)
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.8, OPTIMISTIC)
        assert result == pytest.approx(0.34)

    # --- CONSERVATIVE: evidence_weight=0.8, contradiction_tolerance=0.7 ---

    def test_conservative_confirmed_softer(self):
        # 0.5 + (0.1 * 0.8) = 0.58
        result = apply_modulated_strength_delta(0.5, +0.1, True, 0.8, CONSERVATIVE)
        assert result == pytest.approx(0.58)

    def test_conservative_contradiction_suppressed_below_tolerance(self):
        # similarity=0.65 < tolerance=0.7 → strength unchanged
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.65, CONSERVATIVE)
        assert result == pytest.approx(0.5)

    def test_conservative_contradiction_applied_above_tolerance(self):
        # similarity=0.75 >= tolerance=0.7 → delta applied
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.75, CONSERVATIVE)
        assert result == pytest.approx(0.5 + (-0.2 * 0.8))

    # --- Contradiction suppression general ---

    def test_contradiction_applied_at_exact_tolerance(self):
        # similarity == tolerance: delta applied (suppression requires strictly less-than)
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.7, CONSERVATIVE)
        assert result == pytest.approx(0.5 + (-0.2 * 0.8))

    def test_neutral_never_suppresses_contradiction(self):
        # NEUTRAL tolerance=0.0, similarity=0.0: still applies delta (0.0 is NOT < 0.0)
        result = apply_modulated_strength_delta(0.5, -0.2, False, 0.0, NEUTRAL)
        assert result == pytest.approx(0.3)

    # --- Clamping ---

    def test_clamps_at_max_1_0(self):
        result = apply_modulated_strength_delta(0.95, +0.1, True, 0.8, ANALYTICAL)
        assert result == pytest.approx(1.0)

    def test_clamps_at_min_0_0(self):
        result = apply_modulated_strength_delta(0.1, -0.2, False, 0.9, ANALYTICAL)
        assert result == pytest.approx(0.0)
