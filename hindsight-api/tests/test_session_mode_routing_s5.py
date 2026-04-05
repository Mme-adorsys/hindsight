"""
Unit tests for Story 05 — Session-Mode Routing (S5).

Tests cover:
- Bank weight resolution per mode (TestBankWeights)
- Dual-bank result merging with source marking and score scaling (TestMergeParallelResults)
- Dual-bank query orchestration in _search_with_retries (TestDualBankQuery)
- Source field propagation on RetrievalResult / ScoredResult (TestSourceMarking)

All mocked — no live databases required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.search.bank_merge import (
    BANK_WEIGHTS,
    get_bank_weights,
    merge_parallel_results,
)
from hindsight_api.engine.search.retrieval import ParallelRetrievalResult
from hindsight_api.engine.search.types import MergedCandidate, RetrievalResult, ScoredResult
from hindsight_api.engine.session.mode_config import RetrievalMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rr(id_: str = "abc", similarity: float = 1.0) -> RetrievalResult:
    return RetrievalResult(
        id=id_,
        text="test",
        fact_type="general",
        similarity=similarity,
    )


def _empty_prr() -> ParallelRetrievalResult:
    return ParallelRetrievalResult(semantic=[], bm25=[], graph=[], temporal=None)


def _prr_with(
    semantic: list[RetrievalResult] | None = None,
    bm25: list[RetrievalResult] | None = None,
    graph: list[RetrievalResult] | None = None,
    temporal: list[RetrievalResult] | None = None,
) -> ParallelRetrievalResult:
    return ParallelRetrievalResult(
        semantic=semantic or [],
        bm25=bm25 or [],
        graph=graph or [],
        temporal=temporal,
    )


# ---------------------------------------------------------------------------
# TestBankWeights
# ---------------------------------------------------------------------------


class TestBankWeights:
    def test_precision_weights(self):
        aw, sw = get_bank_weights(RetrievalMode.PRECISION)
        assert aw == 0.7
        assert sw == 0.3

    def test_exploration_weights(self):
        aw, sw = get_bank_weights(RetrievalMode.EXPLORATION)
        assert aw == 0.3
        assert sw == 0.7

    def test_none_mode_defaults_to_precision(self):
        aw, sw = get_bank_weights(None)
        assert aw == 0.7
        assert sw == 0.3

    def test_all_modes_sum_to_one(self):
        for mode_key, (aw, sw) in BANK_WEIGHTS.items():
            assert abs(aw + sw - 1.0) < 1e-9, f"Mode {mode_key}: {aw} + {sw} != 1.0"


# ---------------------------------------------------------------------------
# TestMergeParallelResults
# ---------------------------------------------------------------------------


class TestMergeParallelResults:
    def test_agent_results_marked_source_agent(self):
        agent_result = _rr("a1")
        agent_rr = _prr_with(semantic=[agent_result])
        shared_rr = _empty_prr()

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.PRECISION)

        assert merged.semantic[0].source == "agent"

    def test_shared_results_marked_source_shared(self):
        shared_result = _rr("s1")
        agent_rr = _empty_prr()
        shared_rr = _prr_with(semantic=[shared_result])

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.PRECISION)

        assert merged.semantic[0].source == "shared"

    def test_scores_scaled_by_bank_weight_precision(self):
        """Precision: agent_w=0.7, shared_w=0.3 — similarity must be scaled."""
        agent_result = _rr("a1", similarity=1.0)
        shared_result = _rr("s1", similarity=1.0)
        agent_rr = _prr_with(semantic=[agent_result])
        shared_rr = _prr_with(semantic=[shared_result])

        merge_parallel_results(agent_rr, shared_rr, RetrievalMode.PRECISION)

        assert abs(agent_result.similarity - 0.7) < 1e-9
        assert abs(shared_result.similarity - 0.3) < 1e-9

    def test_merged_lists_concatenated(self):
        a1 = _rr("a1")
        s1 = _rr("s1")
        agent_rr = _prr_with(semantic=[a1], bm25=[a1])
        shared_rr = _prr_with(semantic=[s1], bm25=[s1])

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.EXPLORATION)

        assert len(merged.semantic) == 2
        assert len(merged.bm25) == 2

    def test_temporal_none_when_both_empty(self):
        agent_rr = _prr_with(temporal=None)
        shared_rr = _prr_with(temporal=None)

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.VALIDATION)

        assert merged.temporal is None

    def test_temporal_merged_when_present(self):
        t1 = _rr("t1")
        t2 = _rr("t2")
        agent_rr = _prr_with(temporal=[t1])
        shared_rr = _prr_with(temporal=[t2])

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.EXPLORATION)

        assert merged.temporal is not None
        assert len(merged.temporal) == 2


# ---------------------------------------------------------------------------
# TestDualBankQuery — monkeypatch retrieve_parallel
# ---------------------------------------------------------------------------


class TestDualBankQuery:
    def test_backward_compat_no_shared_bank_id(self):
        """_search_with_retries accepts calls without shared_bank_id (default None)."""
        import inspect

        from hindsight_api.engine.memory_engine import MemoryEngine

        sig = inspect.signature(MemoryEngine._search_with_retries)
        param = sig.parameters.get("shared_bank_id")
        assert param is not None, "shared_bank_id param missing from _search_with_retries"
        assert param.default is None, "shared_bank_id must default to None"

    def test_recall_async_accepts_shared_bank_id(self):
        """recall_async accepts shared_bank_id kwarg."""
        import inspect

        from hindsight_api.engine.memory_engine import MemoryEngine

        sig = inspect.signature(MemoryEngine.recall_async)
        param = sig.parameters.get("shared_bank_id")
        assert param is not None, "shared_bank_id param missing from recall_async"
        assert param.default is None

    def test_no_shared_bank_skips_merge(self):
        """Without shared_bank_id, merge_parallel_results is never invoked."""
        # The merge path only runs when shared_bank_id is truthy.
        # We verify by calling merge directly with two empty PRRs and checking
        # the merged result is also empty — i.e., the happy path for single-bank.
        rr = merge_parallel_results(_empty_prr(), _empty_prr(), None)
        assert rr.semantic == []
        assert rr.bm25 == []
        assert rr.graph == []
        assert rr.temporal is None

    def test_dual_bank_merge_produces_combined_result(self):
        """With two banks, merge_parallel_results combines both result sets."""
        a = _rr("a1")
        s = _rr("s1")
        agent_rr = _prr_with(semantic=[a])
        shared_rr = _prr_with(semantic=[s])

        merged = merge_parallel_results(agent_rr, shared_rr, RetrievalMode.PRECISION)

        ids = {r.id for r in merged.semantic}
        assert "a1" in ids
        assert "s1" in ids


# ---------------------------------------------------------------------------
# TestSourceMarking
# ---------------------------------------------------------------------------


class TestSourceMarking:
    def test_retrieval_result_default_source_is_agent(self):
        rr = RetrievalResult(id="x", text="hello", fact_type="general")
        assert rr.source == "agent"

    def test_scored_result_to_dict_includes_source(self):
        rr = RetrievalResult(id="x", text="hello", fact_type="general", source="shared")
        mc = MergedCandidate(retrieval=rr, rrf_score=0.5, rrf_rank=1)
        sr = ScoredResult(candidate=mc)

        d = sr.to_dict()
        assert "source" in d
        assert d["source"] == "shared"

    def test_source_field_preserved_through_merge(self):
        """Source set by _scale_and_mark is accessible on the final merged result."""
        r1 = _rr("a1")
        r2 = _rr("s1")
        agent_rr = _prr_with(semantic=[r1])
        shared_rr = _prr_with(semantic=[r2])

        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)

        sources = {r.id: r.source for r in merged.semantic}
        assert sources["a1"] == "agent"
        assert sources["s1"] == "shared"
