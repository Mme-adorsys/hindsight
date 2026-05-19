"""
Unit tests for tag-based promote thresholds + normalized hard gates
(Epic 24 Story 04).

Covers:
- ``get_promote_threshold_for_tags`` lookup + multi-tag fairness rule.
- ``compute_min_access`` bank-size normalization + floor clamp.
- ``passes_hard_gates`` access + novelty combined check.
"""

from __future__ import annotations

from hindsight_api.engine.consolidation.scoring import (
    BASE_MIN_ACCESS_FOR_PROMOTE,
    DEFAULT_TAG_PROMOTE_THRESHOLD,
    MIN_NOVELTY_FOR_PROMOTE,
    TAG_PROMOTE_THRESHOLDS,
    compute_min_access,
    get_promote_threshold_for_tags,
    passes_hard_gates,
)

# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_tag_thresholds_keys(self) -> None:
        assert set(TAG_PROMOTE_THRESHOLDS.keys()) == {"fact", "experience", "opinion"}

    def test_tag_threshold_values(self) -> None:
        assert TAG_PROMOTE_THRESHOLDS["fact"] == 0.7
        assert TAG_PROMOTE_THRESHOLDS["experience"] == 0.35
        assert TAG_PROMOTE_THRESHOLDS["opinion"] == 0.35

    def test_default_matches_fact(self) -> None:
        # Fallback should be strict by default — behave like an unlabeled fact.
        assert DEFAULT_TAG_PROMOTE_THRESHOLD == TAG_PROMOTE_THRESHOLDS["fact"]

    def test_base_min_access_default(self) -> None:
        assert BASE_MIN_ACCESS_FOR_PROMOTE == 5


# ---------------------------------------------------------------------------
# get_promote_threshold_for_tags
# ---------------------------------------------------------------------------


class TestTagThresholdLookup:
    def test_none_tags_returns_default(self) -> None:
        assert get_promote_threshold_for_tags(None) == DEFAULT_TAG_PROMOTE_THRESHOLD

    def test_empty_tags_returns_default(self) -> None:
        assert get_promote_threshold_for_tags([]) == DEFAULT_TAG_PROMOTE_THRESHOLD

    def test_fact_returns_strict_threshold(self) -> None:
        assert get_promote_threshold_for_tags(["fact"]) == 0.7

    def test_experience_returns_lenient_threshold(self) -> None:
        assert get_promote_threshold_for_tags(["experience"]) == 0.35

    def test_opinion_returns_lenient_threshold(self) -> None:
        assert get_promote_threshold_for_tags(["opinion"]) == 0.35

    def test_unknown_tag_falls_back_to_default(self) -> None:
        assert get_promote_threshold_for_tags(["weather"]) == DEFAULT_TAG_PROMOTE_THRESHOLD

    def test_lowest_match_wins_on_multi_tag(self) -> None:
        # fact (0.7) + experience (0.35) → 0.35 (benefit of the doubt)
        assert get_promote_threshold_for_tags(["fact", "experience"]) == 0.35

    def test_unknown_tags_mixed_with_known(self) -> None:
        # Unknown tags are ignored — "fact" still applies.
        assert get_promote_threshold_for_tags(["urgent", "fact", "tech"]) == 0.7

    def test_only_unknown_tags_returns_default(self) -> None:
        assert get_promote_threshold_for_tags(["alpha", "beta", "gamma"]) == DEFAULT_TAG_PROMOTE_THRESHOLD


# ---------------------------------------------------------------------------
# compute_min_access
# ---------------------------------------------------------------------------


