"""
Unit tests for flush_session_async (Epic 18 Story 03 — Flush-Prozess & Session Lifecycle).

Tests cover:
- _process_inferences_into_wm: confirmed/tentative/rejected routing
- _process_inferences_into_wm: deduplication (higher confidence wins)
- _co_activation_to_links: normalized weights, empty input
- flush_session_async: episodic buffer → retain_batch_async
- flush_session_async: inference routing into WM
- flush_session_async: co-activation → Neo4j (best-effort)
- flush_session_async: error resilience (retain down → still returns metrics)
- flush_session_async: no Neo4j client → co-activation skipped gracefully
- flush_session_async: empty cache → no retain/neo4j calls
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.memory_engine import _co_activation_to_links, _process_inferences_into_wm
from hindsight_api.engine.session.session_cache import SessionCache
from hindsight_api.engine.session.working_context import Inference
from hindsight_api.engine.session.working_memory import WorkingMemory
from hindsight_api.engine.response_models import Episode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_inference(iid: str, status: str = "confirmed", confidence: float = 0.8) -> Inference:
    return Inference(
        id=iid,
        content=f"hypothesis {iid}",
        confidence=confidence,
        supporting_engram_ids=["e1"],
        created_at=datetime.now(UTC),
        status=status,  # type: ignore[arg-type]
    )


def make_cache(session_id: str = "sess-1") -> SessionCache:
    return SessionCache(session_id=session_id)


def make_wm(bank_id: str = "bank-1") -> WorkingMemory:
    return WorkingMemory.default(bank_id)


# ---------------------------------------------------------------------------
# _process_inferences_into_wm
# ---------------------------------------------------------------------------


class TestProcessInferencesIntoWm:
    def test_confirmed_added_to_wm(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i1", "confirmed", 0.9))
        wm = make_wm()
        count = _process_inferences_into_wm(sc, wm)
        assert count == 1
        assert len(wm.confirmed_inferences) == 1
        assert wm.confirmed_inferences[0].id == "i1"
        assert wm.confirmed_inferences[0].confidence == pytest.approx(0.9)

    def test_tentative_halved_confidence(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i2", "tentative", 0.8))
        wm = make_wm()
        _process_inferences_into_wm(sc, wm)
        assert len(wm.confirmed_inferences) == 1
        assert wm.confirmed_inferences[0].confidence == pytest.approx(0.4)

    def test_tentative_status_set_to_confirmed(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i3", "tentative", 0.6))
        wm = make_wm()
        _process_inferences_into_wm(sc, wm)
        assert wm.confirmed_inferences[0].status == "confirmed"

    def test_rejected_not_added(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i4", "rejected", 0.5))
        wm = make_wm()
        count = _process_inferences_into_wm(sc, wm)
        assert count == 0
        assert wm.confirmed_inferences == []

    def test_mixed_routing(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("c1", "confirmed", 0.9))
        sc.add_inference(make_inference("t1", "tentative", 0.8))
        sc.add_inference(make_inference("r1", "rejected", 0.7))
        wm = make_wm()
        count = _process_inferences_into_wm(sc, wm)
        assert count == 2
        ids = {inf.id for inf in wm.confirmed_inferences}
        assert "c1" in ids
        assert "t1" in ids
        assert "r1" not in ids

    def test_dedup_higher_confidence_wins(self) -> None:
        # existing: i1 at 0.5, new: i1 at 0.9 → 0.9 kept
        wm = make_wm()
        wm.confirmed_inferences.append(make_inference("i1", "confirmed", 0.5))
        sc = make_cache()
        sc.add_inference(make_inference("i1", "confirmed", 0.9))
        _process_inferences_into_wm(sc, wm)
        assert len(wm.confirmed_inferences) == 1
        assert wm.confirmed_inferences[0].confidence == pytest.approx(0.9)

    def test_dedup_lower_confidence_not_overwrite(self) -> None:
        # existing: i1 at 0.9, new: i1 at 0.3 → 0.9 kept
        wm = make_wm()
        wm.confirmed_inferences.append(make_inference("i1", "confirmed", 0.9))
        sc = make_cache()
        sc.add_inference(make_inference("i1", "confirmed", 0.3))
        _process_inferences_into_wm(sc, wm)
        assert wm.confirmed_inferences[0].confidence == pytest.approx(0.9)

    def test_empty_cache_returns_zero(self) -> None:
        sc = make_cache()
        wm = make_wm()
        assert _process_inferences_into_wm(sc, wm) == 0
        assert wm.confirmed_inferences == []


# ---------------------------------------------------------------------------
# _co_activation_to_links
# ---------------------------------------------------------------------------


class TestCoActivationToLinks:
    def test_empty_returns_empty(self) -> None:
        assert _co_activation_to_links({}) == []

    def test_single_pair_weight_one(self) -> None:
        counts = {("e1", "e2"): 3}
        links = _co_activation_to_links(counts)
        assert len(links) == 1
        assert links[0].weight == pytest.approx(1.0)  # 3/3 = 1.0
        assert links[0].rel_type == "CO_ACTIVATED"

    def test_normalization(self) -> None:
        counts = {("e1", "e2"): 4, ("e2", "e3"): 2, ("e1", "e3"): 1}
        links = _co_activation_to_links(counts)
        weights = {(l.from_id, l.to_id): l.weight for l in links}
        assert weights[("e1", "e2")] == pytest.approx(1.0)   # 4/4
        assert weights[("e2", "e3")] == pytest.approx(0.5)   # 2/4
        assert weights[("e1", "e3")] == pytest.approx(0.25)  # 1/4

    def test_link_source_is_session_flush(self) -> None:
        counts = {("a", "b"): 1}
        links = _co_activation_to_links(counts)
        assert links[0].source == "session_flush"

    def test_zero_max_returns_empty(self) -> None:
        # edge case: all counts are 0 (shouldn't happen in practice but safe)
        counts = {("a", "b"): 0}
        links = _co_activation_to_links(counts)
        assert links == []


# ---------------------------------------------------------------------------
# flush_session_async integration (mock-based)
# ---------------------------------------------------------------------------


def _make_engine_with_session(wm: WorkingMemory | None = None):
    """Build a MemoryEngine stub with a real SessionManager holding one session."""
    from hindsight_api.engine.memory_engine import MemoryEngine
    from hindsight_api.engine.session.session_manager import SessionManager
    import asyncio

    sm = SessionManager()
    mock_ctx = MagicMock()
    mock_ctx.get_session_manager.return_value = sm

    mock_conn = AsyncMock()
    mock_pool = AsyncMock()
    mock_ctx.get_pool = AsyncMock(return_value=mock_pool)

    engine = object.__new__(MemoryEngine)
    engine._ctx = mock_ctx
    engine._retain = MagicMock()
    engine._retain.engram_storage = None  # no Neo4j by default

    return engine, sm


class TestFlushSessionAsync:
    @pytest.mark.asyncio
    async def test_empty_cache_returns_zero_metrics(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()) as mock_retain:
                result = await engine.flush_session_async(
                    session.session_id, "bank", request_context=MagicMock()
                )

        assert result["flushed_episodes"] == 0
        assert result["flushed_inferences"] == 0
        assert result["flushed_co_activations"] == 0
        mock_retain.assert_not_called()

    @pytest.mark.asyncio
    async def test_episodic_buffer_sent_to_retain(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.add_episode(Episode(action="did X", context="ctx", outcome="ok"))
        sc.add_episode(Episode(action="did Y", context="ctx", outcome="ok"))

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()) as mock_retain:
                result = await engine.flush_session_async(
                    session.session_id, "bank", request_context=MagicMock()
                )

        assert result["flushed_episodes"] == 2
        mock_retain.assert_called_once()
        items = mock_retain.call_args.args[1]
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_inferences_processed_before_end_session(self) -> None:
        """Confirmed inferences must be in WM before end_session_async is called."""
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        wm = WorkingMemory.default("inf-bank")
        sm.initialize_working_memory(session.session_id, wm)

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.add_inference(make_inference("i1", "confirmed", 0.9))
        sc.add_inference(make_inference("i2", "rejected", 0.5))

        wm_at_end: list[WorkingMemory] = []

        async def capture_end(sid, bid):
            wm_at_end.append(sm.get_working_memory(sid))  # type: ignore[arg-type]
            return []

        with patch.object(engine, "end_session_async", new=capture_end):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()):
                result = await engine.flush_session_async(
                    session.session_id, "inf-bank", request_context=MagicMock()
                )

        assert result["flushed_inferences"] == 1  # only confirmed counts
        assert len(wm_at_end) == 1
        assert len(wm_at_end[0].confirmed_inferences) == 1
        assert wm_at_end[0].confirmed_inferences[0].id == "i1"

    @pytest.mark.asyncio
    async def test_retain_failure_does_not_abort_flush(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.add_episode(Episode(action="event", context="ctx", outcome="ok"))

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock(side_effect=RuntimeError("DB down"))):
                result = await engine.flush_session_async(
                    session.session_id, "bank", request_context=MagicMock()
                )

        # Retain failed → episodes = 0, but flush itself succeeded
        assert result["flushed_episodes"] == 0
        assert result["flushed_inferences"] == 0

    @pytest.mark.asyncio
    async def test_no_neo4j_client_co_activation_skipped(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.record_co_activation("e1", "e2")

        engine._retain.engram_storage = None  # no Neo4j

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()):
                result = await engine.flush_session_async(
                    session.session_id, "bank", request_context=MagicMock()
                )

        assert result["flushed_co_activations"] == 0

    @pytest.mark.asyncio
    async def test_co_activation_written_to_neo4j(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.record_co_activation("e1", "e2")
        sc.record_co_activation("e2", "e3")

        mock_neo4j = AsyncMock()
        mock_storage = MagicMock()
        mock_storage._neo4j = mock_neo4j
        engine._retain.engram_storage = mock_storage

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()):
                with patch(
                    "hindsight_api.engine.working_memory_repo.WorkingMemoryRepository"
                ):
                    with patch(
                        "hindsight_api.engine.retain.neo4j_link_writer.write_links_to_neo4j",
                        new=AsyncMock(),
                    ) as mock_write:
                        result = await engine.flush_session_async(
                            session.session_id, "bank", request_context=MagicMock()
                        )

        assert result["flushed_co_activations"] == 2
        mock_write.assert_called_once()
        links = mock_write.call_args.args[1]
        assert len(links) == 2

    @pytest.mark.asyncio
    async def test_neo4j_failure_does_not_abort_flush(self) -> None:
        engine, sm = _make_engine_with_session()
        session = await sm.create_session()

        sc = sm.get_session_cache(session.session_id)
        assert sc is not None
        sc.record_co_activation("e1", "e2")

        mock_storage = MagicMock()
        mock_storage._neo4j = AsyncMock()
        engine._retain.engram_storage = mock_storage

        with patch.object(engine, "end_session_async", new=AsyncMock(return_value=[])):
            with patch.object(engine, "retain_batch_async", new=AsyncMock()):
                with patch(
                    "hindsight_api.engine.retain.neo4j_link_writer.write_links_to_neo4j",
                    new=AsyncMock(side_effect=RuntimeError("Neo4j down")),
                ):
                    result = await engine.flush_session_async(
                        session.session_id, "bank", request_context=MagicMock()
                    )

        # Neo4j failed → co_activations = 0, but flush still returns
        assert result["flushed_co_activations"] == 0
