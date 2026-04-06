"""
Unit tests for Epic 11 Story 01 — ConstructedAnswer Data Model.

Covers:
- ConstructedFact: Pydantic validation, source literals, thalamus_scores
- Inference: all inference_types, confidence clamping
- Gap: relevance, suggested_query optional
- ConstructedAnswer: to_memory_facts() backward compat, has_gaps, counts
- RecallResult.constructed_answer: optional field presence
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hindsight_api.engine.constructive.models import (
    ConstructedAnswer,
    ConstructedFact,
    Gap,
    Inference,
)
from hindsight_api.engine.response_models import RecallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(
    engram_id: str = "e-1",
    content: str = "Test content.",
    confidence: float = 0.8,
    source: str = "direct",
) -> ConstructedFact:
    return ConstructedFact(engram_id=engram_id, content=content, confidence=confidence, source=source)


def _inference(
    content: str = "Therefore X.",
    confidence: float = 0.7,
    inference_type: str = "deduction",
) -> Inference:
    return Inference(
        content=content,
        confidence=confidence,
        supporting_engram_ids=["e-1"],
        inference_type=inference_type,
        reasoning="e-1 implies X",
    )


def _gap(description: str = "Missing Y", relevance: float = 0.5) -> Gap:
    return Gap(description=description, relevance=relevance)


# ---------------------------------------------------------------------------
# ConstructedFact
# ---------------------------------------------------------------------------


class TestConstructedFact:
    def test_direct_source_valid(self):
        f = _fact(source="direct")
        assert f.source == "direct"

    def test_reconstructed_source_valid(self):
        f = _fact(source="reconstructed")
        assert f.source == "reconstructed"

    def test_invalid_source_raises(self):
        with pytest.raises(ValidationError):
            ConstructedFact(engram_id="e-1", content="x", confidence=0.5, source="unknown")

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            _fact(confidence=-0.1)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            _fact(confidence=1.1)

    def test_confidence_at_boundaries_valid(self):
        assert _fact(confidence=0.0).confidence == pytest.approx(0.0)
        assert _fact(confidence=1.0).confidence == pytest.approx(1.0)

    def test_tags_default_empty(self):
        assert _fact().tags == []

    def test_thalamus_scores_default_empty(self):
        assert _fact().thalamus_scores == {}

    def test_thalamus_scores_stored(self):
        f = ConstructedFact(
            engram_id="e-1",
            content="x",
            confidence=0.5,
            source="direct",
            thalamus_scores={"novelty": 0.8, "surprise": 0.6},
        )
        assert f.thalamus_scores["novelty"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


class TestInference:
    def test_all_inference_types_valid(self):
        for t in ["deduction", "analogy", "interpolation", "extrapolation"]:
            inf = Inference(
                content="x", confidence=0.5, inference_type=t, reasoning="r"
            )
            assert inf.inference_type == t

    def test_invalid_inference_type_raises(self):
        with pytest.raises(ValidationError):
            Inference(content="x", confidence=0.5, inference_type="speculation", reasoning="r")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            Inference(content="x", confidence=1.5, inference_type="deduction", reasoning="r")

    def test_supporting_engram_ids_default_empty(self):
        inf = Inference(content="x", confidence=0.5, inference_type="deduction", reasoning="r")
        assert inf.supporting_engram_ids == []

    def test_reasoning_stored(self):
        inf = _inference(content="C follows from A and B", inference_type="deduction")
        assert inf.reasoning == "e-1 implies X"


# ---------------------------------------------------------------------------
# Gap
# ---------------------------------------------------------------------------


class TestGap:
    def test_gap_basic(self):
        g = _gap("Missing info about Y", relevance=0.7)
        assert g.description == "Missing info about Y"
        assert g.relevance == pytest.approx(0.7)

    def test_suggested_query_default_none(self):
        assert _gap().suggested_query is None

    def test_suggested_query_set(self):
        g = Gap(description="Need X", relevance=0.5, suggested_query="What is X?")
        assert g.suggested_query == "What is X?"

    def test_relevance_bounds(self):
        with pytest.raises(ValidationError):
            Gap(description="x", relevance=1.5)
        with pytest.raises(ValidationError):
            Gap(description="x", relevance=-0.1)

    def test_related_engram_ids_default_empty(self):
        assert _gap().related_engram_ids == []


# ---------------------------------------------------------------------------
# ConstructedAnswer
# ---------------------------------------------------------------------------


class TestConstructedAnswer:
    def test_empty_answer_valid(self):
        a = ConstructedAnswer()
        assert a.facts == []
        assert a.inferences == []
        assert a.gaps == []
        assert a.overall_confidence == pytest.approx(0.0)
        assert a.mode_influence == "conservative"

    def test_fact_count(self):
        a = ConstructedAnswer(facts=[_fact("e-1"), _fact("e-2")])
        assert a.fact_count == 2

    def test_inference_count(self):
        a = ConstructedAnswer(inferences=[_inference(), _inference()])
        assert a.inference_count == 2

    def test_has_gaps_false_no_gaps(self):
        assert not ConstructedAnswer().has_gaps

    def test_has_gaps_false_low_relevance(self):
        a = ConstructedAnswer(gaps=[Gap(description="minor", relevance=0.2)])
        assert not a.has_gaps  # relevance < 0.3

    def test_has_gaps_true_high_relevance(self):
        a = ConstructedAnswer(gaps=[Gap(description="major", relevance=0.5)])
        assert a.has_gaps

    def test_overall_confidence_bounds(self):
        with pytest.raises(ValidationError):
            ConstructedAnswer(overall_confidence=1.5)

    def test_construction_metadata_default_empty(self):
        assert ConstructedAnswer().construction_metadata == {}

    def test_mode_influence_stored(self):
        a = ConstructedAnswer(mode_influence="creative")
        assert a.mode_influence == "creative"


# ---------------------------------------------------------------------------
# to_memory_facts() backward compatibility
# ---------------------------------------------------------------------------


class TestToMemoryFacts:
    def test_empty_answer_returns_empty_list(self):
        assert ConstructedAnswer().to_memory_facts() == []

    def test_fact_becomes_memory_fact(self):
        a = ConstructedAnswer(facts=[_fact("e-1", "Alice works at Google.", 0.85)])
        mf_list = a.to_memory_facts()
        assert len(mf_list) == 1
        mf = mf_list[0]
        assert mf.id == "e-1"
        assert mf.text == "Alice works at Google."
        assert mf.fact_type == "general"

    def test_multiple_facts_preserved(self):
        facts = [_fact(f"e-{i}", f"Content {i}.") for i in range(5)]
        a = ConstructedAnswer(facts=facts)
        mf_list = a.to_memory_facts()
        assert len(mf_list) == 5
        assert [mf.id for mf in mf_list] == [f"e-{i}" for i in range(5)]

    def test_confidence_in_metadata(self):
        a = ConstructedAnswer(facts=[_fact("e-1", confidence=0.75)])
        mf = a.to_memory_facts()[0]
        assert mf.metadata is not None
        assert "confidence" in mf.metadata

    def test_inferences_not_in_memory_facts(self):
        """Inferences are dropped in backward-compat mode."""
        a = ConstructedAnswer(
            facts=[_fact("e-1")],
            inferences=[_inference()],
        )
        assert len(a.to_memory_facts()) == 1

    def test_gaps_not_in_memory_facts(self):
        """Gaps are dropped in backward-compat mode."""
        a = ConstructedAnswer(
            facts=[_fact("e-1")],
            gaps=[_gap()],
        )
        assert len(a.to_memory_facts()) == 1


# ---------------------------------------------------------------------------
# RecallResult.constructed_answer optional field
# ---------------------------------------------------------------------------


class TestRecallResultConstructedAnswer:
    def test_constructed_answer_defaults_to_none(self):
        rr = RecallResult(results=[])
        assert rr.constructed_answer is None

    def test_constructed_answer_can_be_set(self):
        ca = ConstructedAnswer(facts=[_fact("e-1")])
        rr = RecallResult(results=[], constructed_answer=ca)
        assert rr.constructed_answer is not None
        assert rr.constructed_answer.fact_count == 1

    def test_existing_recall_result_unchanged(self):
        """RecallResult without constructed_answer is fully backward-compatible."""
        rr = RecallResult(results=[])
        assert rr.results == []
        assert rr.trace is None
        assert rr.constructed_answer is None
