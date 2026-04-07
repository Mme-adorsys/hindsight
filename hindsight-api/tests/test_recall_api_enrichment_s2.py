"""
Tests for Epic 15 Story 02 — Recall API Enrichment.

Covers:
- RecallRequest: question/query alias logic
- RecallRequest: at-least-one validation
- RecallRequest: expectation and tags fields
- RecallResult: new fields (tags, expectation, outcome)
- MemoryFact: new fields present
- Backward compatibility
"""

import pytest
from pydantic import ValidationError

from hindsight_api.api.http import RecallRequest, RecallResult
from hindsight_api.engine.response_models import MemoryFact


# ---------------------------------------------------------------------------
# RecallRequest — question/query alias
# ---------------------------------------------------------------------------


class TestRecallRequestAlias:
    def test_query_alone_accepted(self):
        req = RecallRequest(query="What is the capital of France?")
        assert req.query == "What is the capital of France?"

    def test_question_alone_accepted(self):
        req = RecallRequest(question="What is the capital of France?")
        assert req.query == "What is the capital of France?"

    def test_question_takes_precedence_over_query(self):
        req = RecallRequest(query="old query", question="new question")
        assert req.query == "new question"

    def test_question_stored_as_query_after_validation(self):
        """After model_validator, req.query always holds the effective text."""
        req = RecallRequest(question="preferred text")
        assert req.query == "preferred text"

    def test_neither_query_nor_question_raises(self):
        with pytest.raises(ValidationError, match="At least one"):
            RecallRequest()

    def test_empty_string_query_raises(self):
        """Empty string is falsy — treated as missing."""
        with pytest.raises(ValidationError, match="At least one"):
            RecallRequest(query="", question="")


# ---------------------------------------------------------------------------
# RecallRequest — new fields
# ---------------------------------------------------------------------------


class TestRecallRequestNewFields:
    def test_expectation_accepted(self):
        req = RecallRequest(query="q", expectation="I expect to find meeting notes")
        assert req.expectation == "I expect to find meeting notes"

    def test_expectation_default_none(self):
        req = RecallRequest(query="q")
        assert req.expectation is None

    def test_tags_accepted(self):
        req = RecallRequest(query="q", tags=["ml", "project-alpha"])
        assert req.tags == ["ml", "project-alpha"]

    def test_tags_default_none(self):
        req = RecallRequest(query="q")
        assert req.tags is None

    def test_tags_empty_list_accepted(self):
        req = RecallRequest(query="q", tags=[])
        assert req.tags == []


# ---------------------------------------------------------------------------
# RecallRequest — backward compatibility
# ---------------------------------------------------------------------------


class TestRecallRequestBackwardCompat:
    def test_legacy_call_with_query_only(self):
        req = RecallRequest(
            query="What did Alice say?",
            budget="mid",
            max_tokens=4096,
            trace=False,
        )
        assert req.query == "What did Alice say?"
        assert req.expectation is None
        assert req.tags is None
        assert req.question is None

    def test_all_legacy_fields_still_work(self):
        req = RecallRequest(
            query="q",
            types=["world", "experience"],
            budget="high",
            max_tokens=2048,
            trace=True,
            query_timestamp="2023-05-30T23:40:00",
        )
        assert req.types == ["world", "experience"]
        assert req.budget.value == "high"
        assert req.max_tokens == 2048
        assert req.trace is True


# ---------------------------------------------------------------------------
# RecallResult — new fields
# ---------------------------------------------------------------------------


class TestRecallResultNewFields:
    def test_tags_field_present(self):
        result = RecallResult(id="abc", text="Hello", type="world", tags=["ml", "alice"])
        assert result.tags == ["ml", "alice"]

    def test_expectation_field_present(self):
        result = RecallResult(id="abc", text="Hello", type="world", expectation="I expected X")
        assert result.expectation == "I expected X"

    def test_outcome_field_present(self):
        result = RecallResult(id="abc", text="Hello", type="world", outcome="Y happened")
        assert result.outcome == "Y happened"

    def test_new_fields_default_none(self):
        result = RecallResult(id="abc", text="Hello", type="world")
        assert result.tags is None
        assert result.expectation is None
        assert result.outcome is None

    def test_all_new_fields_together(self):
        result = RecallResult(
            id="abc",
            text="Hello",
            type="experience",
            tags=["project-x"],
            expectation="I thought it would work",
            outcome="It worked better than expected",
        )
        assert result.tags == ["project-x"]
        assert result.expectation == "I thought it would work"
        assert result.outcome == "It worked better than expected"


# ---------------------------------------------------------------------------
# MemoryFact — new fields
# ---------------------------------------------------------------------------


class TestMemoryFactNewFields:
    def test_tags_field_present(self):
        fact = MemoryFact(id="1", text="t", fact_type="world", tags=["a", "b"])
        assert fact.tags == ["a", "b"]

    def test_expectation_field_present(self):
        fact = MemoryFact(id="1", text="t", fact_type="world", expectation="exp")
        assert fact.expectation == "exp"

    def test_outcome_field_present(self):
        fact = MemoryFact(id="1", text="t", fact_type="world", outcome="out")
        assert fact.outcome == "out"

    def test_new_fields_default_none(self):
        fact = MemoryFact(id="1", text="t", fact_type="world")
        assert fact.tags is None
        assert fact.expectation is None
        assert fact.outcome is None
