"""
Unit tests for Epic 07 Story 04 — EngramRetriever (S6).

Tests validate WITHOUT live Qdrant / Neo4j / PostgreSQL:
- Qdrant seed phase: uses semantic_seeds when provided, calls Qdrant otherwise
- Neo4j traversal phase: called per seed, depth/rel_types depend on mode
- Activation score decays with depth
- Enrichment: builds RetrievalResult list, respects budget
- retrieve() end-to-end with mocked sub-methods
- RetrieverRegistry: default and override routing
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.search.engram_retrieval import (
    EngramRetriever,
    _SEED_LIMITS,
    _resolve_mode_params,
)
from hindsight_api.engine.search.retrieval import RetrieverRegistry, build_retriever_registry
from hindsight_api.engine.search.types import MPFPTimings, RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(unit_id: str = "unit-1", similarity: float = 0.9) -> RetrievalResult:
    return RetrievalResult(id=unit_id, text="test text", fact_type="world", similarity=similarity)


def _make_retriever() -> EngramRetriever:
    qdrant = MagicMock()
    neo4j = MagicMock()
    return EngramRetriever(qdrant=qdrant, neo4j=neo4j)


# ---------------------------------------------------------------------------
# TestResolveModeParams — module-level helper
# ---------------------------------------------------------------------------


class TestResolveModeParams:
    def test_none_mode_returns_defaults(self):
        from hindsight_api.engine.neo4j_client import RELATIONSHIP_TYPES

        seed_limit, rel_types, max_depth = _resolve_mode_params(None)
        assert seed_limit == 10
        assert set(rel_types) == set(RELATIONSHIP_TYPES)
        assert max_depth == 2

    def test_precision_returns_5_seeds_and_depth_1(self):
        seed_limit, _, max_depth = _resolve_mode_params(RetrievalMode.PRECISION)
        assert seed_limit == 5
        assert max_depth == 1

    def test_exploration_returns_20_seeds_and_depth_3(self):
        seed_limit, _, max_depth = _resolve_mode_params(RetrievalMode.EXPLORATION)
        assert seed_limit == 20
        assert max_depth == 3

    def test_analogy_returns_10_seeds(self):
        seed_limit, _, _ = _resolve_mode_params(RetrievalMode.ANALOGY)
        assert seed_limit == 10

    def test_validation_returns_10_seeds(self):
        seed_limit, _, _ = _resolve_mode_params(RetrievalMode.VALIDATION)
        assert seed_limit == 10

    def test_all_modes_have_seed_limits(self):
        for mode in RetrievalMode:
            assert mode.value in _SEED_LIMITS, f"Missing seed limit for {mode}"


# ---------------------------------------------------------------------------
# TestEngramRetrieverSeeds — T2
# ---------------------------------------------------------------------------


class TestEngramRetrieverSeeds:
    @pytest.mark.asyncio
    async def test_uses_semantic_seeds_if_provided(self):
        """If semantic_seeds are given, Qdrant must NOT be called."""
        retriever = _make_retriever()
        seeds = [_make_result("seed-1"), _make_result("seed-2")]

        retriever._traverse = AsyncMock(return_value={})
        retriever._enrich = AsyncMock(return_value=[])

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="bank-1",
            budget=10,
            semantic_seeds=seeds,
        )

        retriever._qdrant.search_similar.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_qdrant_when_no_seeds(self):
        """Without semantic_seeds, Qdrant search_similar must be called."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": "e-1", "score": 0.9, "payload": {}}]
        )
        retriever._traverse = AsyncMock(return_value={})
        retriever._enrich = AsyncMock(return_value=[])

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1, 0.2]",
            bank_id="bank-1",
            budget=10,
            semantic_seeds=None,
        )

        retriever._qdrant.search_similar.assert_called_once()
        call_kwargs = retriever._qdrant.search_similar.call_args
        assert call_kwargs.kwargs["limit"] == 10 or call_kwargs.args[1] == 10

    @pytest.mark.asyncio
    async def test_qdrant_filter_includes_bank_id(self):
        """Qdrant query must filter by bank_id."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(return_value=[])
        retriever._traverse = AsyncMock(return_value={})
        retriever._enrich = AsyncMock(return_value=[])

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="my-bank",
            budget=5,
        )

        call_kwargs = retriever._qdrant.search_similar.call_args
        filters = call_kwargs.kwargs.get("filters") or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
        assert filters is not None
        must = filters.get("must", [])
        bank_filter = any(f.get("key") == "bank_id" and f.get("match", {}).get("value") == "my-bank" for f in must)
        assert bank_filter, "Qdrant filter must include bank_id"

    @pytest.mark.asyncio
    async def test_precision_mode_limits_to_5_seeds(self):
        """PRECISION mode: Qdrant limit must be 5."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(return_value=[])
        retriever._traverse = AsyncMock(return_value={})
        retriever._enrich = AsyncMock(return_value=[])

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="bank-1",
            budget=20,
            mode=RetrievalMode.PRECISION,
        )

        call = retriever._qdrant.search_similar.call_args
        limit = call.kwargs.get("limit") or call.args[1]
        assert limit == 5

    @pytest.mark.asyncio
    async def test_exploration_mode_limits_to_20_seeds(self):
        """EXPLORATION mode: Qdrant limit must be 20."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(return_value=[])
        retriever._traverse = AsyncMock(return_value={})
        retriever._enrich = AsyncMock(return_value=[])

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="bank-1",
            budget=50,
            mode=RetrievalMode.EXPLORATION,
        )

        call = retriever._qdrant.search_similar.call_args
        limit = call.kwargs.get("limit") or call.args[1]
        assert limit == 20


# ---------------------------------------------------------------------------
# TestEngramRetrieverTraversal — T3
# ---------------------------------------------------------------------------


class TestEngramRetrieverTraversal:
    @pytest.mark.asyncio
    async def test_traversal_called_per_seed(self):
        """Neo4j traverse() must be called once per seed ID."""
        retriever = _make_retriever()
        retriever._neo4j.traverse = AsyncMock(return_value=[])

        await retriever._traverse(
            seed_ids=["s1", "s2", "s3"],
            rel_types=["SEMANTIC"],
            max_depth=2,
        )

        assert retriever._neo4j.traverse.call_count == 3

    @pytest.mark.asyncio
    async def test_depth_shallow_for_precision(self):
        """PRECISION mode: max_depth passed to traverse must be 1."""
        retriever = _make_retriever()
        called_depths = []

        async def mock_traverse(start_id, rel_types, max_depth, min_weight):
            called_depths.append(max_depth)
            return []

        retriever._neo4j.traverse = mock_traverse
        _, rel_types, max_depth = _resolve_mode_params(RetrievalMode.PRECISION)

        await retriever._traverse(["s1"], rel_types, max_depth)
        assert called_depths == [1]

    @pytest.mark.asyncio
    async def test_depth_deep_for_exploration(self):
        """EXPLORATION mode: max_depth must be 3."""
        retriever = _make_retriever()
        called_depths = []

        async def mock_traverse(start_id, rel_types, max_depth, min_weight):
            called_depths.append(max_depth)
            return []

        retriever._neo4j.traverse = mock_traverse
        _, rel_types, max_depth = _resolve_mode_params(RetrievalMode.EXPLORATION)

        await retriever._traverse(["s1"], rel_types, max_depth)
        assert called_depths == [3]

    @pytest.mark.asyncio
    async def test_activation_decays_with_depth(self):
        """Nodes at depth=1 get higher activation than depth=2."""
        retriever = _make_retriever()

        async def mock_traverse(start_id, rel_types, max_depth, min_weight):
            return [
                {"engram_id": "shallow", "depth": 1, "properties": {}},
                {"engram_id": "deep", "depth": 2, "properties": {}},
            ]

        retriever._neo4j.traverse = mock_traverse
        result = await retriever._traverse(["seed"], ["SEMANTIC"], 3)

        assert result["shallow"] > result["deep"], "Shallow nodes must have higher activation"

    @pytest.mark.asyncio
    async def test_deduplication_keeps_max_activation(self):
        """If same node reached via multiple seeds, keep highest activation score."""
        retriever = _make_retriever()
        call_count = 0

        async def mock_traverse(start_id, rel_types, max_depth, min_weight):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"engram_id": "shared", "depth": 1, "properties": {}}]
            else:
                return [{"engram_id": "shared", "depth": 2, "properties": {}}]

        retriever._neo4j.traverse = mock_traverse
        result = await retriever._traverse(["s1", "s2"], ["SEMANTIC"], 3)

        # depth=1 gives 1/(1+1)=0.5; depth=2 gives 1/(1+2)=0.333; keep max
        assert result["shared"] == pytest.approx(1.0 / (1.0 + 1))

    @pytest.mark.asyncio
    async def test_traversal_failure_per_seed_is_non_fatal(self):
        """If one seed's traversal fails, others must still succeed."""
        retriever = _make_retriever()
        call_count = 0

        async def mock_traverse(start_id, rel_types, max_depth, min_weight):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Neo4j down")
            return [{"engram_id": "ok-node", "depth": 1, "properties": {}}]

        retriever._neo4j.traverse = mock_traverse
        result = await retriever._traverse(["bad", "good"], ["SEMANTIC"], 2)

        assert "ok-node" in result
        assert "bad" not in result


