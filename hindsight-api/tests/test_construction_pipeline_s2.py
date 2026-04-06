"""
Unit tests for Epic 11 Story 02 — Construction Pipeline.

Covers:
- T2 Fact Extraction: ScoredResult → ConstructedFact (weak-link penalty, source classification)
- T5 Confidence Aggregation: weighted mean × coverage factor
- T3 Inference Phase: mode-specific prompts, valid/invalid types, supporting IDs
- T4 Gap Detection: gaps → Gap objects with related engram IDs
- T6 Working Context Update: confirmed inferences pushed to inference_layer
- T7 Integration smoke tests: construct() end-to-end with mocked LLM
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.constructive.models import (
    ConstructedAnswer,
    ConstructedFact,
    Gap,
    Inference,
)
from hindsight_api.engine.constructive.pipeline import (
    DIRECT_SOURCE_THRESHOLD,
    INFERENCE_CONFIRMATION_THRESHOLD,
    WEAK_LINK_CONFIDENCE_PENALTY,
    ConstructionPipeline,
    _GapItem,
    _GapList,
    _InferenceItem,
    _InferenceList,
)


# ---------------------------------------------------------------------------
# Helpers — fake ScoredResult
# ---------------------------------------------------------------------------


def _scored_result(
    engram_id: str = "e-1",
    combined_score: float = 0.8,
    text: str = "Fact text.",
    traversal_source: str = "direct",
) -> MagicMock:
    """Build a minimal fake ScoredResult."""
    sr = MagicMock()
    sr.id = engram_id
    sr.combined_score = combined_score
    sr.retrieval = SimpleNamespace(text=text, traversal_source=traversal_source)
    return sr


def _make_pipeline(style: str = "conservative") -> ConstructionPipeline:
    """Pipeline with a mocked LLM and ModeConfig."""
    llm = MagicMock()
    mode_config = MagicMock()
    mode_config.construction_style = style
    return ConstructionPipeline(llm=llm, mode_config=mode_config)


# ---------------------------------------------------------------------------
# T2 — Fact Extraction
# ---------------------------------------------------------------------------


class TestFactExtraction:
    def test_basic_extraction(self):
        p = _make_pipeline()
        facts = p._extract_facts([_scored_result("e-1", 0.9, "Hello.")])
        assert len(facts) == 1
        assert facts[0].engram_id == "e-1"
        assert facts[0].content == "Hello."
        assert facts[0].confidence == pytest.approx(0.9)

    def test_direct_source_above_threshold(self):
        p = _make_pipeline()
        facts = p._extract_facts([_scored_result(combined_score=DIRECT_SOURCE_THRESHOLD + 0.1)])
        assert facts[0].source == "direct"

    def test_reconstructed_source_below_threshold(self):
        p = _make_pipeline()
        facts = p._extract_facts([_scored_result(combined_score=DIRECT_SOURCE_THRESHOLD - 0.01)])
        assert facts[0].source == "reconstructed"

    def test_reconstructed_source_at_threshold(self):
        # At exactly threshold → direct (confidence >= threshold)
        p = _make_pipeline()
        facts = p._extract_facts([_scored_result(combined_score=DIRECT_SOURCE_THRESHOLD)])
        assert facts[0].source == "direct"

    def test_weak_link_penalty_applied(self):
        p = _make_pipeline()
        score = 0.9
        facts = p._extract_facts([_scored_result(combined_score=score, traversal_source="weak_link")])
        expected = score * WEAK_LINK_CONFIDENCE_PENALTY
        assert facts[0].confidence == pytest.approx(expected)

    def test_weak_link_penalty_not_applied_for_direct(self):
        p = _make_pipeline()
        score = 0.9
        facts = p._extract_facts([_scored_result(combined_score=score, traversal_source="direct")])
        assert facts[0].confidence == pytest.approx(score)

    def test_confidence_clamped_to_one(self):
        p = _make_pipeline()
        # combined_score already validated by pydantic upstream; simulate edge case
        sr = _scored_result(combined_score=0.99)
        sr.combined_score = 1.05  # override after creation
        facts = p._extract_facts([sr])
        assert facts[0].confidence <= 1.0

    def test_confidence_clamped_to_zero(self):
        p = _make_pipeline()
        sr = _scored_result(combined_score=0.01)
        sr.combined_score = -0.1
        facts = p._extract_facts([sr])
        assert facts[0].confidence >= 0.0

    def test_empty_text_becomes_empty_string(self):
        p = _make_pipeline()
        sr = _scored_result()
        sr.retrieval.text = None
        facts = p._extract_facts([sr])
        assert facts[0].content == ""

    def test_multiple_results_preserved(self):
        p = _make_pipeline()
        srs = [_scored_result(f"e-{i}", 0.5 + i * 0.1) for i in range(4)]
        facts = p._extract_facts(srs)
        assert len(facts) == 4
        assert [f.engram_id for f in facts] == ["e-0", "e-1", "e-2", "e-3"]

    def test_thalamus_score_attached_when_present(self):
        p = _make_pipeline()
        sr = _scored_result("e-1", 0.8)
        sr.thalamus_score = 0.75
        facts = p._extract_facts([sr])
        assert facts[0].thalamus_scores.get("thalamus_composite") == pytest.approx(0.75)

    def test_no_thalamus_score_when_absent(self):
        p = _make_pipeline()
        sr = _scored_result("e-1", 0.8)
        # No thalamus_score attribute → empty dict
        del sr.thalamus_score
        facts = p._extract_facts([sr])
        assert facts[0].thalamus_scores == {}


# ---------------------------------------------------------------------------
# T5 — Confidence Aggregation
# ---------------------------------------------------------------------------


def _fact(confidence: float, engram_id: str = "e") -> ConstructedFact:
    return ConstructedFact(engram_id=engram_id, content="x", confidence=confidence, source="direct")


def _gap(relevance: float) -> Gap:
    return Gap(description="missing", relevance=relevance)


class TestConfidenceAggregation:
    def test_empty_facts_returns_zero(self):
        assert ConstructionPipeline._aggregate_confidence([], []) == pytest.approx(0.0)

    def test_single_fact_no_gaps(self):
        result = ConstructionPipeline._aggregate_confidence([_fact(0.8)], [])
        assert result == pytest.approx(0.8)

    def test_multiple_facts_mean(self):
        facts = [_fact(0.6), _fact(0.8)]
        result = ConstructionPipeline._aggregate_confidence(facts, [])
        assert result == pytest.approx(0.7)

    def test_gap_below_threshold_no_penalty(self):
        result = ConstructionPipeline._aggregate_confidence([_fact(0.8)], [_gap(0.2)])
        assert result == pytest.approx(0.8)

    def test_gap_above_threshold_reduces_confidence(self):
        result = ConstructionPipeline._aggregate_confidence([_fact(0.8)], [_gap(0.5)])
        # coverage = max(0.5, 1.0 - 0.5*0.15) = max(0.5, 0.925) = 0.925
        assert result == pytest.approx(0.8 * 0.925)

    def test_multiple_gaps_cumulative_penalty(self):
        gaps = [_gap(0.5), _gap(0.4)]
        result = ConstructionPipeline._aggregate_confidence([_fact(0.8)], gaps)
        # coverage = max(0.5, 1.0 - (0.5+0.4)*0.15) = max(0.5, 1-0.135) = 0.865
        assert result == pytest.approx(0.8 * 0.865)

    def test_coverage_factor_floor_at_half(self):
        # Many high-relevance gaps → coverage should not go below 0.5
        gaps = [_gap(1.0) for _ in range(10)]
        result = ConstructionPipeline._aggregate_confidence([_fact(0.8)], gaps)
        assert result >= 0.8 * 0.5

    def test_overall_confidence_clamped_to_one(self):
        result = ConstructionPipeline._aggregate_confidence([_fact(1.0)], [])
        assert result <= 1.0


# ---------------------------------------------------------------------------
# T3 — Inference Phase (mocked LLM)
# ---------------------------------------------------------------------------


def _llm_returning_inferences(items: list[_InferenceItem]) -> MagicMock:
    llm = MagicMock()
    llm.call = AsyncMock(return_value=_InferenceList(inferences=items))
    return llm


class TestInferencePhase:
    @pytest.mark.asyncio
    async def test_basic_inference_returned(self):
        item = _InferenceItem(
            content="Therefore B.",
            confidence=0.75,
            inference_type="deduction",
            reasoning="A implies B",
            supporting_indices=[0],
        )
        p = ConstructionPipeline(llm=_llm_returning_inferences([item]), mode_config=MagicMock(construction_style="conservative"))
        facts = [_fact(0.8, "e-1")]
        inferences = await p._run_inference_phase(facts, "test query", None, "conservative")
        assert len(inferences) == 1
        assert inferences[0].content == "Therefore B."
        assert inferences[0].confidence == pytest.approx(0.75)
        assert inferences[0].inference_type == "deduction"
        assert inferences[0].supporting_engram_ids == ["e-1"]

    @pytest.mark.asyncio
    async def test_invalid_inference_type_replaced_by_type_hint(self):
        item = _InferenceItem(
            content="X",
            confidence=0.6,
            inference_type="speculation",  # invalid
            reasoning="r",
        )
        p = ConstructionPipeline(llm=_llm_returning_inferences([item]), mode_config=MagicMock(construction_style="creative"))
        facts = [_fact(0.8, "e-1")]
        inferences = await p._run_inference_phase(facts, "q", None, "creative")
        # creative → type_hint = "extrapolation"
        assert inferences[0].inference_type == "extrapolation"

    @pytest.mark.asyncio
    async def test_out_of_range_supporting_indices_ignored(self):
        item = _InferenceItem(
            content="X",
            confidence=0.5,
            inference_type="deduction",
            reasoning="r",
            supporting_indices=[0, 99],  # 99 out of range
        )
        p = ConstructionPipeline(llm=_llm_returning_inferences([item]), mode_config=MagicMock(construction_style="conservative"))
        facts = [_fact(0.8, "e-1")]
        inferences = await p._run_inference_phase(facts, "q", None, "conservative")
        assert inferences[0].supporting_engram_ids == ["e-1"]  # 99 ignored

    @pytest.mark.asyncio
    async def test_empty_inference_list_ok(self):
        p = ConstructionPipeline(llm=_llm_returning_inferences([]), mode_config=MagicMock(construction_style="conservative"))
        facts = [_fact(0.8)]
        inferences = await p._run_inference_phase(facts, "q", None, "conservative")
        assert inferences == []

    @pytest.mark.asyncio
    async def test_confidence_clamped(self):
        # _InferenceItem validates 0..1 — pipeline additionally clamps with max(0, min(1, x))
        # Test that a valid high-boundary value is preserved (not clamped away)
        item = _InferenceItem(content="X", confidence=1.0, inference_type="deduction", reasoning="r")
        p = ConstructionPipeline(llm=_llm_returning_inferences([item]), mode_config=MagicMock(construction_style="conservative"))
        inferences = await p._run_inference_phase([_fact(0.8)], "q", None, "conservative")
        assert inferences[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# T4 — Gap Detection Phase (mocked LLM)
# ---------------------------------------------------------------------------


def _llm_returning_gaps(items: list[_GapItem]) -> MagicMock:
    llm = MagicMock()
    llm.call = AsyncMock(return_value=_GapList(gaps=items))
    return llm


class TestGapDetection:
    @pytest.mark.asyncio
    async def test_basic_gap_returned(self):
        item = _GapItem(description="Missing data on Y.", relevance=0.6, related_fact_indices=[0])
        p = ConstructionPipeline(llm=_llm_returning_gaps([item]), mode_config=MagicMock(construction_style="conservative"))
        facts = [_fact(0.8, "e-1")]
        gaps = await p._run_gap_detection(facts, [], "query")
        assert len(gaps) == 1
        assert gaps[0].description == "Missing data on Y."
        assert gaps[0].relevance == pytest.approx(0.6)
        assert gaps[0].related_engram_ids == ["e-1"]

    @pytest.mark.asyncio
    async def test_suggested_query_preserved(self):
        item = _GapItem(description="Y", relevance=0.5, suggested_query="Search for Y")
        p = ConstructionPipeline(llm=_llm_returning_gaps([item]), mode_config=MagicMock(construction_style="conservative"))
        gaps = await p._run_gap_detection([], [], "query")
        assert gaps[0].suggested_query == "Search for Y"

    @pytest.mark.asyncio
    async def test_out_of_range_indices_ignored(self):
        item = _GapItem(description="Y", relevance=0.5, related_fact_indices=[0, 50])
        p = ConstructionPipeline(llm=_llm_returning_gaps([item]), mode_config=MagicMock(construction_style="conservative"))
        facts = [_fact(0.8, "e-1")]
        gaps = await p._run_gap_detection(facts, [], "query")
        assert gaps[0].related_engram_ids == ["e-1"]

    @pytest.mark.asyncio
    async def test_relevance_clamped(self):
        # _GapItem validates 0..1 — pipeline additionally clamps with max(0, min(1, x))
        # Test that a valid high-boundary value is preserved (not clamped away)
        item = _GapItem(description="Y", relevance=1.0)
        p = ConstructionPipeline(llm=_llm_returning_gaps([item]), mode_config=MagicMock(construction_style="conservative"))
        gaps = await p._run_gap_detection([], [], "query")
        assert gaps[0].relevance <= 1.0


# ---------------------------------------------------------------------------
# T6 — Working Context Update
# ---------------------------------------------------------------------------


def _inference_obj(confidence: float) -> Inference:
    return Inference(
        content=f"Inference at {confidence}",
        confidence=confidence,
        inference_type="deduction",
        reasoning="test",
    )


class TestWorkingContextUpdate:
    def test_confirmed_inference_pushed(self):
        p = _make_pipeline()
        wc = MagicMock()
        wc.inference_layer = []
        inferences = [_inference_obj(INFERENCE_CONFIRMATION_THRESHOLD)]
        p._update_working_context(inferences, [], wc)
        assert len(wc.inference_layer) == 1

    def test_unconfirmed_inference_not_pushed(self):
        p = _make_pipeline()
        wc = MagicMock()
        wc.inference_layer = []
        inferences = [_inference_obj(INFERENCE_CONFIRMATION_THRESHOLD - 0.01)]
        p._update_working_context(inferences, [], wc)
        assert len(wc.inference_layer) == 0

    def test_multiple_inferences_filtered_correctly(self):
        p = _make_pipeline()
        wc = MagicMock()
        wc.inference_layer = []
        inferences = [
            _inference_obj(0.9),   # confirmed
            _inference_obj(0.5),   # not confirmed
            _inference_obj(0.75),  # confirmed
        ]
        p._update_working_context(inferences, [], wc)
        assert len(wc.inference_layer) == 2

    def test_wc_inference_has_confirmed_status(self):
        p = _make_pipeline()
        wc = MagicMock()
        wc.inference_layer = []
        p._update_working_context([_inference_obj(0.9)], [], wc)
        pushed = wc.inference_layer[0]
        assert pushed.status == "confirmed"
        assert pushed.content == "Inference at 0.9"


# ---------------------------------------------------------------------------
# T3/T7 — construct() end-to-end with mocked LLM
# ---------------------------------------------------------------------------


def _make_pipeline_with_llm(
    inference_items: list[_InferenceItem] | None = None,
    gap_items: list[_GapItem] | None = None,
    style: str = "conservative",
) -> ConstructionPipeline:
    """Pipeline with LLM mock returning specified items."""
    inference_items = inference_items or []
    gap_items = gap_items or []

    llm = MagicMock()

    async def _call(messages, response_format, scope, temperature=0.3):
        if response_format is _InferenceList:
            return _InferenceList(inferences=inference_items)
        return _GapList(gaps=gap_items)

    llm.call = _call
    mode_config = MagicMock()
    mode_config.construction_style = style
    return ConstructionPipeline(llm=llm, mode_config=mode_config)


class TestConstructEndToEnd:
    @pytest.mark.asyncio
    async def test_empty_scored_results(self):
        p = _make_pipeline_with_llm()
        answer = await p.construct([], "query")
        assert isinstance(answer, ConstructedAnswer)
        assert answer.facts == []
        assert answer.inferences == []
        assert answer.gaps == []
        assert answer.overall_confidence == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_no_llm_skips_inference_and_gaps(self):
        """When llm is None, inference and gap phases are skipped."""
        mode_config = MagicMock()
        mode_config.construction_style = "conservative"
        p = ConstructionPipeline(llm=None, mode_config=mode_config)
        srs = [_scored_result("e-1", 0.8)]
        answer = await p.construct(srs, "query")
        assert answer.facts != []
        assert answer.inferences == []
        assert answer.gaps == []

    @pytest.mark.asyncio
    async def test_facts_extracted_from_scored_results(self):
        p = _make_pipeline_with_llm()
        srs = [_scored_result("e-1", 0.85, "Fact A"), _scored_result("e-2", 0.65, "Fact B")]
        answer = await p.construct(srs, "query")
        assert len(answer.facts) == 2
        assert answer.facts[0].engram_id == "e-1"
        assert answer.facts[1].engram_id == "e-2"

    @pytest.mark.asyncio
    async def test_inferences_included_in_answer(self):
        inf_item = _InferenceItem(content="C follows from A", confidence=0.8, inference_type="deduction", reasoning="r")
        p = _make_pipeline_with_llm(inference_items=[inf_item])
        srs = [_scored_result("e-1", 0.9)]
        answer = await p.construct(srs, "query")
        assert len(answer.inferences) == 1
        assert answer.inferences[0].content == "C follows from A"

    @pytest.mark.asyncio
    async def test_gaps_included_in_answer(self):
        gap_item = _GapItem(description="Missing Z", relevance=0.5)
        p = _make_pipeline_with_llm(gap_items=[gap_item])
        srs = [_scored_result("e-1", 0.9)]
        answer = await p.construct(srs, "query")
        assert len(answer.gaps) == 1
        assert answer.gaps[0].description == "Missing Z"

    @pytest.mark.asyncio
    async def test_mode_influence_set_in_answer(self):
        p = _make_pipeline_with_llm(style="creative")
        srs = [_scored_result("e-1", 0.9)]
        answer = await p.construct(srs, "query")
        assert answer.mode_influence == "creative"

    @pytest.mark.asyncio
    async def test_construction_metadata_populated(self):
        p = _make_pipeline_with_llm()
        srs = [_scored_result("e-1", 0.9)]
        answer = await p.construct(srs, "query")
        assert "elapsed_s" in answer.construction_metadata
        assert answer.construction_metadata["fact_count"] == 1

    @pytest.mark.asyncio
    async def test_wc_updated_with_confirmed_inferences(self):
        inf_item = _InferenceItem(content="High conf", confidence=0.85, inference_type="deduction", reasoning="r")
        p = _make_pipeline_with_llm(inference_items=[inf_item])
        wc = MagicMock()
        wc.inference_layer = []
        srs = [_scored_result("e-1", 0.9)]
        await p.construct(srs, "query", working_context=wc)
        assert len(wc.inference_layer) == 1

    @pytest.mark.asyncio
    async def test_inference_phase_exception_is_non_fatal(self):
        """Exceptions in LLM calls must not crash construct()."""
        llm = MagicMock()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM down"))
        mode_config = MagicMock()
        mode_config.construction_style = "conservative"
        p = ConstructionPipeline(llm=llm, mode_config=mode_config)
        srs = [_scored_result("e-1", 0.8)]
        answer = await p.construct(srs, "query")
        assert isinstance(answer, ConstructedAnswer)
        assert answer.facts  # facts still extracted

    @pytest.mark.asyncio
    async def test_precision_mode_conservative_style(self):
        """Precision mode → conservative construction (limited inferences)."""
        inf_item = _InferenceItem(content="X", confidence=0.9, inference_type="deduction", reasoning="r")
        p = _make_pipeline_with_llm(inference_items=[inf_item], style="conservative")
        answer = await p.construct([_scored_result("e-1", 0.9)], "query")
        assert answer.mode_influence == "conservative"

    @pytest.mark.asyncio
    async def test_exploration_mode_extrapolation_style(self):
        """Exploration mode → creative/extrapolation construction."""
        inf_item = _InferenceItem(content="Y", confidence=0.9, inference_type="extrapolation", reasoning="r")
        p = _make_pipeline_with_llm(inference_items=[inf_item], style="creative")
        answer = await p.construct([_scored_result("e-1", 0.9)], "query")
        assert answer.mode_influence == "creative"
        assert answer.inferences[0].inference_type == "extrapolation"
