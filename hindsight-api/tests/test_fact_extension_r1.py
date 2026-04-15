"""
Unit tests for Epic 05 Story 01 — ExtractedFact/ProcessedFact Extension (R1).

Covers:
- Tags propagation through the pipeline
- Thalamus score propagation through the pipeline
- fact_type derivation from tags (backward compat)
- Fallback when no tags are provided
- engram_dictionary batch insert (T5) with tags + thalamus scores
- engram_dictionary batch insert with missing scores (graceful None handling)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID

import pytest

from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.retain.fact_storage import _insert_engram_dictionary_batch
from hindsight_api.engine.retain.types import ExtractedFact, ProcessedFact

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _processed_fact(
    tags: list[str] | None = None,
    thalamus_scores: ThalamusScores | None = None,
    fact_type: str = "world",
) -> ProcessedFact:
    return ProcessedFact(
        fact_text="The sky is blue.",
        fact_type=fact_type,
        embedding=[0.1, 0.2, 0.3],
        occurred_start=None,
        occurred_end=None,
        mentioned_at=datetime.now(UTC),
        context="test",
        metadata={},
        tags=tags or [],
        thalamus_scores=thalamus_scores,
    )


def _scores(n=0.8, s=0.6, tr=0.7, ev=0.5, overall=0.65) -> ThalamusScores:
    return ThalamusScores(novelty=n, surprise=s, task_relevance=tr, emotional_valence=ev, overall=overall)


# ---------------------------------------------------------------------------
# T1/T2/T3: ExtractedFact and ProcessedFact field presence
# ---------------------------------------------------------------------------


class TestExtractedFactFields:
    def test_tags_default_empty_list(self):
        fact = ExtractedFact(fact_text="A fact.", fact_type="world")
        assert fact.tags == []

    def test_thalamus_scores_default_none(self):
        fact = ExtractedFact(fact_text="A fact.", fact_type="world")
        assert fact.thalamus_scores is None

    def test_tags_can_be_set(self):
        fact = ExtractedFact(fact_text="A fact.", fact_type="world", tags=["technical", "goal"])
        assert fact.tags == ["technical", "goal"]

    def test_thalamus_scores_can_be_set(self):
        scores = _scores()
        fact = ExtractedFact(fact_text="A fact.", fact_type="world", thalamus_scores=scores)
        assert fact.thalamus_scores is scores


class TestProcessedFactFields:
    def test_tags_default_empty_list(self):
        fact = _processed_fact()
        assert fact.tags == []

    def test_thalamus_scores_default_none(self):
        fact = _processed_fact()
        assert fact.thalamus_scores is None

    def test_from_extracted_fact_passes_tags(self):
        extracted = ExtractedFact(
            fact_text="A fact.",
            fact_type="world",
            tags=["personal", "health"],
            mentioned_at=datetime.now(UTC),
        )
        processed = ProcessedFact.from_extracted_fact(extracted, embedding=[0.1])
        assert processed.tags == ["personal", "health"]

    def test_from_extracted_fact_passes_thalamus_scores(self):
        scores = _scores()
        extracted = ExtractedFact(
            fact_text="A fact.",
            fact_type="world",
            thalamus_scores=scores,
            mentioned_at=datetime.now(UTC),
        )
        processed = ProcessedFact.from_extracted_fact(extracted, embedding=[0.1])
        assert processed.thalamus_scores is scores

    def test_from_extracted_fact_no_tags_stays_empty(self):
        extracted = ExtractedFact(fact_text="A fact.", fact_type="world", mentioned_at=datetime.now(UTC))
        processed = ProcessedFact.from_extracted_fact(extracted, embedding=[0.1])
        assert processed.tags == []


# ---------------------------------------------------------------------------
# T5: _insert_engram_dictionary_batch
# ---------------------------------------------------------------------------


class TestInsertEngramDictionaryBatch:
    @pytest.mark.asyncio
    async def test_inserts_one_row_per_fact(self):
        conn = AsyncMock()
        unit_ids = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
        facts = [_processed_fact(tags=["a"]), _processed_fact(tags=["b"])]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_passes_tags_to_insert(self):
        conn = AsyncMock()
        unit_ids = ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
        facts = [_processed_fact(tags=["technical", "decision"])]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        # args[2] is the tags parameter ($3)
        assert args[2] == ["technical", "decision"]

    @pytest.mark.asyncio
    async def test_passes_thalamus_scores_to_insert(self):
        conn = AsyncMock()
        scores = _scores(n=0.9, s=0.4, tr=0.7, ev=0.3, overall=0.6)
        unit_ids = ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]
        facts = [_processed_fact(thalamus_scores=scores)]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        # $4=novelty, $5=surprise, $6=task_relevance, $7=emotional_valence, $8=overall
        assert args[3] == pytest.approx(0.9)
        assert args[4] == pytest.approx(0.4)
        assert args[5] == pytest.approx(0.7)
        assert args[6] == pytest.approx(0.3)
        assert args[7] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_none_thalamus_scores_inserts_nulls(self):
        conn = AsyncMock()
        unit_ids = ["cccccccc-cccc-cccc-cccc-cccccccccccc"]
        facts = [_processed_fact(thalamus_scores=None)]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        assert args[3] is None  # novelty
        assert args[4] is None  # surprise
        assert args[5] is None  # task_relevance
        assert args[6] is None  # emotional_valence
        assert args[7] is None  # thalamus_overall

    @pytest.mark.asyncio
    async def test_empty_tags_list_stored_as_none(self):
        conn = AsyncMock()
        unit_ids = ["dddddddd-dddd-dddd-dddd-dddddddddddd"]
        facts = [_processed_fact(tags=[])]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        assert args[2] is None  # empty list → None (no JSONB noise)

    @pytest.mark.asyncio
    async def test_engram_id_is_uuid_object(self):
        conn = AsyncMock()
        uid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        unit_ids = [uid]
        facts = [_processed_fact()]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        assert args[0] == UUID(uid)

    @pytest.mark.asyncio
    async def test_no_facts_does_not_call_execute(self):
        conn = AsyncMock()
        await _insert_engram_dictionary_batch(conn, "bank-1", [], [])
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_initial_strength_is_zero(self):
        conn = AsyncMock()
        unit_ids = ["ffffffff-ffff-ffff-ffff-ffffffffffff"]
        facts = [_processed_fact()]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        assert args[8] == pytest.approx(0.0)  # strength

    @pytest.mark.asyncio
    async def test_status_is_active(self):
        conn = AsyncMock()
        unit_ids = ["00000000-0000-0000-0000-000000000001"]
        facts = [_processed_fact()]

        await _insert_engram_dictionary_batch(conn, "bank-1", unit_ids, facts)

        _, *args = conn.execute.call_args[0]
        # Positional args follow the INSERT column order:
        # ... strength ($9), layer ($10), status ($11), ...
        # args[8]=strength, args[9]=layer ('working'), args[10]=status ('active').
        assert args[9] == "working"
        assert args[10] == "active"