# ---------------------------------------------------------------------------
# TestEngramRetrieverEnrich — T4
# ---------------------------------------------------------------------------


class TestEngramRetrieverEnrich:
    @pytest.mark.asyncio
    async def test_enrichment_builds_retrieval_results(self):
        """Enrichment must return RetrievalResult objects with correct activation."""
        import uuid

        retriever = _make_retriever()
        eid = str(uuid.uuid4())

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": eid,
                    "text": "hello world",
                    "fact_type": "world",
                    "context": None,
                    "occurred_start": None,
                    "occurred_end": None,
                    "mentioned_at": None,
                    "access_count": 0,
                    "document_id": None,
                    "chunk_id": None,
                }
            ]
        )
        mock_pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("hindsight_api.engine.search.engram_retrieval.acquire_with_retry", return_value=acquire_ctx):
            results = await retriever._enrich(mock_pool, "bank-1", {eid: 0.75}, budget=10)

        assert len(results) == 1
        assert results[0].id == eid
        assert results[0].activation == pytest.approx(0.75)
        assert results[0].text == "hello world"

    @pytest.mark.asyncio
    async def test_results_truncated_to_budget(self):
        """Enrichment must return at most `budget` results."""
        import uuid

        retriever = _make_retriever()
        ids = [str(uuid.uuid4()) for _ in range(5)]
        activation_map = {eid: float(i) / 10 for i, eid in enumerate(ids, start=1)}

        db_rows = [
            {
                "id": eid,
                "text": f"text-{i}",
                "fact_type": "world",
                "context": None,
                "occurred_start": None,
                "occurred_end": None,
                "mentioned_at": None,
                "access_count": 0,
                "document_id": None,
                "chunk_id": None,
            }
            for i, eid in enumerate(ids)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=db_rows)
        mock_pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("hindsight_api.engine.search.engram_retrieval.acquire_with_retry", return_value=acquire_ctx):
            results = await retriever._enrich(mock_pool, "bank-1", activation_map, budget=3)

        assert len(results) <= 3