class TestMinAccess:
    def test_empty_bank_uses_small_bank_cap(self) -> None:
        # bank_factor(0) = SMALL_BANK_FACTOR_CAP = 1.5 → ceil(5 × 1.5) = 8
        assert compute_min_access(0) == 8

    def test_small_bank_capped_gate(self) -> None:
        # bank_factor(50) is capped at 1.5 (was 1.7572) → ceil(5 × 1.5) = 8
        assert compute_min_access(50) == 8

    def test_reference_size_returns_base(self) -> None:
        # bank_factor(1000) = 1.0 → ceil(5 × 1.0) = 5
        assert compute_min_access(1000) == 5

    def test_large_bank_lowers_gate(self) -> None:
        # bank_factor(50000) ≈ 0.6386 → ceil(5 × 0.6386) = ceil(3.193) = 4
        assert compute_min_access(50_000) == 4

    def test_huge_bank_clamps_to_floor(self) -> None:
        # Extreme end: bank_factor(10^18) ≈ log(1001)/log(10^18) ≈ 6.908/41.45 ≈ 0.167
        # → raw = 5 × 0.167 ≈ 0.83 → ceil(0.83) = 1, floor holds.
        assert compute_min_access(10**18) == 1

    def test_custom_base_scales_linearly(self) -> None:
        # base=10, bank_factor(1000)=1.0 → ceil(10) = 10
        assert compute_min_access(1000, base=10) == 10

    def test_result_is_always_integer(self) -> None:
        for size in (1, 10, 50, 100, 500, 1000, 5000, 50000):
            result = compute_min_access(size)
            assert isinstance(result, int)
            assert result >= 1


# ---------------------------------------------------------------------------
# passes_hard_gates
# ---------------------------------------------------------------------------


class TestHardGates:
    def test_both_gates_pass(self) -> None:
        # bank_size=1000 → min_access=5; access=5, novelty=0.3 → both pass
        assert passes_hard_gates(access_count=5, novelty=0.3, bank_size=1000) is True

    def test_fails_on_access_gate(self) -> None:
        # bank_size=1000 → min_access=5; access=4 fails even with high novelty
        assert passes_hard_gates(access_count=4, novelty=0.9, bank_size=1000) is False

    def test_fails_on_novelty_gate(self) -> None:
        # novelty below MIN_NOVELTY_FOR_PROMOTE (0.2) → fails
        assert passes_hard_gates(access_count=10, novelty=0.1, bank_size=1000) is False

    def test_small_bank_requires_higher_access(self) -> None:
        # bank_size=50 → min_access=8 (capped via SMALL_BANK_FACTOR_CAP);
        # access=5 would have passed for size=1000 but is too low here.
        assert passes_hard_gates(access_count=5, novelty=0.5, bank_size=50) is False
        assert passes_hard_gates(access_count=8, novelty=0.5, bank_size=50) is True

    def test_large_bank_accepts_lower_access(self) -> None:
        # bank_size=50000 → min_access=4; access=4 is enough
        assert passes_hard_gates(access_count=4, novelty=0.5, bank_size=50_000) is True
        assert passes_hard_gates(access_count=3, novelty=0.5, bank_size=50_000) is False

    def test_novelty_threshold_boundary(self) -> None:
        # novelty exactly at the gate passes
        assert passes_hard_gates(access_count=5, novelty=MIN_NOVELTY_FOR_PROMOTE, bank_size=1000) is True
        # one epsilon below fails
        assert passes_hard_gates(access_count=5, novelty=MIN_NOVELTY_FOR_PROMOTE - 1e-9, bank_size=1000) is False

    def test_access_threshold_boundary(self) -> None:
        # access == min_access passes; one below fails
        min_acc = compute_min_access(1000)
        assert passes_hard_gates(access_count=min_acc, novelty=0.5, bank_size=1000) is True
        assert passes_hard_gates(access_count=min_acc - 1, novelty=0.5, bank_size=1000) is False

    def test_empty_bank_requires_capped_access(self) -> None:
        # bank_size=0 → min_access=8 (SMALL_BANK_FACTOR_CAP × BASE).
        # access=8 passes; access=7 fails.
        assert passes_hard_gates(access_count=8, novelty=0.5, bank_size=0) is True
        assert passes_hard_gates(access_count=7, novelty=0.5, bank_size=0) is False
