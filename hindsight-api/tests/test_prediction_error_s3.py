"""
Unit tests for Epic 11 Story 03 — Prediction Error Detection & Feedback.

Covers:
- T1/T2 PredictionErrorDetector.detect(): error on deviation, no error on match, severity clamp
- T2 PredictionError dataclass: level classification, triggers_mode_shift property
- T3 apply_prediction_error_feedback: mode shift triggered ≥ 0.3, blocked below 0.3
- T4 Reconsolidation flagging: conflicting_fact_ids → PE registry
- T5 Integration: no check without current_expectation, no check with empty facts
- T6 Severity classification thresholds
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.constructive.models import ConstructedAnswer, ConstructedFact
from hindsight_api.engine.constructive.prediction_error import (
    SEVERITY_MINOR_MAX,
    SEVERITY_MODERATE_MAX,
    PredictionError,
    PredictionErrorDetector,
    _PEResponse,
    apply_prediction_error_feedback,
)
from hindsight_api.engine.reflect.prediction_error_registry import PredictionErrorRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(engram_id: str = "e-1", content: str = "Fact text.") -> ConstructedFact:
    return ConstructedFact(engram_id=engram_id, content=content, confidence=0.8, source="direct")


def _answer(*facts: ConstructedFact) -> ConstructedAnswer:
    return ConstructedAnswer(facts=list(facts))


def _llm_returning(response: _PEResponse) -> MagicMock:
    llm = MagicMock()
    llm.call = AsyncMock(return_value=response)
    return llm


def _detector(response: _PEResponse) -> PredictionErrorDetector:
    return PredictionErrorDetector(llm=_llm_returning(response))


# ---------------------------------------------------------------------------
# T2 — PredictionError dataclass
# ---------------------------------------------------------------------------


class TestPredictionErrorDataclass:
    def test_level_minor(self):
        pe = PredictionError(severity=0.1, description="small diff")
        assert pe.level == "minor"

    def test_level_minor_at_boundary(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX - 0.01, description="x")
        assert pe.level == "minor"

    def test_level_moderate_at_threshold(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX, description="x")
        assert pe.level == "moderate"

    def test_level_moderate_mid(self):
        pe = PredictionError(severity=0.5, description="x")
        assert pe.level == "moderate"

    def test_level_major_at_threshold(self):
        pe = PredictionError(severity=SEVERITY_MODERATE_MAX, description="x")
        assert pe.level == "major"

    def test_level_major_high(self):
        pe = PredictionError(severity=0.9, description="x")
        assert pe.level == "major"

    def test_triggers_mode_shift_true_at_threshold(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX, description="x")
        assert pe.triggers_mode_shift is True

    def test_triggers_mode_shift_false_below_threshold(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX - 0.01, description="x")
        assert pe.triggers_mode_shift is False

    def test_conflicting_fact_ids_default_empty(self):
        pe = PredictionError(severity=0.5, description="x")
        assert pe.conflicting_fact_ids == []

    def test_summaries_default_empty(self):
        pe = PredictionError(severity=0.5, description="x")
        assert pe.expected_summary == ""
        assert pe.actual_summary == ""


# ---------------------------------------------------------------------------
# T1 — PredictionErrorDetector.detect()
# ---------------------------------------------------------------------------


class TestPredictionErrorDetector:
    @pytest.mark.asyncio
    async def test_no_error_when_has_error_false(self):
        resp = _PEResponse(has_error=False, severity=0.5)
        result = await _detector(resp).detect(_answer(_fact()), "expectation")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_error_when_severity_at_threshold(self):
        # severity <= 0.1 is treated as "no meaningful error"
        resp = _PEResponse(has_error=True, severity=0.1)
        result = await _detector(resp).detect(_answer(_fact()), "expectation")
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returned_on_deviation(self):
        resp = _PEResponse(
            has_error=True,
            severity=0.6,
            description="Answer contradicts expectation.",
            conflicting_fact_indices=[0],
            expected_summary="X should be true",
            actual_summary="X is false",
        )
        result = await _detector(resp).detect(_answer(_fact("e-1")), "X should be true")
        assert result is not None
        assert result.severity == pytest.approx(0.6)
        assert result.description == "Answer contradicts expectation."
        assert result.conflicting_fact_ids == ["e-1"]
        assert result.expected_summary == "X should be true"
        assert result.actual_summary == "X is false"

    @pytest.mark.asyncio
    async def test_out_of_range_indices_ignored(self):
        resp = _PEResponse(has_error=True, severity=0.5, conflicting_fact_indices=[0, 99])
        result = await _detector(resp).detect(_answer(_fact("e-1")), "expectation")
        assert result is not None
        assert result.conflicting_fact_ids == ["e-1"]

    @pytest.mark.asyncio
    async def test_severity_clamped_to_one(self):
        # _PEResponse validates ge/le, so build a scenario with max valid value
        resp = _PEResponse(has_error=True, severity=1.0, description="d")
        result = await _detector(resp).detect(_answer(_fact()), "expectation")
        assert result is not None
        assert result.severity <= 1.0

    @pytest.mark.asyncio
    async def test_no_check_with_empty_expectation(self):
        resp = _PEResponse(has_error=True, severity=0.8)
        result = await _detector(resp).detect(_answer(_fact()), "")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_check_with_no_facts(self):
        resp = _PEResponse(has_error=True, severity=0.8)
        result = await _detector(resp).detect(ConstructedAnswer(), "some expectation")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_not_called_when_no_expectation(self):
        llm = MagicMock()
        llm.call = AsyncMock()
        detector = PredictionErrorDetector(llm=llm)
        await detector.detect(_answer(_fact()), "")
        llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_not_called_when_no_facts(self):
        llm = MagicMock()
        llm.call = AsyncMock()
        detector = PredictionErrorDetector(llm=llm)
        await detector.detect(ConstructedAnswer(), "some expectation")
        llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# T3 — Mode shift feedback
# ---------------------------------------------------------------------------


class TestModeShiftFeedback:
    @pytest.mark.asyncio
    async def test_mode_shift_triggered_at_threshold(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX, description="d")
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        session_id = uuid4()
        await apply_prediction_error_feedback(pe, session_id, sm, registry)
        sm.process_signal.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mode_shift_not_triggered_below_threshold(self):
        pe = PredictionError(severity=SEVERITY_MINOR_MAX - 0.01, description="d")
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        session_id = uuid4()
        await apply_prediction_error_feedback(pe, session_id, sm, registry)
        sm.process_signal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mode_signal_is_prediction_error(self):
        from hindsight_api.engine.session.session_manager import ModeSignal

        pe = PredictionError(severity=0.5, description="d")
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        session_id = uuid4()
        await apply_prediction_error_feedback(pe, session_id, sm, registry)

        _, call_args = sm.process_signal.await_args
        # process_signal(session_id, ModeSignal.PREDICTION_ERROR)
        positional = sm.process_signal.call_args.args
        assert positional[1] == ModeSignal.PREDICTION_ERROR


# ---------------------------------------------------------------------------
# T4 — Reconsolidation flagging
# ---------------------------------------------------------------------------


class TestReconsolidationFlagging:
    @pytest.mark.asyncio
    async def test_conflicting_engrams_flagged(self):
        pe = PredictionError(
            severity=0.5,
            description="d",
            conflicting_fact_ids=["e-1", "e-2"],
        )
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        await apply_prediction_error_feedback(pe, uuid4(), sm, registry)
        assert registry.is_flagged("e-1")
        assert registry.is_flagged("e-2")

    @pytest.mark.asyncio
    async def test_no_engrams_flagged_when_empty(self):
        pe = PredictionError(severity=0.5, description="d", conflicting_fact_ids=[])
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        await apply_prediction_error_feedback(pe, uuid4(), sm, registry)
        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_flagged_even_without_mode_shift(self):
        """Engrams should be flagged regardless of whether mode shift triggered."""
        pe = PredictionError(
            severity=0.1,  # below mode-shift threshold
            description="d",
            conflicting_fact_ids=["e-3"],
        )
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        await apply_prediction_error_feedback(pe, uuid4(), sm, registry)
        assert registry.is_flagged("e-3")

    @pytest.mark.asyncio
    async def test_registry_context_contains_severity(self):
        pe = PredictionError(
            severity=0.6,
            description="Big mismatch",
            conflicting_fact_ids=["e-5"],
        )
        sm = MagicMock()
        sm.process_signal = AsyncMock(return_value=True)
        registry = PredictionErrorRegistry()

        from uuid import uuid4

        await apply_prediction_error_feedback(pe, uuid4(), sm, registry)
        entry = registry.get_entry("e-5")
        assert entry is not None
        assert "0.60" in entry.error_context


# ---------------------------------------------------------------------------
# T5 — No-check scenarios (covered via detector tests above)
# T6 — Severity classification (covered by TestPredictionErrorDataclass)
# ---------------------------------------------------------------------------


class TestSeverityThresholds:
    """Explicit boundary tests for all three severity levels."""

    def test_minor_boundary(self):
        assert PredictionError(severity=0.0, description="x").level == "minor"
        assert PredictionError(severity=0.29, description="x").level == "minor"

    def test_moderate_boundary(self):
        assert PredictionError(severity=0.3, description="x").level == "moderate"
        assert PredictionError(severity=0.69, description="x").level == "moderate"

    def test_major_boundary(self):
        assert PredictionError(severity=0.7, description="x").level == "major"
        assert PredictionError(severity=1.0, description="x").level == "major"