# ---------------------------------------------------------------------------
# TestEngramRetrieverRetrieve — T5
# ---------------------------------------------------------------------------


class TestEngramRetrieverRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_returns_tuple(self):
        """retrieve() must return (list[RetrievalResult], MPFPTimings | None)."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": "e-1", "score": 0.9, "payload": {}}]
        )
        retriever._neo4j.traverse = AsyncMock(return_value=[])

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("hindsight_api.engine.search.engram_retrieval.acquire_with_retry", return_value=acquire_ctx):
            result = await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str="[0.1, 0.2]",
                bank_id="bank-1",
                budget=10,
            )

        assert isinstance(result, tuple)
        assert len(result) == 2
        results_list, timings = result
        assert isinstance(results_list, list)

    @pytest.mark.asyncio
    async def test_seeds_injected_with_activation_1_0(self):
        """Seeds themselves must appear in activation_map at activation=1.0."""
        retriever = _make_retriever()
        seen_maps: list[dict] = []

        async def mock_enrich(pool, bank_id, activation_map, budget):
            seen_maps.append(dict(activation_map))
            return []

        retriever._qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": "seed-1", "score": 0.9, "payload": {}}]
        )
        retriever._neo4j.traverse = AsyncMock(return_value=[])
        retriever._enrich = mock_enrich

        await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="bank-1",
            budget=10,
        )

        assert seen_maps, "enrich must be called"
        assert seen_maps[0].get("seed-1") == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_empty_seeds_returns_empty_no_crash(self):
        """If Qdrant returns no seeds, retrieve() must return ([], None) without error."""
        retriever = _make_retriever()
        retriever._qdrant.search_similar = AsyncMock(return_value=[])

        result, timings = await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="[0.1]",
            bank_id="bank-1",
            budget=10,
        )
        assert result == []
        assert timings is None

    @pytest.mark.asyncio
    async def test_invalid_embedding_string_returns_empty(self):
        """Malformed query_embedding_str must not crash — returns ([], None)."""
        retriever = _make_retriever()

        result, timings = await retriever.retrieve(
            pool=MagicMock(),
            query_embedding_str="not-json",
            bank_id="bank-1",
            budget=10,
        )
        assert result == []
        assert timings is None

    def test_name_property(self):
        retriever = _make_retriever()
        assert retriever.name == "engram"


# ---------------------------------------------------------------------------
# TestRetrieverRegistry — T6
# ---------------------------------------------------------------------------


class TestRetrieverRegistry:
    def test_returns_default_for_unknown_bank(self):
        default = MagicMock()
        default.name = "mpfp"
        registry = RetrieverRegistry(default=default)

        result = registry.get("unknown-bank")
        assert result is default

    def test_returns_override_for_registered_bank(self):
        default = MagicMock()
        default.name = "mpfp"
        override = MagicMock()
        override.name = "engram"

        registry = RetrieverRegistry(default=default)
        registry.register("shared-bank", override)

        assert registry.get("shared-bank") is override

    def test_register_overrides_default(self):
        default = MagicMock()
        registry = RetrieverRegistry(default=default)
        override = MagicMock()

        registry.register("bank-x", override)
        assert registry.get("bank-x") is override
        assert registry.get("bank-y") is default  # unregistered still gets default

    def test_build_retriever_registry_applies_overrides(self):
        default = MagicMock()
        override = MagicMock()

        registry = build_retriever_registry(default=default, overrides={"special-bank": override})

        assert registry.get("special-bank") is override
        assert registry.get("other-bank") is default

    def test_build_retriever_registry_no_overrides(self):
        default = MagicMock()
        registry = build_retriever_registry(default=default)
        assert registry.get("any-bank") is default
