"""
Unit tests for Epic 07 Story 02 — Mode-aware MPFP Patterns (S2).

Tests validate WITHOUT a running database:
- MPFPPatternSet is immutable and correctly structured
- MODE_PATTERNS covers all 4 RetrievalModes
- Mode-specific pattern properties (length, link types, thresholds, top_k)
- MPFPGraphRetriever.retrieve() selects correct patterns when mode is given
- Fallback to MPFPConfig defaults when mode is None
- mode kwarg flows from retrieve_parallel → _retrieve_parallel_mpfp → retriever.retrieve()
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.search.mpfp_retrieval import (
    MODE_PATTERNS,
    MPFPConfig,
    MPFPGraphRetriever,
    MPFPPatternSet,
    PatternResult,
)
from hindsight_api.engine.search.types import RetrievalResult


# ---------------------------------------------------------------------------
# TestMPFPPatternSet — structure + immutability
# ---------------------------------------------------------------------------


class TestMPFPPatternSet:
    def test_all_four_modes_have_pattern_set(self):
        """Every RetrievalMode value must be present in MODE_PATTERNS."""
        for mode in RetrievalMode:
            assert mode.value in MODE_PATTERNS, f"Missing pattern set for mode: {mode.value}"

    def test_pattern_set_is_frozen(self):
        """MPFPPatternSet must be immutable (frozen dataclass)."""
        ps = MODE_PATTERNS["precision"]
        with pytest.raises((FrozenInstanceError, AttributeError)):
            ps.threshold = 999.0

    def test_all_modes_have_at_least_one_semantic_pattern(self):
        for mode_key, ps in MODE_PATTERNS.items():
            assert len(ps.semantic_patterns) >= 1, f"Mode {mode_key} has no semantic patterns"

    def test_precision_patterns_are_short(self):
        """Precision mode: all semantic patterns must be ≤ 2 hops (short paths)."""
        ps = MODE_PATTERNS["precision"]
        for pattern in ps.semantic_patterns:
            assert len(pattern) <= 2, f"Precision pattern too long: {pattern}"

    def test_exploration_includes_co_activated(self):
        """Exploration mode must include co_activated link type (weak connections)."""
        ps = MODE_PATTERNS["exploration"]
        all_edges = {edge for pattern in ps.semantic_patterns for edge in pattern}
        assert "co_activated" in all_edges

    def test_exploration_includes_temporal_proximity(self):
        """Exploration mode must include temporal_proximity link type."""
        ps = MODE_PATTERNS["exploration"]
        all_edges = {edge for pattern in ps.semantic_patterns for edge in pattern}
        assert "temporal_proximity" in all_edges

    def test_analogy_includes_schema(self):
        """Analogy mode must include schema link type for cross-domain traversal."""
        ps = MODE_PATTERNS["analogy"]
        all_edges = {edge for pattern in ps.semantic_patterns for edge in pattern}
        assert "schema" in all_edges

    def test_validation_includes_contradiction(self):
        """Validation mode must include contradiction link type for evidence checking."""
        ps = MODE_PATTERNS["validation"]
        all_edges = {edge for pattern in ps.semantic_patterns for edge in pattern}
        assert "contradiction" in all_edges

    def test_validation_includes_causal_links(self):
        """Validation mode must include causal link types (causes, caused_by)."""
        ps = MODE_PATTERNS["validation"]
        all_edges = {edge for pattern in ps.semantic_patterns for edge in pattern}
        assert "causes" in all_edges or "caused_by" in all_edges

    def test_precision_higher_threshold_than_exploration(self):
        """Precision prunes aggressively (higher threshold = less exploration)."""
        assert MODE_PATTERNS["precision"].threshold > MODE_PATTERNS["exploration"].threshold

    def test_exploration_higher_top_k_than_precision(self):
        """Exploration returns more results (higher top_k = broader recall)."""
        assert MODE_PATTERNS["exploration"].top_k > MODE_PATTERNS["precision"].top_k

    def test_analogy_medium_threshold(self):
        """Analogy threshold must be between precision and exploration."""
        assert (
            MODE_PATTERNS["exploration"].threshold
            < MODE_PATTERNS["analogy"].threshold
            < MODE_PATTERNS["precision"].threshold
        )


# ---------------------------------------------------------------------------
# TestMPFPGraphRetrieverModeAware — pattern selection in retrieve()
# ---------------------------------------------------------------------------


class TestMPFPGraphRetrieverModeAware:
    """Verify that MPFPGraphRetriever selects the correct pattern set by mode."""

    def _make_seed(self, unit_id: str = "seed-1", score: float = 0.9) -> RetrievalResult:
        return RetrievalResult(id=unit_id, text="seed", fact_type="world", similarity=score)

    @pytest.mark.asyncio
    async def test_none_mode_uses_config_defaults(self):
        """When mode=None, retriever must use MPFPConfig.patterns_semantic (not MODE_PATTERNS)."""
        retriever = MPFPGraphRetriever()
        captured_patterns = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_patterns.append(pattern)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        with (
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                side_effect=mock_traverse,
            ),
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                side_effect=mock_fetch,
            ),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                budget=10,
                semantic_seeds=[self._make_seed()],
                mode=None,
            )

        default_patterns = retriever.config.patterns_semantic
        assert len(captured_patterns) == len(default_patterns), (
            "Without mode, should use all default config patterns"
        )
        for captured, expected in zip(captured_patterns, default_patterns):
            assert captured == expected

    @pytest.mark.asyncio
    async def test_precision_mode_selects_precision_patterns(self):
        """With PRECISION mode, only precision patterns should be traversed."""
        retriever = MPFPGraphRetriever()
        captured_patterns = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_patterns.append(pattern)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        with (
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                side_effect=mock_traverse,
            ),
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                side_effect=mock_fetch,
            ),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                budget=10,
                semantic_seeds=[self._make_seed()],
                mode=RetrievalMode.PRECISION,
            )

        precision_ps = MODE_PATTERNS["precision"]
        expected_patterns = [list(p) for p in precision_ps.semantic_patterns]
        assert captured_patterns == expected_patterns

    @pytest.mark.asyncio
    async def test_exploration_mode_selects_exploration_patterns(self):
        """With EXPLORATION mode, exploration patterns (including co_activated) must be used."""
        retriever = MPFPGraphRetriever()
        captured_patterns = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_patterns.append(pattern)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        with (
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                side_effect=mock_traverse,
            ),
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                side_effect=mock_fetch,
            ),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                budget=50,
                semantic_seeds=[self._make_seed()],
                mode=RetrievalMode.EXPLORATION,
            )

        exploration_ps = MODE_PATTERNS["exploration"]
        expected_patterns = [list(p) for p in exploration_ps.semantic_patterns]
        assert captured_patterns == expected_patterns
        # co_activated must appear in at least one traversed pattern
        all_edges = {edge for p in captured_patterns for edge in p}
        assert "co_activated" in all_edges

    @pytest.mark.asyncio
    async def test_precision_uses_higher_threshold_than_exploration(self):
        """Precision mode must pass a higher threshold to mpfp_traverse_async than exploration."""
        retriever = MPFPGraphRetriever()
        captured_thresholds: list[float] = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_thresholds.append(config.threshold)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        seed = self._make_seed()

        for mode in (RetrievalMode.PRECISION, RetrievalMode.EXPLORATION):
            captured_thresholds.clear()
            with (
                patch(
                    "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                    side_effect=mock_traverse,
                ),
                patch(
                    "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                    side_effect=mock_fetch,
                ),
            ):
                await retriever.retrieve(
                    pool=MagicMock(),
                    query_embedding_str="[0.1]",
                    bank_id="bank-1",
                    budget=10,
                    semantic_seeds=[seed],
                    mode=mode,
                )

            if mode == RetrievalMode.PRECISION:
                precision_threshold = captured_thresholds[0]
            else:
                exploration_threshold = captured_thresholds[0]

        assert precision_threshold > exploration_threshold

    @pytest.mark.asyncio
    async def test_analogy_mode_traverses_schema_patterns(self):
        """Analogy mode must include schema link type in traversed patterns."""
        retriever = MPFPGraphRetriever()
        captured_patterns = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_patterns.append(pattern)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        with (
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                side_effect=mock_traverse,
            ),
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                side_effect=mock_fetch,
            ),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                budget=20,
                semantic_seeds=[self._make_seed()],
                mode=RetrievalMode.ANALOGY,
            )

        all_edges = {edge for p in captured_patterns for edge in p}
        assert "schema" in all_edges

    @pytest.mark.asyncio
    async def test_validation_mode_traverses_causal_contradiction_patterns(self):
        """Validation mode must include causal/contradiction patterns."""
        retriever = MPFPGraphRetriever()
        captured_patterns = []

        async def mock_traverse(pool, seeds, pattern, config, cache):
            captured_patterns.append(pattern)
            return PatternResult(pattern=pattern, scores={})

        async def mock_fetch(pool, node_ids, **kwargs):
            return []

        with (
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.mpfp_traverse_async",
                side_effect=mock_traverse,
            ),
            patch(
                "hindsight_api.engine.search.mpfp_retrieval.fetch_memory_units_by_ids",
                side_effect=mock_fetch,
            ),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                budget=20,
                semantic_seeds=[self._make_seed()],
                mode=RetrievalMode.VALIDATION,
            )

        all_edges = {edge for p in captured_patterns for edge in p}
        assert "contradiction" in all_edges
        assert "causes" in all_edges or "caused_by" in all_edges


# ---------------------------------------------------------------------------
# TestRetrieveParallelModeFlow — mode flows from retrieve_parallel to retriever
# ---------------------------------------------------------------------------


class TestRetrieveParallelModeFlow:
    """Verify mode kwarg is forwarded through the call chain."""

    @pytest.mark.asyncio
    async def test_mode_reaches_retriever_in_mpfp_path(self):
        """retrieve_parallel with mpfp retriever must pass mode to retriever.retrieve()."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult, retrieve_parallel

        captured_mode = {}

        async def mock_retrieve(*args, **kwargs):
            captured_mode["mode"] = kwargs.get("mode")
            return [], None

        mock_retriever = MagicMock()
        mock_retriever.name = "mpfp"
        mock_retriever.retrieve = mock_retrieve

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry",
                return_value=acquire_ctx,
            ),
            patch(
                "hindsight_api.engine.search.temporal_extraction.extract_temporal_constraint",
                return_value=None,
            ),
        ):
            await retrieve_parallel(
                pool=MagicMock(),
                query_text="test",
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                thinking_budget=10,
                graph_retriever=mock_retriever,
                mode=RetrievalMode.EXPLORATION,
            )

        assert captured_mode.get("mode") == RetrievalMode.EXPLORATION

    @pytest.mark.asyncio
    async def test_none_mode_forwarded_as_none(self):
        """When mode=None, retriever.retrieve() must receive mode=None."""
        from hindsight_api.engine.search.retrieval import retrieve_parallel

        captured_mode = {"mode": "NOT_SET"}

        async def mock_retrieve(*args, **kwargs):
            captured_mode["mode"] = kwargs.get("mode", "NOT_SET")
            return [], None

        mock_retriever = MagicMock()
        mock_retriever.name = "mpfp"
        mock_retriever.retrieve = mock_retrieve

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry",
                return_value=acquire_ctx,
            ),
            patch(
                "hindsight_api.engine.search.temporal_extraction.extract_temporal_constraint",
                return_value=None,
            ),
        ):
            await retrieve_parallel(
                pool=MagicMock(),
                query_text="test",
                query_embedding_str="[0.1]",
                bank_id="bank-1",
                thinking_budget=10,
                graph_retriever=mock_retriever,
                mode=None,
            )

        assert captured_mode["mode"] is None
