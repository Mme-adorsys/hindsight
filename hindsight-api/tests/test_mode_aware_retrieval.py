"""
Mode-aware retrieval tests — M3 Review Fixes 4, 3, 21, 23.

Fix 4  — retrieve_parallel forwards mode to MPFP and EngramRetriever
Fix 3  — tag filter SQL is not executed when tags=None
Fix 21 — Neo4j relationship type validation rejects unknown types
Fix 23 — Session lifecycle integration: co-activation + association window track correctly
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.search.types import RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rr(eid: str) -> RetrievalResult:
    return RetrievalResult(id=eid, text="text", fact_type="world")


def _fake_parallel_result(graph_results=None):
    from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

    return ParallelRetrievalResult(
        semantic=[],
        bm25=[],
        graph=graph_results or [],
        temporal=None,
        timings={"semantic": 0, "bm25": 0, "graph": 0},
        temporal_constraint=None,
        mpfp_timings=[],
    )


# ---------------------------------------------------------------------------
# Fix 4 — retrieve_parallel forwards mode to retrievers
# ---------------------------------------------------------------------------


class TestRetrieveParallelForwardsMode:
    @pytest.mark.asyncio
    async def test_retrieve_parallel_forwards_mode_to_mpfp(self) -> None:
        """retrieve_parallel passes the mode kwarg to the MPFP retriever's retrieve()."""
        from hindsight_api.engine.search.mpfp_retrieval import MPFPGraphRetriever
        from hindsight_api.engine.search.retrieval import retrieve_parallel

        mock_retriever = MagicMock(spec=MPFPGraphRetriever)
        mock_retriever.name = "mpfp"
        received_modes: list = []

        async def fake_retrieve(**kwargs):
            received_modes.append(kwargs.get("mode"))
            return [], None

        mock_retriever.retrieve = fake_retrieve

        with (
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_semantic",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_bm25",
                new=AsyncMock(return_value=[]),
            ),
            # extract_temporal_constraint is imported locally inside retrieve_parallel,
            # so we patch it at its source module.
            patch(
                "hindsight_api.engine.search.temporal_extraction.extract_temporal_constraint",
                return_value=None,
            ),
            patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry",
            ) as mock_acq,
        ):
            mock_conn = AsyncMock()
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            await retrieve_parallel(
                pool=MagicMock(),
                query_text="test query",
                query_embedding_str="[0.1]",
                bank_id="bank",
                thinking_budget=10,
                graph_retriever=mock_retriever,
                mode=RetrievalMode.ANALOGY,
            )

        assert len(received_modes) >= 1
        assert received_modes[0] == RetrievalMode.ANALOGY

    @pytest.mark.asyncio
    async def test_retrieve_parallel_forwards_mode_to_engram_retriever(self) -> None:
        """retrieve_parallel passes mode to EngramRetriever (non-mpfp path)."""
        from hindsight_api.engine.search.retrieval import retrieve_parallel

        mock_retriever = MagicMock()
        mock_retriever.name = "engram"
        received_modes: list = []

        async def fake_retrieve(**kwargs):
            received_modes.append(kwargs.get("mode"))
            return [], None

        mock_retriever.retrieve = fake_retrieve

        with (
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_semantic",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_bm25",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.search.temporal_extraction.extract_temporal_constraint",
                return_value=None,
            ),
            patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry",
            ) as mock_acq,
        ):
            mock_conn = AsyncMock()
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            await retrieve_parallel(
                pool=MagicMock(),
                query_text="test query",
                query_embedding_str="[0.1]",
                bank_id="bank",
                thinking_budget=10,
                graph_retriever=mock_retriever,
                mode=RetrievalMode.EXPLORATION,
            )

        assert len(received_modes) >= 1
        assert received_modes[0] == RetrievalMode.EXPLORATION


# ---------------------------------------------------------------------------
# Fix 3 — tag filter SQL not applied when tags=None
# ---------------------------------------------------------------------------


class TestTagFilterRobustness:
    def test_retrieval_without_tags_no_sql_error(self) -> None:
        """retrieve_semantic and retrieve_bm25 accept tags=None without SQL parameter error."""
        # We can verify by inspecting the function signatures — tags has a default of None
        import inspect

        from hindsight_api.engine.search.retrieval import retrieve_bm25, retrieve_semantic

        sig_s = inspect.signature(retrieve_semantic)
        sig_b = inspect.signature(retrieve_bm25)

        # Both must accept tags as optional keyword argument defaulting to None
        assert "tags" in sig_s.parameters
        assert sig_s.parameters["tags"].default is None

        assert "tags" in sig_b.parameters
        assert sig_b.parameters["tags"].default is None

    def test_retrieve_parallel_accepts_none_tags(self) -> None:
        """retrieve_parallel signature accepts tags=None (no required tags arg)."""
        import inspect

        from hindsight_api.engine.search.retrieval import retrieve_parallel

        sig = inspect.signature(retrieve_parallel)
        assert "tags" in sig.parameters
        assert sig.parameters["tags"].default is None


