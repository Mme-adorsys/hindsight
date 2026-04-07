"""
Unit tests for WorkingMemory (Epic 18 Story 02 — Working Memory Persistence).

Tests cover:
- WorkingMemory.to_dict() / from_dict() roundtrip (all field types)
- WorkingMemory.default() returns empty state, schema_version=1
- Schema version mismatch → returns default (forward-compat safety net)
- WorkingMemoryRepository.load() with mock conn (no row → default, row → WM)
- WorkingMemoryRepository.save() with mock conn (upsert SQL called)
- WorkingMemoryRepository.exists() with mock conn
- SessionManager.initialize_working_memory() / get_working_memory()
- Session history FIFO: 21st entry evicts oldest
- create_session_async: primes WorkingContext from WM (Priming Effect)
- end_session_async: merges WC→WM and records session history
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.session.working_context import (
    ActiveEngrams,
    EngramRef,
    Goal,
    Inference,
    WorkingContext,
)
from hindsight_api.engine.session.working_memory import (
    CURRENT_SCHEMA_VERSION,
    SESSION_HISTORY_MAX,
    SessionRef,
    WorkingMemory,
)
from hindsight_api.engine.working_memory_repo import WorkingMemoryRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_goal(gid: str = "g1") -> Goal:
    return Goal(
        id=gid,
        description=f"Goal {gid}",
        priority=0.9,
        status="active",
        created_at=datetime.now(UTC),
    )


def make_engram_ref(eid: str = "e1") -> EngramRef:
    return EngramRef(
        engram_id=eid,
        strength=0.8,
        relevance_score=0.75,
        activated_at=datetime.now(UTC),
    )


def make_inference(iid: str = "i1") -> Inference:
    return Inference(
        id=iid,
        content="hypothesis",
        confidence=0.7,
        supporting_engram_ids=["e1"],
        created_at=datetime.now(UTC),
        status="confirmed",
    )


def make_session_ref(sid: str = "sess-1") -> SessionRef:
    return SessionRef(session_id=sid, started_at=datetime.now(UTC))


def make_wm(bank_id: str = "bank-1") -> WorkingMemory:
    wm = WorkingMemory(bank_id=bank_id)
    wm.goal_stack = [make_goal("g1"), make_goal("g2")]
    wm.active_engrams = ActiveEngrams(
        focus=[make_engram_ref("e1")],
        supporting=[make_engram_ref("e2")],
        peripheral=[make_engram_ref("e3")],
    )
    wm.confirmed_inferences = [make_inference("i1")]
    wm.session_history = [make_session_ref("s1"), make_session_ref("s2")]
    return wm


# ---------------------------------------------------------------------------
# WorkingMemory.default()
# ---------------------------------------------------------------------------


class TestWorkingMemoryDefault:
    def test_default_bank_id(self) -> None:
        wm = WorkingMemory.default("my-bank")
        assert wm.bank_id == "my-bank"

    def test_default_lists_empty(self) -> None:
        wm = WorkingMemory.default("bank")
        assert wm.goal_stack == []
        assert wm.confirmed_inferences == []
        assert wm.session_history == []

    def test_default_active_engrams_empty(self) -> None:
        wm = WorkingMemory.default("bank")
        assert wm.active_engrams.focus == []
        assert wm.active_engrams.supporting == []
        assert wm.active_engrams.peripheral == []

    def test_default_schema_version(self) -> None:
        wm = WorkingMemory.default("bank")
        assert wm.schema_version == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# WorkingMemory roundtrip (to_dict / from_dict)
# ---------------------------------------------------------------------------


class TestWorkingMemoryRoundtrip:
    def _roundtrip(self, wm: WorkingMemory) -> WorkingMemory:
        return WorkingMemory.from_dict(wm.to_dict())

    def test_bank_id_preserved(self) -> None:
        wm = make_wm("test-bank")
        assert self._roundtrip(wm).bank_id == "test-bank"

    def test_goal_stack_preserved(self) -> None:
        wm = make_wm()
        rt = self._roundtrip(wm)
        assert len(rt.goal_stack) == 2
        assert rt.goal_stack[0].id == "g1"
        assert rt.goal_stack[1].id == "g2"
        assert rt.goal_stack[0].status == "active"
        assert rt.goal_stack[0].priority == pytest.approx(0.9)

    def test_active_engrams_preserved(self) -> None:
        wm = make_wm()
        rt = self._roundtrip(wm)
        assert len(rt.active_engrams.focus) == 1
        assert rt.active_engrams.focus[0].engram_id == "e1"
        assert rt.active_engrams.focus[0].strength == pytest.approx(0.8)
        assert len(rt.active_engrams.supporting) == 1
        assert len(rt.active_engrams.peripheral) == 1

    def test_confirmed_inferences_preserved(self) -> None:
        wm = make_wm()
        rt = self._roundtrip(wm)
        assert len(rt.confirmed_inferences) == 1
        assert rt.confirmed_inferences[0].id == "i1"
        assert rt.confirmed_inferences[0].content == "hypothesis"
        assert rt.confirmed_inferences[0].confidence == pytest.approx(0.7)

    def test_session_history_preserved(self) -> None:
        wm = make_wm()
        rt = self._roundtrip(wm)
        assert len(rt.session_history) == 2
        assert rt.session_history[0].session_id == "s1"
        assert rt.session_history[1].session_id == "s2"

    def test_schema_version_preserved(self) -> None:
        wm = make_wm()
        assert self._roundtrip(wm).schema_version == CURRENT_SCHEMA_VERSION

    def test_datetime_timezone_aware(self) -> None:
        wm = make_wm()
        rt = self._roundtrip(wm)
        assert rt.goal_stack[0].created_at.tzinfo is not None
        assert rt.active_engrams.focus[0].activated_at.tzinfo is not None
        assert rt.confirmed_inferences[0].created_at.tzinfo is not None
        assert rt.session_history[0].started_at.tzinfo is not None

    def test_empty_wm_roundtrip(self) -> None:
        wm = WorkingMemory.default("empty-bank")
        rt = self._roundtrip(wm)
        assert rt.bank_id == "empty-bank"
        assert rt.goal_stack == []

    def test_json_serializable(self) -> None:
        wm = make_wm()
        d = wm.to_dict()
        # Must not raise
        json.dumps(d)


# ---------------------------------------------------------------------------
# Schema version mismatch → returns default
# ---------------------------------------------------------------------------


class TestSchemaVersionMismatch:
    def test_wrong_version_returns_default(self) -> None:
        data = make_wm("bank-x").to_dict()
        data["schema_version"] = 99  # future/unknown version
        result = WorkingMemory.from_dict(data)
        assert result.bank_id == "bank-x"
        assert result.goal_stack == []
        assert result.schema_version == CURRENT_SCHEMA_VERSION

    def test_missing_version_returns_default(self) -> None:
        data = make_wm("bank-y").to_dict()
        del data["schema_version"]
        result = WorkingMemory.from_dict(data)
        assert result.goal_stack == []

    def test_corrupt_data_returns_default(self) -> None:
        data = {
            "bank_id": "bank-z",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "goal_stack": [{"broken": "data"}],  # missing required keys
        }
        result = WorkingMemory.from_dict(data)
        assert result.bank_id == "bank-z"
        assert result.goal_stack == []


# ---------------------------------------------------------------------------
# Session History FIFO
# ---------------------------------------------------------------------------


class TestSessionHistoryFIFO:
    def test_add_below_capacity(self) -> None:
        wm = WorkingMemory.default("bank")
        for i in range(5):
            wm.add_session_ref(make_session_ref(f"s{i}"))
        assert len(wm.session_history) == 5

    def test_add_at_capacity(self) -> None:
        wm = WorkingMemory.default("bank")
        for i in range(SESSION_HISTORY_MAX):
            wm.add_session_ref(make_session_ref(f"s{i}"))
        assert len(wm.session_history) == SESSION_HISTORY_MAX

    def test_add_evicts_oldest(self) -> None:
        wm = WorkingMemory.default("bank")
        for i in range(SESSION_HISTORY_MAX):
            wm.add_session_ref(make_session_ref(f"s{i}"))
        # Adding the 21st entry must evict s0 (oldest)
        wm.add_session_ref(make_session_ref("s-new"))
        assert len(wm.session_history) == SESSION_HISTORY_MAX
        assert wm.session_history[0].session_id == "s1"
        assert wm.session_history[-1].session_id == "s-new"

    def test_newest_at_end(self) -> None:
        wm = WorkingMemory.default("bank")
        wm.add_session_ref(make_session_ref("first"))
        wm.add_session_ref(make_session_ref("second"))
        assert wm.session_history[-1].session_id == "second"


# ---------------------------------------------------------------------------
# WorkingMemoryRepository — mock conn
# ---------------------------------------------------------------------------


class TestWorkingMemoryRepository:
    @pytest.mark.asyncio
    async def test_load_no_row_returns_default(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        repo = WorkingMemoryRepository()
        wm = await repo.load(conn, "new-bank")
        assert wm.bank_id == "new-bank"
        assert wm.goal_stack == []

    @pytest.mark.asyncio
    async def test_load_existing_row(self) -> None:
        wm_source = make_wm("existing-bank")
        state_dict = wm_source.to_dict()
        state_dict.pop("bank_id")
        state_dict.pop("schema_version")

        row = {"state": state_dict, "schema_version": CURRENT_SCHEMA_VERSION}
        conn = AsyncMock()
        conn.fetchrow.return_value = row

        repo = WorkingMemoryRepository()
        wm = await repo.load(conn, "existing-bank")
        assert wm.bank_id == "existing-bank"
        assert len(wm.goal_stack) == 2
        assert len(wm.active_engrams.focus) == 1

    @pytest.mark.asyncio
    async def test_load_injects_bank_id(self) -> None:
        row = {
            "state": {"goal_stack": [], "active_engrams": {"focus": [], "supporting": [], "peripheral": []},
                      "confirmed_inferences": [], "session_history": []},
            "schema_version": CURRENT_SCHEMA_VERSION,
        }
        conn = AsyncMock()
        conn.fetchrow.return_value = row

        repo = WorkingMemoryRepository()
        wm = await repo.load(conn, "injected-bank")
        assert wm.bank_id == "injected-bank"

    @pytest.mark.asyncio
    async def test_save_calls_execute(self) -> None:
        conn = AsyncMock()
        repo = WorkingMemoryRepository()
        wm = make_wm("save-bank")
        await repo.save(conn, wm)
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_does_not_include_bank_id_in_json(self) -> None:
        conn = AsyncMock()
        repo = WorkingMemoryRepository()
        wm = make_wm("save-bank")
        await repo.save(conn, wm)

        call_args = conn.execute.call_args
        state_json = call_args.args[2]  # $2 positional arg
        state_dict = json.loads(state_json)
        assert "bank_id" not in state_dict
        assert "schema_version" not in state_dict

    @pytest.mark.asyncio
    async def test_save_passes_bank_id_as_first_arg(self) -> None:
        conn = AsyncMock()
        repo = WorkingMemoryRepository()
        wm = make_wm("target-bank")
        await repo.save(conn, wm)

        call_args = conn.execute.call_args
        assert call_args.args[1] == "target-bank"  # $1 positional arg

    @pytest.mark.asyncio
    async def test_exists_true(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = {"1": 1}
        repo = WorkingMemoryRepository()
        assert await repo.exists(conn, "bank") is True

    @pytest.mark.asyncio
    async def test_exists_false(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        repo = WorkingMemoryRepository()
        assert await repo.exists(conn, "bank") is False


# ---------------------------------------------------------------------------
# SessionManager — working memory methods
# ---------------------------------------------------------------------------


class TestSessionManagerWorkingMemory:
    @pytest.mark.asyncio
    async def test_initialize_and_get_working_memory(self) -> None:
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        session = await sm.create_session()
        wm = make_wm("bank-1")
        sm.initialize_working_memory(session.session_id, wm)
        result = sm.get_working_memory(session.session_id)
        assert result is wm

    @pytest.mark.asyncio
    async def test_get_working_memory_none_for_transient_session(self) -> None:
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        session = await sm.create_session()
        # No initialize_working_memory called → should be None
        assert sm.get_working_memory(session.session_id) is None

    @pytest.mark.asyncio
    async def test_get_working_memory_unknown_session(self) -> None:
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        assert sm.get_working_memory(uuid4()) is None

    @pytest.mark.asyncio
    async def test_initialize_overwrites_existing(self) -> None:
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        session = await sm.create_session()
        wm1 = WorkingMemory.default("bank-a")
        wm2 = WorkingMemory.default("bank-b")
        sm.initialize_working_memory(session.session_id, wm1)
        sm.initialize_working_memory(session.session_id, wm2)
        assert sm.get_working_memory(session.session_id) is wm2


# ---------------------------------------------------------------------------
# create_session_async — Priming Effect
# ---------------------------------------------------------------------------


class TestCreateSessionAsyncPrimingEffect:
    @pytest.mark.asyncio
    async def test_primes_working_context_from_wm(self) -> None:
        """create_session_async must copy WM state into the new WorkingContext."""
        from hindsight_api.engine.memory_engine import MemoryEngine
        from hindsight_api.engine.session.session_manager import SessionManager

        # Build WM with pre-existing state
        wm = make_wm("prime-bank")

        # Mock the engine context internals
        sm = SessionManager()
        mock_ctx = MagicMock()
        mock_ctx.get_session_manager.return_value = sm

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # WM load will return None → default
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get_pool = AsyncMock(return_value=mock_pool)

        engine = object.__new__(MemoryEngine)
        engine._ctx = mock_ctx

        # Patch WorkingMemoryRepository.load to return our pre-built WM
        with patch(
            "hindsight_api.engine.working_memory_repo.WorkingMemoryRepository",
            autospec=True,
        ) as MockRepo:
            mock_repo_instance = MockRepo.return_value
            mock_repo_instance.load = AsyncMock(return_value=wm)

            # Patch acquire_with_retry to be a simple async context manager
            with patch("hindsight_api.engine.memory_engine.acquire_with_retry") as mock_acq:
                mock_acq.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

                session = await engine.create_session_async("prime-bank")

        wc = sm.get_working_context(session.session_id)
        assert wc is not None
        assert len(wc.goal_stack) == 2
        assert wc.goal_stack[0].id == "g1"
        assert len(wc.active_engrams.focus) == 1
        assert wc.active_engrams.focus[0].engram_id == "e1"
        assert len(wc.confirmed_inferences) == 1

    @pytest.mark.asyncio
    async def test_wm_stored_in_session_state(self) -> None:
        """create_session_async stores loaded WM in SessionManager."""
        from hindsight_api.engine.memory_engine import MemoryEngine
        from hindsight_api.engine.session.session_manager import SessionManager

        wm = make_wm("wm-bank")
        sm = SessionManager()
        mock_ctx = MagicMock()
        mock_ctx.get_session_manager.return_value = sm
        mock_ctx.get_pool = AsyncMock(return_value=AsyncMock())

        engine = object.__new__(MemoryEngine)
        engine._ctx = mock_ctx

        with patch("hindsight_api.engine.working_memory_repo.WorkingMemoryRepository") as MockRepo:
            mock_repo_instance = MockRepo.return_value
            mock_repo_instance.load = AsyncMock(return_value=wm)

            with patch("hindsight_api.engine.memory_engine.acquire_with_retry") as mock_acq:
                mock_conn = AsyncMock()
                mock_acq.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

                session = await engine.create_session_async("wm-bank")

        assert sm.get_working_memory(session.session_id) is wm


# ---------------------------------------------------------------------------
# end_session_async — WM merge + save
# ---------------------------------------------------------------------------


class TestEndSessionAsync:
    @pytest.mark.asyncio
    async def test_merges_wc_into_wm_and_saves(self) -> None:
        """end_session_async must merge WC state into WM and call repo.save()."""
        from hindsight_api.engine.memory_engine import MemoryEngine
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        session = await sm.create_session()

        # Inject WM
        wm = WorkingMemory.default("end-bank")
        sm.initialize_working_memory(session.session_id, wm)

        # Modify WorkingContext
        wc = sm.get_working_context(session.session_id)
        assert wc is not None
        wc.goal_stack.append(make_goal("g-new"))

        mock_ctx = MagicMock()
        mock_ctx.get_session_manager.return_value = sm
        mock_conn = AsyncMock()
        mock_ctx.get_pool = AsyncMock(return_value=AsyncMock())

        engine = object.__new__(MemoryEngine)
        engine._ctx = mock_ctx

        mock_save = AsyncMock()
        with patch("hindsight_api.engine.working_memory_repo.WorkingMemoryRepository") as MockRepo:
            MockRepo.return_value.save = mock_save

            with patch("hindsight_api.engine.memory_engine.acquire_with_retry") as mock_acq:
                mock_acq.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

                await engine.end_session_async(session.session_id, "end-bank")

        mock_save.assert_called_once()
        saved_wm = mock_save.call_args.args[1]
        assert len(saved_wm.goal_stack) == 1
        assert saved_wm.goal_stack[0].id == "g-new"

    @pytest.mark.asyncio
    async def test_appends_session_to_history(self) -> None:
        """end_session_async must add a SessionRef to WM.session_history."""
        from hindsight_api.engine.memory_engine import MemoryEngine
        from hindsight_api.engine.session.session_manager import SessionManager

        sm = SessionManager()
        session = await sm.create_session()

        wm = WorkingMemory.default("hist-bank")
        sm.initialize_working_memory(session.session_id, wm)

        mock_ctx = MagicMock()
        mock_ctx.get_session_manager.return_value = sm
        mock_ctx.get_pool = AsyncMock(return_value=AsyncMock())

        engine = object.__new__(MemoryEngine)
        engine._ctx = mock_ctx

        mock_save = AsyncMock()
        with patch("hindsight_api.engine.working_memory_repo.WorkingMemoryRepository") as MockRepo:
            MockRepo.return_value.save = mock_save

            with patch("hindsight_api.engine.memory_engine.acquire_with_retry") as mock_acq:
                mock_acq.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
                mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

                await engine.end_session_async(session.session_id, "hist-bank")

        saved_wm = mock_save.call_args.args[1]
        assert len(saved_wm.session_history) == 1
        assert saved_wm.session_history[0].session_id == str(session.session_id)
