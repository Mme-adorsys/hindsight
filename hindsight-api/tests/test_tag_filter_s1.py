"""
Unit tests for Epic 07 Story 01 — Tags-based Filtering.

Validates WITHOUT a running database:
- retrieve_semantic accepts tags=None (no filter SQL)
- retrieve_semantic accepts tags=[...] (engram_dictionary JOIN added)
- retrieve_bm25 same pattern
- retrieve_temporal same pattern
- retrieve_parallel routes tags through to internal calls
- recall_async single retrieve_parallel call (loop removed)
- _get_temporal_entry_points tags param builds correct SQL branch
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.search.retrieval import (
    _get_temporal_entry_points,
    _retrieve_parallel_bfs,
    _retrieve_parallel_mpfp,
    retrieve_bm25,
    retrieve_semantic,
    retrieve_temporal,
)
from hindsight_api.engine.search.types import RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(unit_id: str | None = None, score: float = 0.8) -> dict:
    """Minimal DB row compatible with RetrievalResult.from_db_row."""
    return {
        "id": uuid.UUID(unit_id) if unit_id else uuid.uuid4(),
        "text": "sample text",
        "fact_type": "world",
        "context": None,
        "event_date": None,
        "occurred_start": None,
        "occurred_end": None,
        "mentioned_at": None,
        "document_id": None,
        "chunk_id": None,
        "access_count": 0,
        "embedding": None,
        "similarity": score,
        "bm25_score": score,
        "temporal_score": score,
        "temporal_proximity": score,
        "activation": score,
        "weight": score,
        "link_type": "semantic",
        "from_unit_id": uuid.uuid4(),
    }


def _mock_conn(rows=None):
    """Create a mock asyncpg connection that returns `rows` from .fetch()."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    return conn


# ---------------------------------------------------------------------------
# retrieve_semantic
# ---------------------------------------------------------------------------


class TestRetrieveSemanticTagFilter:
    @pytest.mark.asyncio
    async def test_no_tags_no_join(self):
        """Without tags, engram_dictionary JOIN must NOT appear in SQL."""
        conn = _mock_conn()
        await retrieve_semantic(conn, "[0.1]", "bank-1", limit=10, tags=None)

        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        assert "engram_dictionary" not in sql
        assert "tags @>" not in sql

    @pytest.mark.asyncio
    async def test_with_tags_adds_join(self):
        """With tags, engram_dictionary JOIN and tags @> filter must appear."""
        conn = _mock_conn()
        await retrieve_semantic(conn, "[0.1]", "bank-1", limit=10, tags=["world"])

        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        assert "engram_dictionary" in sql
        assert "tags @>" in sql

    @pytest.mark.asyncio
    async def test_tags_param_passed_to_query(self):
        """The tags list must be in the positional parameters."""
        conn = _mock_conn()
        await retrieve_semantic(conn, "[0.1]", "bank-1", limit=5, tags=["experience"])

        call_args = conn.fetch.call_args
        params = call_args[0][1:]  # positional args after SQL
        assert ["experience"] in params

    @pytest.mark.asyncio
    async def test_returns_retrieval_results(self):
        """Returns list of RetrievalResult from DB rows."""
        row = _make_row(score=0.9)
        conn = _mock_conn([row])
        results = await retrieve_semantic(conn, "[0.1]", "bank-1", limit=10)
        assert len(results) == 1
        assert isinstance(results[0], RetrievalResult)


# ---------------------------------------------------------------------------
# retrieve_bm25
# ---------------------------------------------------------------------------


class TestRetrieveBm25TagFilter:
    @pytest.mark.asyncio
    async def test_no_tags_no_join(self):
        conn = _mock_conn()
        await retrieve_bm25(conn, "hello world", "bank-1", limit=10, tags=None)

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" not in sql
        assert "tags @>" not in sql

    @pytest.mark.asyncio
    async def test_with_tags_adds_join(self):
        conn = _mock_conn()
        await retrieve_bm25(conn, "hello world", "bank-1", limit=10, tags=["opinion"])

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" in sql
        assert "tags @>" in sql

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        """BM25 with no valid tokens returns empty list without hitting DB."""
        conn = _mock_conn()
        results = await retrieve_bm25(conn, "!!! ???", "bank-1", limit=10)
        assert results == []
        conn.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# retrieve_temporal
