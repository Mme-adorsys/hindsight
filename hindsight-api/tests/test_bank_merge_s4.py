"""
Unit tests for Epic 14 Story 04 — Shared-Bank Query Integration (B4 + B6).

Tests the three Epic-14-S4 enhancements added to bank_merge.py:
  T2 — Schema-Boost: shared results with fact_type='schema' get +0.2 similarity
  T3 — Freshness Penalty: all shared scores multiplied by 0.95
  T4 — Cross-Bank Deduplication: shared results with ID already in agent bank dropped

Also verifies the existing merge_parallel_results integration path (T1 validation).
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.search.bank_merge import (
    SCHEMA_BOOST,
    SHARED_FRESHNESS_PENALTY,
    _apply_freshness_penalty,
    _apply_schema_boost,
    _deduplicate_shared,
    _scale_and_mark,
    get_bank_weights,
    merge_parallel_results,
)
from hindsight_api.engine.search.types import RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rr(
    id: str = "abc",
    text: str = "test",
    fact_type: str = "world",
    similarity: float | None = None,
    bm25_score: float | None = None,
    activation: float | None = None,
    temporal_score: float | None = None,
    source: str = "agent",
) -> RetrievalResult:
    return RetrievalResult(
        id=id,
        text=text,
        fact_type=fact_type,
        similarity=similarity,
        bm25_score=bm25_score,
        activation=activation,
        temporal_score=temporal_score,
        source=source,
    )


def _make_parallel(semantic=None, bm25=None, graph=None, temporal=None):
    """Build a minimal ParallelRetrievalResult for use in merge tests."""
    from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

    return ParallelRetrievalResult(
        semantic=semantic or [],
        bm25=bm25 or [],
        graph=graph or [],
        temporal=temporal,
        timings={},
        temporal_constraint=None,
        mpfp_timings=[],
    )


# ---------------------------------------------------------------------------
# T2 — Schema Boost
# ---------------------------------------------------------------------------


class TestApplySchemaBoost:
    def test_boosts_schema_similarity(self):
        r = _rr(fact_type="schema", similarity=0.5)
        _apply_schema_boost([r])
        assert r.similarity == pytest.approx(0.5 + SCHEMA_BOOST)

    def test_does_not_boost_non_schema(self):
        r = _rr(fact_type="world", similarity=0.5)
        _apply_schema_boost([r])
        assert r.similarity == pytest.approx(0.5)

    def test_does_not_boost_when_similarity_is_none(self):
        r = _rr(fact_type="schema", similarity=None)
        _apply_schema_boost([r])
        assert r.similarity is None

    def test_boost_is_additive_after_weight_scaling(self):
        """Schema boost must be applied after weight scaling, not before."""
        r = _rr(fact_type="schema", similarity=0.3)
        _apply_schema_boost([r], boost=0.2)
        assert r.similarity == pytest.approx(0.5)

    def test_empty_list_is_noop(self):
        _apply_schema_boost([])  # Must not raise


# ---------------------------------------------------------------------------
# T3 — Freshness Penalty
# ---------------------------------------------------------------------------


class TestApplyFreshnessPenalty:
    def test_applies_penalty_to_similarity(self):
        r = _rr(similarity=1.0)
        _apply_freshness_penalty([r])
        assert r.similarity == pytest.approx(SHARED_FRESHNESS_PENALTY)

    def test_applies_penalty_to_all_score_types(self):
        r = _rr(similarity=1.0, bm25_score=1.0, activation=1.0, temporal_score=1.0)
        _apply_freshness_penalty([r])
        assert r.similarity == pytest.approx(SHARED_FRESHNESS_PENALTY)
        assert r.bm25_score == pytest.approx(SHARED_FRESHNESS_PENALTY)
        assert r.activation == pytest.approx(SHARED_FRESHNESS_PENALTY)
        assert r.temporal_score == pytest.approx(SHARED_FRESHNESS_PENALTY)

    def test_skips_none_scores(self):
        r = _rr(similarity=None, bm25_score=0.8)
        _apply_freshness_penalty([r])
        assert r.similarity is None
        assert r.bm25_score == pytest.approx(0.8 * SHARED_FRESHNESS_PENALTY)

    def test_penalty_is_less_than_one(self):
        assert SHARED_FRESHNESS_PENALTY < 1.0

    def test_empty_list_is_noop(self):
        _apply_freshness_penalty([])


# ---------------------------------------------------------------------------
# T4 — Cross-Bank Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicateShared:
    def test_removes_shared_result_with_same_id_as_agent(self):
        agent = [_rr(id="eng-1", source="agent")]
        shared = [_rr(id="eng-1", source="shared")]
        _deduplicate_shared([agent], [shared])
        assert shared == []

    def test_keeps_shared_result_with_unique_id(self):
        agent = [_rr(id="eng-1", source="agent")]
        shared = [_rr(id="eng-2", source="shared")]
        _deduplicate_shared([agent], [shared])
        assert len(shared) == 1

    def test_deduplicates_across_multiple_lists(self):
        """Agent ID from bm25 list should deduplicate shared semantic list."""
        agent_semantic = [_rr(id="eng-1")]
        agent_bm25 = [_rr(id="eng-2")]
        shared_semantic = [_rr(id="eng-1"), _rr(id="eng-3")]
        _deduplicate_shared([agent_semantic, agent_bm25], [shared_semantic])
        assert len(shared_semantic) == 1
        assert shared_semantic[0].id == "eng-3"

    def test_partial_overlap_keeps_unique(self):
        agent = [_rr(id="eng-1"), _rr(id="eng-2")]
        shared = [_rr(id="eng-2"), _rr(id="eng-3"), _rr(id="eng-4")]
        _deduplicate_shared([agent], [shared])
        assert [r.id for r in shared] == ["eng-3", "eng-4"]

    def test_empty_agent_keeps_all_shared(self):
        shared = [_rr(id="eng-1"), _rr(id="eng-2")]
        _deduplicate_shared([], [shared])
        assert len(shared) == 2

    def test_empty_shared_is_noop(self):
        agent = [_rr(id="eng-1")]
        _deduplicate_shared([agent], [])


# ---------------------------------------------------------------------------
# T1 + Integration — merge_parallel_results
# ---------------------------------------------------------------------------


class TestMergeParallelResultsIntegration:
    def test_agent_results_marked_with_source_agent(self):
        agent_rr = _make_parallel(semantic=[_rr(id="a1", similarity=0.8)])
        shared_rr = _make_parallel()
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        assert merged.semantic[0].source == "agent"

    def test_shared_results_marked_with_source_shared(self):
        agent_rr = _make_parallel()
        shared_rr = _make_parallel(semantic=[_rr(id="s1", similarity=0.8)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        assert merged.semantic[0].source == "shared"

    def test_precision_mode_weights_agent_higher(self):
        """In precision mode agent weight=0.7, shared weight=0.3."""

        class _Mode:
            value = "precision"

        agent_rr = _make_parallel(semantic=[_rr(id="a1", similarity=1.0)])
        shared_rr = _make_parallel(semantic=[_rr(id="s1", similarity=1.0)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=_Mode())
        agent_result = next(r for r in merged.semantic if r.id == "a1")
        shared_result = next(r for r in merged.semantic if r.id == "s1")
        assert agent_result.similarity > shared_result.similarity

    def test_exploration_mode_weights_shared_higher(self):
        """In exploration mode shared weight=0.7, agent weight=0.3."""

        class _Mode:
            value = "exploration"

        agent_rr = _make_parallel(semantic=[_rr(id="a1", similarity=1.0)])
        shared_rr = _make_parallel(semantic=[_rr(id="s1", similarity=1.0)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=_Mode())
        agent_result = next(r for r in merged.semantic if r.id == "a1")
        shared_result = next(r for r in merged.semantic if r.id == "s1")
        # shared gets 0.7 * 0.95 (freshness) = 0.665; agent gets 0.3
        assert shared_result.similarity > agent_result.similarity

    def test_schema_boost_applied_to_shared_schema_results(self):
        agent_rr = _make_parallel()
        shared_rr = _make_parallel(semantic=[_rr(id="schema-1", fact_type="schema", similarity=1.0)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        schema_result = merged.semantic[0]
        # Expected: 1.0 * shared_w(0.3) + boost(0.2), then * penalty(0.95)
        expected = (1.0 * 0.3 + SCHEMA_BOOST) * SHARED_FRESHNESS_PENALTY
        assert schema_result.similarity == pytest.approx(expected)

    def test_freshness_penalty_applied_to_shared_results(self):
        agent_rr = _make_parallel()
        shared_rr = _make_parallel(bm25=[_rr(id="s1", bm25_score=1.0)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        s1 = merged.bm25[0]
        # shared_w(0.3) * penalty(0.95)
        assert s1.bm25_score == pytest.approx(0.3 * SHARED_FRESHNESS_PENALTY)

    def test_deduplication_drops_shared_copy_of_agent_engram(self):
        agent_rr = _make_parallel(semantic=[_rr(id="eng-1", similarity=0.9)])
        shared_rr = _make_parallel(semantic=[_rr(id="eng-1", similarity=0.8), _rr(id="s2", similarity=0.7)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        ids = [r.id for r in merged.semantic]
        assert ids.count("eng-1") == 1  # Only agent copy survives
        assert "s2" in ids

    def test_agent_version_kept_over_shared_duplicate(self):
        agent_rr = _make_parallel(semantic=[_rr(id="eng-1", source="agent", similarity=0.9)])
        shared_rr = _make_parallel(semantic=[_rr(id="eng-1", source="shared", similarity=0.8)])
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        eng1 = next(r for r in merged.semantic if r.id == "eng-1")
        assert eng1.source == "agent"

    def test_timings_merged_with_shared_prefix(self):
        agent_rr = _make_parallel()
        agent_rr.timings = {"semantic": 0.1}
        shared_rr = _make_parallel()
        shared_rr.timings = {"semantic": 0.2}
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        assert "semantic" in merged.timings
        assert "shared_semantic" in merged.timings
