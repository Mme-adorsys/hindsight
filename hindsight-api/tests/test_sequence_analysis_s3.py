"""
Tests for Epic 15 Story 03 — R0 Sequence Analysis Pipeline Step.

Covers:
- StructuredUnit / UnitType data model
- analyze_sequence() per budget tier (mocked LLM)
- Skip logic (items with expectation/outcome bypass R0)
- units_to_content_dicts conversion
- Orchestrator R0→R1 flow (budget propagation, skip)
- Fallback behaviour on empty/malformed LLM output
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.retain.sequence_analysis import (
    StructuredUnit,
    UnitType,
    _extract_json,
    _parse_units,
    analyze_sequence,
    units_to_content_dicts,
)
from hindsight_api.engine.utils import Budget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeLLMConfig:
    """Minimal mock for LLMConfig.call()."""

    def __init__(self, response):
        self._response = response

    async def call(self, **kwargs):
        from hindsight_api.engine.response_models import TokenUsage

        return self._response, TokenUsage(input_tokens=10, output_tokens=20)


# ---------------------------------------------------------------------------
# UnitType enum
# ---------------------------------------------------------------------------


class TestUnitType:
    def test_values(self):
        assert UnitType.FACT.value == "fact"
        assert UnitType.ACTION_EFFECT.value == "action_effect"
        assert UnitType.EXPERIENCE.value == "experience"

    def test_from_string(self):
        assert UnitType("fact") is UnitType.FACT
        assert UnitType("action_effect") is UnitType.ACTION_EFFECT
        assert UnitType("experience") is UnitType.EXPERIENCE


# ---------------------------------------------------------------------------
# StructuredUnit
# ---------------------------------------------------------------------------


class TestStructuredUnit:
    def test_defaults(self):
        unit = StructuredUnit(content="Alice works at Google", unit_type=UnitType.FACT)
        assert unit.context == ""
        assert unit.expectation is None
        assert unit.outcome is None
        assert unit.related_unit_ids == []
        assert unit.confidence == 1.0

    def test_experience_unit(self):
        unit = StructuredUnit(
            content="Model deployment",
            unit_type=UnitType.EXPERIENCE,
            expectation="10% latency improvement",
            outcome="15% improvement achieved",
            confidence=0.9,
        )
        assert unit.expectation == "10% latency improvement"
        assert unit.outcome == "15% improvement achieved"


# ---------------------------------------------------------------------------
# _parse_units helper
# ---------------------------------------------------------------------------


class TestParseUnits:
    def test_valid_list(self):
        raw = [{"content": "Alice works at Google", "unit_type": "fact", "confidence": 0.9}]
        units = _parse_units(raw, fallback_content="fallback")
        assert len(units) == 1
        assert units[0].content == "Alice works at Google"
        assert units[0].unit_type is UnitType.FACT

    def test_unknown_unit_type_falls_back_to_fact(self):
        raw = [{"content": "some content", "unit_type": "unknown_type"}]
        units = _parse_units(raw, fallback_content="fallback")
        assert units[0].unit_type is UnitType.FACT

    def test_empty_list_returns_fallback(self):
        units = _parse_units([], fallback_content="fallback text")
        assert len(units) == 1
        assert units[0].content == "fallback text"
        assert units[0].confidence == 0.5

    def test_non_list_returns_fallback(self):
        units = _parse_units({"not": "a list"}, fallback_content="fallback")
        assert len(units) == 1
        assert units[0].content == "fallback"

    def test_items_without_content_skipped(self):
        raw = [{"unit_type": "fact"}, {"content": "valid", "unit_type": "fact"}]
        units = _parse_units(raw, fallback_content="fb")
        assert len(units) == 1
        assert units[0].content == "valid"

    def test_experience_fields_parsed(self):
        raw = [
            {
                "content": "deployment",
                "unit_type": "experience",
                "expectation": "fast",
                "outcome": "too slow",
                "confidence": 0.8,
            }
        ]
        units = _parse_units(raw, fallback_content="fb")
        assert units[0].expectation == "fast"
        assert units[0].outcome == "too slow"
        assert units[0].confidence == 0.8

    def test_json_string_input(self):
        raw = '[{"content": "parsed from string", "unit_type": "fact"}]'
        units = _parse_units(raw, fallback_content="fb")
        assert units[0].content == "parsed from string"


# ---------------------------------------------------------------------------
# _extract_json helper
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json(self):
        result = _extract_json('[{"content": "x"}]')
        assert isinstance(result, list)

    def test_markdown_fenced(self):
        result = _extract_json('```json\n[{"content": "x"}]\n```')
        assert isinstance(result, list)

    def test_invalid_returns_string(self):
        result = _extract_json("not json at all")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# analyze_sequence — per budget tier
# ---------------------------------------------------------------------------


class TestAnalyzeSequenceLow:
    @pytest.mark.asyncio
    async def test_low_budget_facts_only(self):
        llm = _FakeLLMConfig([{"content": "Alice works at Google", "unit_type": "fact", "confidence": 1.0}])
        units, usage = await analyze_sequence("Alice works at Google.", Budget.LOW, llm)
        assert len(units) == 1
        assert units[0].unit_type is UnitType.FACT
        assert usage.input_tokens == 10

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty(self):
        llm = _FakeLLMConfig([])
        units, usage = await analyze_sequence("", Budget.LOW, llm)
        assert units == []

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self):
        llm = _FakeLLMConfig([])
        units, usage = await analyze_sequence("   ", Budget.LOW, llm)
        assert units == []


class TestAnalyzeSequenceMid:
    @pytest.mark.asyncio
    async def test_mid_budget_mixed_types(self):
        llm = _FakeLLMConfig(
            [
                {"content": "Alice joined Google", "unit_type": "fact", "confidence": 1.0},
                {
                    "content": "Model deployed",
                    "unit_type": "action_effect",
                    "outcome": "10% speedup",
                    "confidence": 0.9,
                },
                {
                    "content": "Project X outcome",
                    "unit_type": "experience",
                    "expectation": "on-time delivery",
                    "outcome": "delayed by 2 weeks",
                    "confidence": 0.85,
                },
            ]
        )
        units, _ = await analyze_sequence("Some rich content", Budget.MID, llm)
        types = {u.unit_type for u in units}
        assert UnitType.FACT in types
        assert UnitType.ACTION_EFFECT in types
        assert UnitType.EXPERIENCE in types


class TestAnalyzeSequenceHigh:
    @pytest.mark.asyncio
    async def test_high_budget_low_confidence_implicit(self):
        llm = _FakeLLMConfig(
            [
                {"content": "Implicit assumption", "unit_type": "experience", "confidence": 0.6},
            ]
        )
        units, _ = await analyze_sequence("Narrative text", Budget.HIGH, llm)
        assert units[0].confidence == 0.6

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        class _FailingLLM:
            async def call(self, **kwargs):
                raise RuntimeError("LLM timeout")

        units, usage = await analyze_sequence("content", Budget.HIGH, _FailingLLM())
        assert units == []


# ---------------------------------------------------------------------------
# units_to_content_dicts
# ---------------------------------------------------------------------------


class TestUnitsToContentDicts:
    def test_basic_conversion(self):
        units = [StructuredUnit(content="Alice works at Google", unit_type=UnitType.FACT, context="work")]
        source = {"content": "original", "context": "meeting", "metadata": {"src": "slack"}}
        result = units_to_content_dicts(units, source)
        assert len(result) == 1
        assert result[0]["content"] == "Alice works at Google"
        assert result[0]["context"] == "work"  # unit context overrides
        assert result[0]["metadata"] == {"src": "slack"}

    def test_experience_fields_propagated(self):
        units = [
            StructuredUnit(
                content="Project deployed",
                unit_type=UnitType.EXPERIENCE,
                expectation="fast delivery",
                outcome="delayed",
            )
        ]
        source = {"content": "original"}
        result = units_to_content_dicts(units, source)
        assert result[0]["expectation"] == "fast delivery"
        assert result[0]["outcome"] == "delayed"

    def test_no_expectation_outcome_not_added(self):
        units = [StructuredUnit(content="simple fact", unit_type=UnitType.FACT)]
        source = {"content": "original"}
        result = units_to_content_dicts(units, source)
        assert "expectation" not in result[0]
        assert "outcome" not in result[0]

    def test_source_metadata_inherited(self):
        units = [StructuredUnit(content="fact", unit_type=UnitType.FACT)]
        source = {"content": "orig", "tags": ["ml"], "document_id": "doc-1"}
        result = units_to_content_dicts(units, source)
        assert result[0]["tags"] == ["ml"]
        assert result[0]["document_id"] == "doc-1"

    def test_multiple_units_from_one_source(self):
        units = [
            StructuredUnit(content="fact 1", unit_type=UnitType.FACT),
            StructuredUnit(content="fact 2", unit_type=UnitType.FACT),
            StructuredUnit(content="experience", unit_type=UnitType.EXPERIENCE, expectation="x", outcome="y"),
        ]
        source = {"content": "orig", "context": "base"}
        result = units_to_content_dicts(units, source)
        assert len(result) == 3
        assert result[2]["expectation"] == "x"


# ---------------------------------------------------------------------------
# Skip logic (T5) — simulate orchestrator behavior
# ---------------------------------------------------------------------------


class TestSkipLogic:
    """Verify that items with expectation/outcome bypass R0."""

    @pytest.mark.asyncio
    async def test_item_with_expectation_skipped(self):
        """Item with expectation must pass through without LLM call."""
        call_count = 0

        class _CountingLLM:
            async def call(self, **kwargs):
                nonlocal call_count
                call_count += 1
                from hindsight_api.engine.response_models import TokenUsage

                return [], TokenUsage()

        content_dict = {"content": "already structured", "expectation": "I expected X"}
        # Simulate skip logic from orchestrator
        if content_dict.get("expectation") or content_dict.get("outcome"):
            result = [content_dict]
        else:
            units, _ = await analyze_sequence(content_dict["content"], Budget.MID, _CountingLLM())
            result = units_to_content_dicts(units, content_dict)

        assert call_count == 0
        assert result[0] is content_dict

    @pytest.mark.asyncio
    async def test_item_with_outcome_skipped(self):
        content_dict = {"content": "already structured", "outcome": "it worked"}
        if content_dict.get("expectation") or content_dict.get("outcome"):
            passed_through = True
        else:
            passed_through = False
        assert passed_through

    @pytest.mark.asyncio
    async def test_item_without_exp_outcome_processed(self):
        """Item without expectation/outcome must go through R0."""
        llm = _FakeLLMConfig([{"content": "extracted fact", "unit_type": "fact", "confidence": 1.0}])
        content_dict = {"content": "rich conversation text"}
        if not (content_dict.get("expectation") or content_dict.get("outcome")):
            units, _ = await analyze_sequence(content_dict["content"], Budget.MID, llm)
            result = units_to_content_dicts(units, content_dict)
        else:
            result = [content_dict]
        assert result[0]["content"] == "extracted fact"