# ---------------------------------------------------------------------------


class TestRetrieveTemporalTagFilter:
    def _dates(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 12, 31, tzinfo=UTC)
        return start, end

    @pytest.mark.asyncio
    async def test_no_tags_no_join(self):
        conn = _mock_conn()  # returns [] → no spreading phase
        start, end = self._dates()
        await retrieve_temporal(conn, "[0.1]", "bank-1", start, end, budget=10, tags=None)

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" not in sql
        assert "tags @>" not in sql

    @pytest.mark.asyncio
    async def test_with_tags_adds_join(self):
        conn = _mock_conn()
        start, end = self._dates()
        await retrieve_temporal(conn, "[0.1]", "bank-1", start, end, budget=10, tags=["world"])

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" in sql
        assert "tags @>" in sql


# ---------------------------------------------------------------------------
# _get_temporal_entry_points
# ---------------------------------------------------------------------------


class TestGetTemporalEntryPoints:
    def _dates(self):
        start = datetime(2024, 6, 1, tzinfo=UTC)
        end = datetime(2024, 9, 1, tzinfo=UTC)
        return start, end

    @pytest.mark.asyncio
    async def test_no_tags_no_join(self):
        conn = _mock_conn()
        start, end = self._dates()
        await _get_temporal_entry_points(conn, "[0.1]", "bank-1", start, end, tags=None)

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" not in sql
        # Old filter clause `AND fact_type = $N` must be gone;
        # `mu.fact_type` still appears in SELECT as a column, which is fine.
        assert "AND fact_type" not in sql
        assert "fact_type =" not in sql

    @pytest.mark.asyncio
    async def test_with_tags_adds_join(self):
        conn = _mock_conn()
        start, end = self._dates()
        await _get_temporal_entry_points(conn, "[0.1]", "bank-1", start, end, tags=["world"])

        sql = conn.fetch.call_args[0][0]
        assert "engram_dictionary" in sql
        assert "tags @>" in sql


# ---------------------------------------------------------------------------
# retrieve_parallel / _retrieve_parallel_* — single call, tags propagated
# ---------------------------------------------------------------------------


class TestRetrieveParallelTagPropagation:
    """Verify tags flow from retrieve_parallel into internal retrieve_* calls."""

    @pytest.mark.asyncio
    async def test_tags_reach_retrieve_semantic_in_bfs(self):
        """In BFS mode, retrieve_semantic must receive the tags kwarg."""
        pool = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_semantic", new_callable=AsyncMock
            ) as mock_sem,
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_bm25", new_callable=AsyncMock
            ) as mock_bm25,
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_temporal", new_callable=AsyncMock
            ) as mock_temp,
        ):
            mock_sem.return_value = []
            mock_bm25.return_value = []
            mock_temp.return_value = []

            # Mock graph retriever
            mock_retriever = AsyncMock()
            mock_retriever.name = "bfs"
            mock_retriever.retrieve = AsyncMock(return_value=([], None))

            # Patch acquire_with_retry to yield a mock connection
            mock_conn = _mock_conn()
            acquire_ctx = MagicMock()
            acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            acquire_ctx.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry", return_value=acquire_ctx
            ):
                await _retrieve_parallel_bfs(
                    pool,
                    "test query",
                    "[0.1]",
                    "bank-1",
                    thinking_budget=50,
                    temporal_constraint=None,
                    retriever=mock_retriever,
                    tags=["world"],
                )

            # tags must have been forwarded to retrieve_semantic
            sem_call = mock_sem.call_args
            assert sem_call is not None
            assert sem_call.kwargs.get("tags") == ["world"] or (
                len(sem_call.args) > 4 and sem_call.args[4] == ["world"]
            )

    @pytest.mark.asyncio
    async def test_no_tags_no_filter_in_bfs(self):
        """Without tags, retrieve_semantic called with tags=None."""
        pool = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_semantic", new_callable=AsyncMock
            ) as mock_sem,
            patch("hindsight_api.engine.search.retrieval.retrieve_bm25", new_callable=AsyncMock) as mock_bm25,
        ):
            mock_sem.return_value = []
            mock_bm25.return_value = []

            mock_retriever = AsyncMock()
            mock_retriever.name = "bfs"
            mock_retriever.retrieve = AsyncMock(return_value=([], None))

            mock_conn = _mock_conn()
            acquire_ctx = MagicMock()
            acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            acquire_ctx.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "hindsight_api.engine.search.retrieval.acquire_with_retry", return_value=acquire_ctx
            ):
                await _retrieve_parallel_bfs(
                    pool,
                    "test query",
                    "[0.1]",
                    "bank-1",
                    thinking_budget=50,
                    temporal_constraint=None,
                    retriever=mock_retriever,
                    tags=None,
                )

            sem_call = mock_sem.call_args
            tags_arg = sem_call.kwargs.get("tags")
            assert tags_arg is None


