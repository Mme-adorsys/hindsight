"""
Tests for Epic 15 Story 01 — Retain API Enrichment.

Covers:
- MemoryItem validation (expectation, outcome, tags)
- RetainContentDict propagation
- ExtractedFact / ProcessedFact round-trip
- Tags merge logic (user_tags + LLM-extracted tags)
- Backward compatibility
"""

import pytest
from pydantic import ValidationError

from hindsight_api.api.http import MemoryItem
from hindsight_api.engine.retain.types import ExtractedFact, ProcessedFact, RetainContentDict


# ---------------------------------------------------------------------------
# MemoryItem validation
# ---------------------------------------------------------------------------


class TestMemoryItemNewFields:
    def test_all_new_fields_optional(self):
        item = MemoryItem(content="Alice works at Google")
        assert item.expectation is None
        assert item.outcome is None
        assert item.tags is None

    def test_expectation_and_outcome_accepted(self):
        item = MemoryItem(
            content="Alice presented the model",
            expectation="Accuracy would improve by 10%",
            outcome="Accuracy improved by 15%",
        )
        assert item.expectation == "Accuracy would improve by 10%"
        assert item.outcome == "Accuracy improved by 15%"

    def test_tags_accepted(self):
        item = MemoryItem(content="Meeting notes", tags=["ml", "project-alpha"])
        assert item.tags == ["ml", "project-alpha"]

    def test_tags_no_spaces_rejected(self):
        with pytest.raises(ValidationError, match="spaces"):
            MemoryItem(content="x", tags=["invalid tag"])

    def test_tags_max_50_chars(self):
        with pytest.raises(ValidationError, match="50"):
            MemoryItem(content="x", tags=["a" * 51])

    def test_tags_exactly_50_chars_ok(self):
        item = MemoryItem(content="x", tags=["a" * 50])
        assert len(item.tags[0]) == 50

    def test_tags_non_string_rejected(self):
        with pytest.raises(ValidationError):
            MemoryItem(content="x", tags=[123])

    def test_backward_compat_no_new_fields(self):
        item = MemoryItem(content="Legacy content", context="work")
        assert item.content == "Legacy content"
        assert item.context == "work"
        assert item.expectation is None
        assert item.outcome is None
        assert item.tags is None


# ---------------------------------------------------------------------------
# RetainContentDict — new keys present
# ---------------------------------------------------------------------------


class TestRetainContentDictFields:
    def test_has_expectation_outcome_tags_keys(self):
        d: RetainContentDict = {
            "content": "test",
            "expectation": "something",
            "outcome": "something else",
            "tags": ["alpha"],
        }
        assert d["expectation"] == "something"
        assert d["outcome"] == "something else"
        assert d["tags"] == ["alpha"]

    def test_missing_new_fields_still_valid(self):
        d: RetainContentDict = {"content": "minimal"}
        assert d.get("expectation") is None
        assert d.get("outcome") is None
        assert d.get("tags") is None


# ---------------------------------------------------------------------------
# ExtractedFact — expectation / outcome / user_tags
# ---------------------------------------------------------------------------


class TestExtractedFactEnrichment:
    def test_defaults_none(self):
        fact = ExtractedFact(fact_text="The sky is blue", fact_type="world")
        assert fact.expectation is None
        assert fact.outcome is None
        assert fact.user_tags == []

    def test_fields_set(self):
        fact = ExtractedFact(
            fact_text="Model performance",
            fact_type="experience",
            expectation="10% gain",
            outcome="15% gain",
            user_tags=["ml", "experiment"],
        )
        assert fact.expectation == "10% gain"
        assert fact.outcome == "15% gain"
        assert fact.user_tags == ["ml", "experiment"]


# ---------------------------------------------------------------------------
# ProcessedFact — from_extracted_fact tags merge + field propagation
# ---------------------------------------------------------------------------


class TestProcessedFactTagsMerge:
    def _make_extracted(self, user_tags=None, llm_tags=None, expectation=None, outcome=None):
        return ExtractedFact(
            fact_text="test",
            fact_type="world",
            user_tags=user_tags or [],
            tags=llm_tags or [],
            expectation=expectation,
            outcome=outcome,
        )

    def test_merge_user_and_llm_tags(self):
        fact = self._make_extracted(user_tags=["user-tag"], llm_tags=["llm-tag"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[0.1, 0.2])
        assert processed.tags == ["user-tag", "llm-tag"]

    def test_user_tags_first_in_merged(self):
        fact = self._make_extracted(user_tags=["u1", "u2"], llm_tags=["l1"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.tags[0] == "u1"
        assert processed.tags[1] == "u2"
        assert processed.tags[2] == "l1"

    def test_duplicate_tags_deduplicated(self):
        fact = self._make_extracted(user_tags=["ml", "shared"], llm_tags=["shared", "new"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.tags.count("shared") == 1

    def test_only_user_tags(self):
        fact = self._make_extracted(user_tags=["only-user"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.tags == ["only-user"]

    def test_only_llm_tags(self):
        fact = self._make_extracted(llm_tags=["only-llm"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.tags == ["only-llm"]

    def test_no_tags_empty(self):
        fact = self._make_extracted()
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.tags == []

    def test_expectation_propagated(self):
        fact = self._make_extracted(expectation="I expected X")
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.expectation == "I expected X"

    def test_outcome_propagated(self):
        fact = self._make_extracted(outcome="Y happened instead")
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.outcome == "Y happened instead"

    def test_none_expectation_outcome_propagated(self):
        fact = self._make_extracted()
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[])
        assert processed.expectation is None
        assert processed.outcome is None


# ---------------------------------------------------------------------------
# Orchestrator propagation — unit test for field injection
# ---------------------------------------------------------------------------


class TestOrchestratorFieldPropagation:
    """Verify that orchestrator propagates expectation/outcome/tags from content_dict."""

    def test_propagation_logic(self):
        """Simulate the orchestrator propagation loop."""
        contents_dicts: list[RetainContentDict] = [
            {
                "content": "Meeting notes",
                "expectation": "Project on track",
                "outcome": "Project delayed",
                "tags": ["meeting", "project"],
            }
        ]
        fact = ExtractedFact(fact_text="Meeting notes", fact_type="world", content_index=0)

        # Simulate orchestrator loop
        content_dict = contents_dicts[fact.content_index]
        if fact.expectation is None and content_dict.get("expectation"):
            fact.expectation = content_dict["expectation"]
        if fact.outcome is None and content_dict.get("outcome"):
            fact.outcome = content_dict["outcome"]
        if not fact.user_tags and content_dict.get("tags"):
            fact.user_tags = content_dict["tags"]

        assert fact.expectation == "Project on track"
        assert fact.outcome == "Project delayed"
        assert fact.user_tags == ["meeting", "project"]

    def test_no_overwrite_if_already_set(self):
        """R0-extracted values take precedence over content_dict values."""
        contents_dicts: list[RetainContentDict] = [
            {"content": "x", "expectation": "content-level", "tags": ["content-tag"]}
        ]
        fact = ExtractedFact(
            fact_text="x",
            fact_type="world",
            content_index=0,
            expectation="r0-extracted",
            user_tags=["r0-tag"],
        )

        content_dict = contents_dicts[fact.content_index]
        if fact.expectation is None and content_dict.get("expectation"):
            fact.expectation = content_dict["expectation"]
        if not fact.user_tags and content_dict.get("tags"):
            fact.user_tags = content_dict["tags"]

        assert fact.expectation == "r0-extracted"
        assert fact.user_tags == ["r0-tag"]