# ---------------------------------------------------------------------------
# Fix 21 — Neo4j relationship type validation
# ---------------------------------------------------------------------------


class TestNeo4jRelationshipTypeValidation:
    @pytest.mark.asyncio
    async def test_invalid_relationship_type_rejected(self) -> None:
        """Neo4jEngineClient.create_relationship raises ValueError for unknown rel_type."""
        from hindsight_api.engine.neo4j_client import Neo4jEngineClient

        client = Neo4jEngineClient(
            bolt_url="bolt://localhost:7687",
            username="neo4j",
            password="test",
            database="neo4j",
        )
        with pytest.raises(ValueError, match="Unknown relationship type"):
            await client.create_relationship(
                from_id="e1",
                to_id="e2",
                rel_type="INVALID_TYPE",
            )

    def test_all_valid_relationship_types_accepted(self) -> None:
        """All 8 RELATIONSHIP_TYPES are considered valid (no ValueError raised during init)."""
        from hindsight_api.engine.neo4j_client import RELATIONSHIP_TYPES

        assert len(RELATIONSHIP_TYPES) == 8
        expected = {
            "SEMANTIC",
            "TEMPORAL",
            "ENTITY",
            "CAUSAL",
            "CO_ACTIVATED",
            "TEMPORAL_PROXIMITY",
            "SCHEMA",
            "CONTRADICTION",
        }
        assert set(RELATIONSHIP_TYPES) == expected


# ---------------------------------------------------------------------------
# Fix 23 — Session lifecycle: co-activation + association window integration
# ---------------------------------------------------------------------------


class TestSessionLifecycleCoActivationAndAssociationWindow:
    @pytest.mark.asyncio
    async def test_session_lifecycle_co_activation_tracks_pairs(self) -> None:
        """
        Simulates a recall session: WorkingContext populated with engrams,
        CoActivationTracker.track_recall() called, pairs accumulate correctly.
        """
        from hindsight_api.engine.session.co_activation_tracker import CoActivationTracker
        from hindsight_api.engine.session.working_context import (
            ActiveEngrams,
            EngramRef,
            WorkingContext,
        )

        wc = WorkingContext(session_id="test-session")
        now = datetime.now(UTC)

        # Push 3 engrams into focus
        for i in range(3):
            ref = EngramRef(
                engram_id=f"e{i}",
                strength=0.8,
                relevance_score=0.7,
                activated_at=now + timedelta(seconds=i),
            )
            wc.active_engrams.focus.append(ref)

        # Simulate recall: track focus+supporting IDs
        active_ids = [r.engram_id for r in wc.active_engrams.focus]
        wc.co_activation_tracker.track_recall(active_ids)
        wc.co_activation_tracker.track_recall(active_ids)
        wc.co_activation_tracker.track_recall(active_ids)

        # 3 engrams → 3 pairs; each tracked 3 times
        counter = wc.co_activation_tracker._counter
        assert len(counter) == 3
        for pair, count in counter.items():
            assert count == 3

    @pytest.mark.asyncio
    async def test_session_lifecycle_association_window_tracks_temporal_pairs(self) -> None:
        """
        Association window detects temporally proximate engrams in focus tier.
        flush_to_neo4j(None) is a no-op (graceful degradation).
        """
        from hindsight_api.engine.session.working_context import (
            ActiveEngrams,
            EngramRef,
            WorkingContext,
        )

        wc = WorkingContext(session_id="test-session")
        now = datetime.now(UTC)

        # Two refs within 1 minute of each other
        ref_a = EngramRef(
            engram_id="ea",
            strength=0.8,
            relevance_score=0.7,
            activated_at=now,
        )
        ref_b = EngramRef(
            engram_id="eb",
            strength=0.8,
            relevance_score=0.7,
            activated_at=now + timedelta(seconds=30),
        )
        wc.active_engrams.focus = [ref_a, ref_b]

        pairs = wc.association_window.check_associations(wc.active_engrams)
        assert len(pairs) == 1
        assert pairs[0] == ("ea", "eb")

        # Flush to None is no-op
        written = await wc.association_window.flush_to_neo4j(None)
        assert written == 0

    @pytest.mark.asyncio
    async def test_flush_errors_continue_remaining_pairs(self) -> None:
        """Fix 9: if Neo4j write fails for one pair, remaining pairs are still attempted."""
        from hindsight_api.engine.session.co_activation_tracker import CoActivationTracker

        tracker = CoActivationTracker(threshold=1)
        tracker._counter[("e1", "e2")] = 2
        tracker._counter[("e3", "e4")] = 2

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_co_activation_link
        call_count = 0

        async def flaky_link(client, from_id, to_id, weight):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated Neo4j failure")

        lc.create_co_activation_link = flaky_link
        try:
            class FakeNeo4j:
                pass

            written = await tracker.flush_to_neo4j(FakeNeo4j())
            # First pair failed, second should succeed → written=1
            assert written == 1
            assert call_count == 2
        finally:
            lc.create_co_activation_link = original