# ---------------------------------------------------------------------------
# Performance: single call vs old 3-call loop (timing sanity)
# ---------------------------------------------------------------------------


class TestSingleCallPerformance:
    """
    Confirm the new code makes ONE retrieve_parallel call per recall.
    We mock retrieve_parallel and count invocations.
    """

    @pytest.mark.asyncio
    async def test_recall_async_calls_retrieve_parallel_once(self):
        """recall_async must call retrieve_parallel exactly once regardless of fact_type list."""
        from unittest.mock import patch

        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        empty_pr = ParallelRetrievalResult(
            semantic=[],
            bm25=[],
            graph=[],
            temporal=None,
            timings={"semantic": 0.0, "bm25": 0.0, "graph": 0.0},
            mpfp_timings=[],
        )

        call_count = {"n": 0}

        async def mock_retrieve_parallel(*args, **kwargs):
            call_count["n"] += 1
            return empty_pr

        # Build a minimal MemoryEngine-like object
        import threading

        from hindsight_api.engine.memory_engine import MemoryEngine
        from hindsight_api.models import RequestContext

        engine = object.__new__(MemoryEngine)
        engine._session_manager = None
        engine._session_manager_lazy_init = True
        engine._session_manager_lock = threading.Lock()
        engine._operation_validator = None
        engine._search_semaphore = asyncio.Semaphore(10)
        engine.query_analyzer = None
        engine.embeddings = MagicMock()
        engine.embeddings.encode = MagicMock(return_value=[0.1] * 128)
        engine._cross_encoder_reranker = None
        engine._reranker_lock = threading.Lock()
        engine._reranker_initialized = False

        mock_pool = AsyncMock()
        engine._pool = mock_pool

        async def _get_pool():
            return mock_pool

        engine._get_pool = _get_pool

        async def _authenticate_tenant(rc):
            pass

        engine._authenticate_tenant = _authenticate_tenant

        with (
            patch(
                "hindsight_api.engine.memory_engine.embedding_utils.generate_embedding",
                return_value=[0.1] * 128,
            ),
            # retrieve_parallel is imported locally inside _search_with_retries;
            # patch it at the source module so the local import picks up the mock.
            patch(
                "hindsight_api.engine.search.retrieval.retrieve_parallel",
                side_effect=mock_retrieve_parallel,
            ),
            patch(
                "hindsight_api.engine.search.temporal_extraction.extract_temporal_constraint",
                return_value=None,
            ),
        ):
            # The old code would call retrieve_parallel 3× (once per fact_type)
            # The new code must call it exactly once
            try:
                await engine.recall_async(
                    "test-bank",
                    "what happened?",
                    fact_type=["world", "experience", "opinion"],
                    request_context=RequestContext(),
                )
            except Exception:
                # Downstream steps (reranking etc.) may fail without full setup —
                # we only care about the retrieve_parallel call count
                pass

        assert call_count["n"] == 1, (
            f"retrieve_parallel called {call_count['n']} times — expected 1 (loop removed)"
        )
