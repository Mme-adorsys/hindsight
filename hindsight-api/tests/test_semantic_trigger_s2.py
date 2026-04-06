"""
Unit tests for Epic 10 Story 02 — Semantic Trigger & Qdrant Integration (RF2+RF3).

Covers:
- find_reconsolidation_candidates: Qdrant hit above threshold → CandidateEngram
- find_reconsolidation_candidates: score below threshold → excluded
- find_reconsolidation_candidates: Qdrant error → empty list (graceful)
- find_reconsolidation_candidates: sorted by similarity descending
- find_entity_match_candidates: empty entity_names → empty list (no DB call)
- merge_candidates: Qdrant first, entity appended
- merge_candidates: duplicate engram_id → Qdrant wins (dedup)
- merge_candidates: empty Qdrant + entity → entity only
- merge_candidates: empty both → empty
- CandidateEngram source and score_bonus values
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.reflect.semantic_trigger import (
    QDRANT_SCORE_BONUS,
    CandidateEngram,
    find_entity_match_candidates,
    find_reconsolidation_candidates,
    merge_candidates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_qdrant(results: list[dict]) -> MagicMock:
    """Build a mock QdrantEngineClient whose search_similar returns results."""
    client = MagicMock()
    client.search_similar = AsyncMock(return_value=results)
    return client


def _hit(engram_id: str, score: float) -> dict:
    return {"engram_id": engram_id, "score": score}


# ---------------------------------------------------------------------------
# find_reconsolidation_candidates
# ---------------------------------------------------------------------------


class TestFindReconsolidationCandidates:
    @pytest.mark.asyncio
    async def test_hit_above_threshold_returns_candidate(self):
        client = _mock_qdrant([_hit("e-1", 0.75)])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        assert len(result) == 1
        assert result[0].engram_id == "e-1"
        assert result[0].similarity_score == pytest.approx(0.75)
        assert result[0].source == "qdrant"
        assert result[0].score_bonus == pytest.approx(QDRANT_SCORE_BONUS)

    @pytest.mark.asyncio
    async def test_hit_exactly_at_threshold_included(self):
        client = _mock_qdrant([_hit("e-1", 0.6)])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_hit_below_threshold_excluded(self):
        client = _mock_qdrant([_hit("e-1", 0.59)])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_scores_only_above_threshold(self):
        client = _mock_qdrant([
            _hit("e-1", 0.80),
            _hit("e-2", 0.55),
            _hit("e-3", 0.65),
        ])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        ids = {c.engram_id for c in result}
        assert ids == {"e-1", "e-3"}

    @pytest.mark.asyncio
    async def test_results_sorted_descending_by_similarity(self):
        client = _mock_qdrant([
            _hit("e-1", 0.65),
            _hit("e-2", 0.90),
            _hit("e-3", 0.75),
        ])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        scores = [c.similarity_score for c in result]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_qdrant_error_returns_empty_list(self):
        client = MagicMock()
        client.search_similar = AsyncMock(side_effect=RuntimeError("Qdrant down"))
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_qdrant_results_returns_empty(self):
        client = _mock_qdrant([])
        result = await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="b")
        assert result == []

    @pytest.mark.asyncio
    async def test_bank_filter_passed_to_qdrant(self):
        client = _mock_qdrant([])
        await find_reconsolidation_candidates(client, [0.1] * 384, bank_id="my-bank")
        call_kwargs = client.search_similar.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs["filters"]
        assert filters["must"][0]["match"]["value"] == "my-bank"


# ---------------------------------------------------------------------------
# find_entity_match_candidates
# ---------------------------------------------------------------------------


class TestFindEntityMatchCandidates:
    @pytest.mark.asyncio
    async def test_empty_entity_names_returns_empty_without_db_call(self):
        pool = MagicMock()
        result = await find_entity_match_candidates(pool, "bank", [], fq_table_fn=lambda t: t)
        assert result == []
        pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        """DB failure → graceful empty, no exception propagated."""
        pool = MagicMock()
        with patch(
            "hindsight_api.engine.db_utils.acquire_with_retry",
            side_effect=RuntimeError("DB down"),
        ):
            result = await find_entity_match_candidates(
                pool, "bank", ["Alice"], fq_table_fn=lambda t: t
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_db_rows_become_entity_match_candidates(self):
        pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"engram_id": "e-42"}])

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "hindsight_api.engine.db_utils.acquire_with_retry",
            return_value=mock_ctx,
        ):
            result = await find_entity_match_candidates(
                pool, "bank", ["Alice"], fq_table_fn=lambda t: t
            )

        assert len(result) == 1
        assert result[0].engram_id == "e-42"
        assert result[0].source == "entity_match"
        assert result[0].score_bonus == 0.0
        assert result[0].similarity_score == 0.0


# ---------------------------------------------------------------------------
# merge_candidates
# ---------------------------------------------------------------------------


class TestMergeCandidates:
    def test_qdrant_only(self):
        q = [CandidateEngram("e-1", 0.8, "qdrant", QDRANT_SCORE_BONUS)]
        result = merge_candidates(q, [])
        assert len(result) == 1
        assert result[0].source == "qdrant"

    def test_entity_only(self):
        e = [CandidateEngram("e-1", 0.0, "entity_match", 0.0)]
        result = merge_candidates([], e)
        assert len(result) == 1
        assert result[0].source == "entity_match"

    def test_qdrant_first_then_entity(self):
        q = [CandidateEngram("e-q", 0.8, "qdrant", QDRANT_SCORE_BONUS)]
        e = [CandidateEngram("e-e", 0.0, "entity_match", 0.0)]
        result = merge_candidates(q, e)
        assert result[0].engram_id == "e-q"
        assert result[1].engram_id == "e-e"

    def test_duplicate_engram_id_qdrant_wins(self):
        q = [CandidateEngram("e-1", 0.8, "qdrant", QDRANT_SCORE_BONUS)]
        e = [CandidateEngram("e-1", 0.0, "entity_match", 0.0)]
        result = merge_candidates(q, e)
        assert len(result) == 1
        assert result[0].source == "qdrant"

    def test_entity_only_duplicate_deduplicated(self):
        e = [
            CandidateEngram("e-1", 0.0, "entity_match", 0.0),
            CandidateEngram("e-1", 0.0, "entity_match", 0.0),
        ]
        result = merge_candidates([], e)
        assert len(result) == 1

    def test_empty_both_returns_empty(self):
        assert merge_candidates([], []) == []

    def test_total_count_no_duplicates(self):
        q = [CandidateEngram(f"e-q{i}", 0.7, "qdrant", QDRANT_SCORE_BONUS) for i in range(3)]
        e = [CandidateEngram(f"e-e{i}", 0.0, "entity_match", 0.0) for i in range(3)]
        result = merge_candidates(q, e)
        assert len(result) == 6
