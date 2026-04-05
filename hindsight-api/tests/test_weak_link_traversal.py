"""
Unit tests for Mode-aware Weak-Link Traversal — Epic 09 Story 02 T5.

Covers:
- _resolve_mode_params: rel_types filtered correctly per weak_link_policy
- Precision/Validation (ignore): CO_ACTIVATED + TEMPORAL_PROXIMITY excluded from rel_types
- Exploration (follow): all rel types included
- Analogy (prefer): all rel types included + separate weak-link traversal boost
- Weight threshold constant (WEAK_LINK_MIN_WEIGHT = 0.1)
- traversal_source='weak_link' set on prefer-mode results
- RetrievalResult.traversal_source field exists and defaults to None
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.search.engram_retrieval import (
    WEAK_LINK_MIN_WEIGHT,
    WEAK_LINK_PREFER_BOOST,
    WEAK_LINK_TYPES,
    EngramRetriever,
    _resolve_mode_params,
)
from hindsight_api.engine.search.types import RetrievalResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_weak_link_types_contains_co_activated(self) -> None:
        assert "CO_ACTIVATED" in WEAK_LINK_TYPES

    def test_weak_link_types_contains_temporal_proximity(self) -> None:
        assert "TEMPORAL_PROXIMITY" in WEAK_LINK_TYPES

    def test_weak_link_min_weight(self) -> None:
        assert WEAK_LINK_MIN_WEIGHT == pytest.approx(0.1)

    def test_weak_link_prefer_boost(self) -> None:
        assert WEAK_LINK_PREFER_BOOST == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# RetrievalResult.traversal_source
# ---------------------------------------------------------------------------


class TestTraversalSourceField:
    def test_defaults_to_none(self) -> None:
        rr = RetrievalResult(id="a", text="hello", fact_type="world")
        assert rr.traversal_source is None

    def test_can_be_set_to_weak_link(self) -> None:
        rr = RetrievalResult(id="a", text="hello", fact_type="world")
        rr.traversal_source = "weak_link"
        assert rr.traversal_source == "weak_link"


# ---------------------------------------------------------------------------
# _resolve_mode_params
# ---------------------------------------------------------------------------


class TestResolveModeParams:
    def test_none_mode_returns_follow_policy(self) -> None:
        _, _, _, policy = _resolve_mode_params(None)
        assert policy == "follow"

    def test_none_mode_includes_weak_link_types(self) -> None:
        _, rel_types, _, _ = _resolve_mode_params(None)
        assert "CO_ACTIVATED" in rel_types
        assert "TEMPORAL_PROXIMITY" in rel_types

    def test_precision_ignores_weak_links(self) -> None:
        _, rel_types, _, policy = _resolve_mode_params(RetrievalMode.PRECISION)
        assert policy == "ignore"
        assert "CO_ACTIVATED" not in rel_types
        assert "TEMPORAL_PROXIMITY" not in rel_types

    def test_validation_ignores_weak_links(self) -> None:
        _, rel_types, _, policy = _resolve_mode_params(RetrievalMode.VALIDATION)
        assert policy == "ignore"
        assert "CO_ACTIVATED" not in rel_types
        assert "TEMPORAL_PROXIMITY" not in rel_types

    def test_exploration_follows_weak_links(self) -> None:
        _, _, _, policy = _resolve_mode_params(RetrievalMode.EXPLORATION)
        assert policy == "follow"

    def test_analogy_prefers_weak_links(self) -> None:
        _, _, _, policy = _resolve_mode_params(RetrievalMode.ANALOGY)
        assert policy == "prefer"

    def test_precision_still_has_strong_link_types(self) -> None:
        _, rel_types, _, _ = _resolve_mode_params(RetrievalMode.PRECISION)
        # At least some strong link types should remain
        strong = {"SEMANTIC", "ENTITY", "CAUSAL", "TEMPORAL"}
        assert strong & set(rel_types), f"Expected strong links in {rel_types}"

    def test_exploration_includes_all_rel_types(self) -> None:
        _, rel_types, _, _ = _resolve_mode_params(RetrievalMode.EXPLORATION)
        assert "CO_ACTIVATED" in rel_types
        assert "TEMPORAL_PROXIMITY" in rel_types

    def test_traversal_depth_shallow_for_precision(self) -> None:
        _, _, max_depth, _ = _resolve_mode_params(RetrievalMode.PRECISION)
        assert max_depth == 1

    def test_traversal_depth_deep_for_exploration(self) -> None:
        _, _, max_depth, _ = _resolve_mode_params(RetrievalMode.EXPLORATION)
        assert max_depth == 3


# ---------------------------------------------------------------------------
# EngramRetriever — prefer mode weak-link boost and source marking
# ---------------------------------------------------------------------------


def _make_retriever() -> EngramRetriever:
    qdrant = MagicMock()
    neo4j = MagicMock()
    return EngramRetriever(qdrant=qdrant, neo4j=neo4j)


def _make_rr(engram_id: str, activation: float = 0.5) -> RetrievalResult:
    rr = RetrievalResult(id=engram_id, text=f"text-{engram_id}", fact_type="world")
    rr.activation = activation
    return rr


class TestEngramRetrieverPreferMode:
    @pytest.mark.asyncio
    async def test_prefer_mode_calls_weak_link_traversal(self) -> None:
        """For Analogy mode (prefer), a second traversal over WEAK_LINK_TYPES should occur."""
        retriever = _make_retriever()

        traverse_calls: list[dict] = []

        async def fake_traverse(seed_ids, rel_types, max_depth, min_weight=0.01):
            traverse_calls.append({"rel_types": rel_types, "min_weight": min_weight})
            return {}

        with (
            patch.object(retriever, "_get_seeds", new=AsyncMock(return_value=["seed-1"])),
            patch.object(retriever, "_traverse", side_effect=fake_traverse),
            patch.object(retriever, "_enrich", new=AsyncMock(return_value=[])),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str='[0.1, 0.2]',
                bank_id="test-bank",
                budget=10,
                mode=RetrievalMode.ANALOGY,
            )

        # First call: normal traversal; second call: weak-link only with min_weight threshold
        assert len(traverse_calls) >= 2
        weak_call = traverse_calls[1]
        assert set(weak_call["rel_types"]) == WEAK_LINK_TYPES
        assert weak_call["min_weight"] == pytest.approx(WEAK_LINK_MIN_WEIGHT)

    @pytest.mark.asyncio
    async def test_prefer_mode_marks_weak_link_source(self) -> None:
        """Results reached via the weak-link traversal get traversal_source='weak_link'."""
        retriever = _make_retriever()

        call_count = 0

        async def fake_traverse(seed_ids, rel_types, max_depth, min_weight=0.01):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Normal traversal — returns seed + one strong result
                return {"seed-1": 1.0, "strong-result": 0.5}
            else:
                # Weak-link traversal — returns one additional node
                return {"weak-result": 0.4}

        rr_seed = _make_rr("seed-1", 1.0)
        rr_strong = _make_rr("strong-result", 0.5)
        rr_weak = _make_rr("weak-result", 0.4)

        async def fake_enrich(pool, bank_id, activation_map, budget):
            return [rr_seed, rr_strong, rr_weak]

        with (
            patch.object(retriever, "_get_seeds", new=AsyncMock(return_value=["seed-1"])),
            patch.object(retriever, "_traverse", side_effect=fake_traverse),
            patch.object(retriever, "_enrich", side_effect=fake_enrich),
        ):
            results = await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str='[0.1, 0.2]',
                bank_id="test-bank",
                budget=10,
                mode=RetrievalMode.ANALOGY,
            )

        result_list, _ = results
        by_id = {r.id: r for r in result_list}

        # seed and strong-result should NOT be marked as weak_link
        assert by_id["seed-1"].traversal_source is None
        assert by_id["strong-result"].traversal_source is None
        # weak-result (found via weak-link traversal, not a seed) → marked
        assert by_id["weak-result"].traversal_source == "weak_link"

    @pytest.mark.asyncio
    async def test_ignore_mode_does_not_call_weak_link_traversal(self) -> None:
        """Precision mode: only one traverse call, no weak-link pass."""
        retriever = _make_retriever()

        traverse_calls: list[dict] = []

        async def fake_traverse(seed_ids, rel_types, max_depth, min_weight=0.01):
            traverse_calls.append({"rel_types": rel_types})
            return {"seed-1": 1.0}

        with (
            patch.object(retriever, "_get_seeds", new=AsyncMock(return_value=["seed-1"])),
            patch.object(retriever, "_traverse", side_effect=fake_traverse),
            patch.object(retriever, "_enrich", new=AsyncMock(return_value=[])),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str='[0.1, 0.2]',
                bank_id="test-bank",
                budget=10,
                mode=RetrievalMode.PRECISION,
            )

        assert len(traverse_calls) == 1
        # rel_types must not contain weak link types
        used_types = set(traverse_calls[0]["rel_types"])
        assert "CO_ACTIVATED" not in used_types
        assert "TEMPORAL_PROXIMITY" not in used_types

    @pytest.mark.asyncio
    async def test_prefer_mode_boosts_weak_link_activation(self) -> None:
        """Weak-link activation scores receive WEAK_LINK_PREFER_BOOST multiplier."""
        retriever = _make_retriever()
        call_count = 0

        async def fake_traverse(seed_ids, rel_types, max_depth, min_weight=0.01):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"seed-1": 1.0}
            else:
                # Weak traversal returns score 0.4 → boosted to 0.4 * 1.5 = 0.6
                return {"weak-node": 0.4}

        captured_activation: dict[str, float] = {}

        async def fake_enrich(pool, bank_id, activation_map, budget):
            captured_activation.update(activation_map)
            return []

        with (
            patch.object(retriever, "_get_seeds", new=AsyncMock(return_value=["seed-1"])),
            patch.object(retriever, "_traverse", side_effect=fake_traverse),
            patch.object(retriever, "_enrich", side_effect=fake_enrich),
        ):
            await retriever.retrieve(
                pool=MagicMock(),
                query_embedding_str='[0.1]',
                bank_id="bank",
                budget=10,
                mode=RetrievalMode.ANALOGY,
            )

        assert "weak-node" in captured_activation
        expected = 0.4 * WEAK_LINK_PREFER_BOOST
        assert captured_activation["weak-node"] == pytest.approx(expected)
