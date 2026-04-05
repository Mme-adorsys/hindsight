"""
Unit tests for Epic 10 Story 01 — Priority-basierte Engram Selection (RF1).

Covers:
- PredictionErrorRegistry: flag, is_flagged, get_flagged_ids, clear, len, bool
- build_reconsolidation_queue: PE priority, weak-first ordering, rest ordering
- build_reconsolidation_queue: budget caps per reconsolidation_level
- build_reconsolidation_queue: all Engram types included (not only opinions)
- apply_strength_delta: confirmed/modified/contradiction outcomes, clamping
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.reflect.prediction_error_registry import (
    PredictionErrorEntry,
    PredictionErrorRegistry,
)
from hindsight_api.engine.reflect.reconsolidation_queue import (
    LEVEL_BUDGET,
    WEAK_STRENGTH_THRESHOLD,
    ReconsolidationOutcome,
    apply_strength_delta,
    build_reconsolidation_queue,
)
from hindsight_api.engine.response_models import Engram, EngramMetadata, FullEngram
from hindsight_api.engine.session.mode_config import MODE_PROFILES, ModeConfig
from hindsight_api.engine.session.session_manager import RetrievalMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(strength: float, last_accessed: datetime | None = None) -> EngramMetadata:
    return EngramMetadata(
        engram_id=uuid4(),
        bank_id="test-bank",
        strength=strength,
        last_accessed=last_accessed,
    )


def _engram(
    strength: float = 0.5,
    last_accessed: datetime | None = None,
    engram_id: UUID | None = None,
) -> FullEngram:
    eid = engram_id or uuid4()
    return FullEngram(
        engram_id=eid,
        metadata=_meta(strength=strength, last_accessed=last_accessed),
    )


def _mode(level: str) -> ModeConfig:
    """Return a ModeConfig with the given reconsolidation_level."""
    base = MODE_PROFILES[RetrievalMode.PRECISION]
    return base.with_overrides(reconsolidation_level=level)


_T1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
_T3 = datetime(2026, 3, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# PredictionErrorRegistry
# ---------------------------------------------------------------------------


class TestPredictionErrorRegistry:
    def test_empty_registry_is_falsy(self):
        reg = PredictionErrorRegistry()
        assert not reg
        assert len(reg) == 0

    def test_flag_makes_registry_truthy(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "context")
        assert reg
        assert len(reg) == 1

    def test_is_flagged_true_after_flag(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "ctx")
        assert reg.is_flagged("e-1")

    def test_is_flagged_false_for_unknown(self):
        reg = PredictionErrorRegistry()
        assert not reg.is_flagged("e-999")

    def test_get_flagged_ids_returns_all(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "a")
        reg.flag_prediction_error("e-2", "b")
        assert reg.get_flagged_ids() == {"e-1", "e-2"}

    def test_flag_same_id_twice_overwrites_context(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "first")
        reg.flag_prediction_error("e-1", "second")
        assert len(reg) == 1
        entry = reg.get_entry("e-1")
        assert entry is not None
        assert entry.error_context == "second"

    def test_get_entry_returns_none_for_unknown(self):
        reg = PredictionErrorRegistry()
        assert reg.get_entry("x") is None

    def test_get_entry_returns_prediction_error_entry(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "ctx")
        entry = reg.get_entry("e-1")
        assert isinstance(entry, PredictionErrorEntry)
        assert entry.engram_id == "e-1"

    def test_clear_empties_registry(self):
        reg = PredictionErrorRegistry()
        reg.flag_prediction_error("e-1", "ctx")
        reg.clear()
        assert not reg
        assert not reg.is_flagged("e-1")

    def test_clear_on_empty_is_no_op(self):
        reg = PredictionErrorRegistry()
        reg.clear()  # must not raise
        assert len(reg) == 0


# ---------------------------------------------------------------------------
# build_reconsolidation_queue — ordering
# ---------------------------------------------------------------------------


class TestReconsolidationQueueOrdering:
    def test_pe_engrams_come_first(self):
        pe_id = uuid4()
        pe_eng = _engram(strength=0.8, engram_id=pe_id)  # strong, but PE-flagged
        weak_eng = _engram(strength=0.1)
        strong_eng = _engram(strength=0.9)

        reg = PredictionErrorRegistry()
        reg.flag_prediction_error(str(pe_id), "error")

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([strong_eng, weak_eng, pe_eng], reg, mode)

        assert result[0].engram_id == pe_id

    def test_weak_engrams_before_rest(self):
        weak_eng = _engram(strength=0.1, last_accessed=_T3)
        strong_eng = _engram(strength=0.9, last_accessed=_T1)

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([strong_eng, weak_eng], None, mode)

        assert result[0].metadata.strength < WEAK_STRENGTH_THRESHOLD
        assert result[1].metadata.strength >= WEAK_STRENGTH_THRESHOLD

    def test_weak_engrams_sorted_by_strength_ascending(self):
        w1 = _engram(strength=0.05)
        w2 = _engram(strength=0.20)
        w3 = _engram(strength=0.29)

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([w3, w1, w2], None, mode)

        strengths = [r.metadata.strength for r in result]
        assert strengths == sorted(strengths)

    def test_rest_sorted_by_last_accessed_ascending(self):
        e1 = _engram(strength=0.5, last_accessed=_T3)
        e2 = _engram(strength=0.5, last_accessed=_T1)
        e3 = _engram(strength=0.5, last_accessed=_T2)

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([e1, e2, e3], None, mode)

        assert result[0].metadata.last_accessed == _T1
        assert result[1].metadata.last_accessed == _T2
        assert result[2].metadata.last_accessed == _T3

    def test_none_last_accessed_sorts_first_within_tier(self):
        # None = never accessed → treated as epoch (1970-01-01) → stalest → reconsolidated first
        e_no_access = _engram(strength=0.5, last_accessed=None)
        e_old = _engram(strength=0.5, last_accessed=_T1)

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([e_no_access, e_old], None, mode)

        assert result[0].metadata.last_accessed is None
        assert result[1].metadata.last_accessed == _T1

    def test_all_three_tiers_in_correct_order(self):
        pe_id = uuid4()
        pe_eng = _engram(strength=0.9, engram_id=pe_id)
        weak_eng = _engram(strength=0.1)
        rest_eng = _engram(strength=0.6)

        reg = PredictionErrorRegistry()
        reg.flag_prediction_error(str(pe_id), "ctx")

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([rest_eng, weak_eng, pe_eng], reg, mode)

        assert result[0].engram_id == pe_id
        assert result[1].metadata.strength < WEAK_STRENGTH_THRESHOLD
        assert result[2].metadata.strength >= WEAK_STRENGTH_THRESHOLD


# ---------------------------------------------------------------------------
# build_reconsolidation_queue — budget limits
# ---------------------------------------------------------------------------


class TestReconsolidationQueueBudget:
    def _many_engrams(self, n: int) -> list[FullEngram]:
        return [_engram(strength=0.5) for _ in range(n)]

    def test_minimal_budget_is_5(self):
        engrams = self._many_engrams(20)
        result = build_reconsolidation_queue(engrams, None, _mode("minimal"))
        assert len(result) == LEVEL_BUDGET["minimal"] == 5

    def test_moderate_budget_is_15(self):
        engrams = self._many_engrams(30)
        result = build_reconsolidation_queue(engrams, None, _mode("moderate"))
        assert len(result) == LEVEL_BUDGET["moderate"] == 15

    def test_schema_update_budget_is_25(self):
        engrams = self._many_engrams(40)
        result = build_reconsolidation_queue(engrams, None, _mode("schema_update"))
        assert len(result) == LEVEL_BUDGET["schema_update"] == 25

    def test_aggressive_budget_is_50(self):
        engrams = self._many_engrams(100)
        result = build_reconsolidation_queue(engrams, None, _mode("aggressive"))
        assert len(result) == LEVEL_BUDGET["aggressive"] == 50

    def test_fewer_engrams_than_budget_returns_all(self):
        engrams = self._many_engrams(3)
        result = build_reconsolidation_queue(engrams, None, _mode("aggressive"))
        assert len(result) == 3

    def test_empty_input_returns_empty(self):
        result = build_reconsolidation_queue([], None, _mode("aggressive"))
        assert result == []

    def test_none_registry_treated_as_empty(self):
        engrams = self._many_engrams(5)
        result = build_reconsolidation_queue(engrams, None, _mode("aggressive"))
        assert len(result) == 5


# ---------------------------------------------------------------------------
# build_reconsolidation_queue — all Engram types included
# ---------------------------------------------------------------------------


class TestAllEngramTypesIncluded:
    def test_engrams_without_tags_included(self):
        """No tag/fact_type filter — all Engrams are candidates."""
        e1 = _engram(strength=0.5)
        e2 = _engram(strength=0.2)
        e3 = _engram(strength=0.8)

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([e1, e2, e3], None, mode)
        assert len(result) == 3

    def test_pe_engrams_included_regardless_of_strength(self):
        """PE-flagged Engrams are included even if strong."""
        pe_id = uuid4()
        pe_eng = _engram(strength=1.0, engram_id=pe_id)

        reg = PredictionErrorRegistry()
        reg.flag_prediction_error(str(pe_id), "contradiction detected")

        mode = _mode("aggressive")
        result = build_reconsolidation_queue([pe_eng], reg, mode)
        assert len(result) == 1
        assert result[0].engram_id == pe_id

    def test_engram_with_none_metadata_strength_treated_as_zero(self):
        """FullEngram with metadata=None → strength defaults to 0.0 (weak tier)."""
        e = FullEngram(engram_id=uuid4(), metadata=None)
        mode = _mode("aggressive")
        result = build_reconsolidation_queue([e], None, mode)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# apply_strength_delta
# ---------------------------------------------------------------------------


class TestApplyStrengthDelta:
    def test_confirmed_increases_by_01(self):
        assert apply_strength_delta(0.5, ReconsolidationOutcome.CONFIRMED) == pytest.approx(0.6)

    def test_modified_leaves_strength_unchanged(self):
        assert apply_strength_delta(0.5, ReconsolidationOutcome.MODIFIED) == pytest.approx(0.5)

    def test_contradiction_decreases_by_02(self):
        assert apply_strength_delta(0.5, ReconsolidationOutcome.CONTRADICTION) == pytest.approx(0.3)

    def test_confirmed_clamps_at_1_0(self):
        assert apply_strength_delta(0.95, ReconsolidationOutcome.CONFIRMED) == pytest.approx(1.0)

    def test_contradiction_clamps_at_0_0(self):
        assert apply_strength_delta(0.1, ReconsolidationOutcome.CONTRADICTION) == pytest.approx(0.0)

    def test_contradiction_never_goes_negative(self):
        result = apply_strength_delta(0.0, ReconsolidationOutcome.CONTRADICTION)
        assert result == pytest.approx(0.0)

    def test_confirmed_at_zero_becomes_01(self):
        assert apply_strength_delta(0.0, ReconsolidationOutcome.CONFIRMED) == pytest.approx(0.1)
