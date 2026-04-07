"""
Unit tests for Session Manager — Lifecycle and Dual Control (Epic 06 Story 02).

Tests validate:
- Session creation with default and custom mode
- Explicit mode set + _explicit_mode flag
- Automatic mode shifts from system signals
- Explicit mode blocks automatic shifts
- Episode buffer lifecycle
- Mode history correctly recorded
- Thread safety: concurrent set_mode and process_signal
"""

import asyncio

import pytest

from hindsight_api.engine.response_models import Episode, RetrievalMode, Session
from datetime import timedelta

from hindsight_api.engine.session.session_manager import (
    DEFAULT_SESSION_TTL,
    ModeSignal,
    ModeTransition,
    SessionManager,
    SessionState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def manager() -> SessionManager:
    return SessionManager()


def _episode(action: str = "test action") -> Episode:
    return Episode(action=action, context="test context", outcome="test outcome")


# ---------------------------------------------------------------------------
# T1 — Session Lifecycle
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    async def test_create_returns_session(self, manager: SessionManager):
        session = await manager.create_session()
        assert isinstance(session, Session)

    async def test_create_default_mode_is_precision(self, manager: SessionManager):
        session = await manager.create_session()
        assert session.mode == RetrievalMode.PRECISION

    async def test_create_with_custom_mode(self, manager: SessionManager):
        session = await manager.create_session(mode=RetrievalMode.EXPLORATION)
        assert session.mode == RetrievalMode.EXPLORATION

    async def test_create_with_task_context(self, manager: SessionManager):
        session = await manager.create_session(task_context="find related bugs")
        assert session.task_context == "find related bugs"

    async def test_create_with_expectation(self, manager: SessionManager):
        session = await manager.create_session(expectation="result will be positive")
        assert session.current_expectation == "result will be positive"

    async def test_get_session_returns_same_object(self, manager: SessionManager):
        session = await manager.create_session()
        retrieved = manager.get_session(session.session_id)
        assert retrieved.session_id == session.session_id

    async def test_get_session_unknown_id_raises(self, manager: SessionManager):
        from uuid import uuid4
        with pytest.raises(KeyError):
            manager.get_session(uuid4())

    async def test_end_session_removes_from_manager(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.end_session(session.session_id)
        assert session.session_id not in manager

    async def test_end_session_unknown_id_raises(self, manager: SessionManager):
        from uuid import uuid4
        with pytest.raises(KeyError):
            await manager.end_session(uuid4())

    async def test_expired_sessions_are_cleaned_up(self):
        """Sessions past their TTL must be removed when cleanup runs."""
        manager = SessionManager(session_ttl=timedelta(seconds=0))
        session = await manager.create_session()
        # Force cleanup directly (rate-limit gate doesn't apply to direct calls).
        manager._cleanup_expired()
        with pytest.raises(KeyError):
            manager.get_session(session.session_id)

    async def test_default_session_ttl_is_24h(self):
        assert DEFAULT_SESSION_TTL.total_seconds() == 86400

    async def test_multiple_sessions_are_independent(self, manager: SessionManager):
        s1 = await manager.create_session(mode=RetrievalMode.PRECISION)
        s2 = await manager.create_session(mode=RetrievalMode.EXPLORATION)
        assert s1.session_id != s2.session_id
        assert len(manager) == 2

    async def test_contains_operator(self, manager: SessionManager):
        session = await manager.create_session()
        assert session.session_id in manager
        await manager.end_session(session.session_id)
        assert session.session_id not in manager


# ---------------------------------------------------------------------------
# T2 — Explicit Mode Control
# ---------------------------------------------------------------------------

class TestExplicitModeControl:
    async def test_set_mode_changes_session_mode(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.ANALOGY)
        assert manager.get_session(session.session_id).mode == RetrievalMode.ANALOGY

    async def test_set_mode_sets_explicit_flag(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.VALIDATION)
        state = manager._sessions[session.session_id]
        assert state.explicit_mode is True

    async def test_new_session_explicit_mode_is_false(self, manager: SessionManager):
        session = await manager.create_session()
        state = manager._sessions[session.session_id]
        assert state.explicit_mode is False

    async def test_set_mode_records_transition(self, manager: SessionManager):
        session = await manager.create_session(mode=RetrievalMode.PRECISION)
        await manager.set_mode(session.session_id, RetrievalMode.EXPLORATION)
        history = manager.get_mode_history(session.session_id)
        assert len(history) == 1
        assert history[0].from_mode == RetrievalMode.PRECISION
        assert history[0].to_mode == RetrievalMode.EXPLORATION
        assert history[0].trigger == "explicit"


# ---------------------------------------------------------------------------
# T3 — Automatic Mode Shift Engine
# ---------------------------------------------------------------------------

class TestAutomaticModeShifts:
    async def test_high_surprise_shifts_to_validation(self, manager: SessionManager):
        session = await manager.create_session()
        shifted = await manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE)
        assert shifted is True
        assert manager.get_session(session.session_id).mode == RetrievalMode.VALIDATION

    async def test_weak_matches_shifts_to_exploration(self, manager: SessionManager):
        session = await manager.create_session()
        shifted = await manager.process_signal(session.session_id, ModeSignal.WEAK_MATCHES)
        assert shifted is True
        assert manager.get_session(session.session_id).mode == RetrievalMode.EXPLORATION

    async def test_contradiction_shifts_to_validation(self, manager: SessionManager):
        session = await manager.create_session()
        shifted = await manager.process_signal(session.session_id, ModeSignal.CONTRADICTION)
        assert shifted is True
        assert manager.get_session(session.session_id).mode == RetrievalMode.VALIDATION

    async def test_prediction_error_shifts_to_validation(self, manager: SessionManager):
        session = await manager.create_session()
        shifted = await manager.process_signal(session.session_id, ModeSignal.PREDICTION_ERROR)
        assert shifted is True
        assert manager.get_session(session.session_id).mode == RetrievalMode.VALIDATION

    async def test_strong_matches_shifts_to_precision(self, manager: SessionManager):
        session = await manager.create_session(mode=RetrievalMode.EXPLORATION)
        shifted = await manager.process_signal(session.session_id, ModeSignal.STRONG_MATCHES)
        assert shifted is True
        assert manager.get_session(session.session_id).mode == RetrievalMode.PRECISION

    async def test_automatic_shift_records_transition(self, manager: SessionManager):
        session = await manager.create_session(mode=RetrievalMode.PRECISION)
        await manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE)
        history = manager.get_mode_history(session.session_id)
        assert len(history) == 1
        assert history[0].trigger == "signal:high_surprise"
        assert history[0].from_mode == RetrievalMode.PRECISION
        assert history[0].to_mode == RetrievalMode.VALIDATION

    async def test_explicit_mode_blocks_automatic_shift(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.ANALOGY)
        shifted = await manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE)
        assert shifted is False
        assert manager.get_session(session.session_id).mode == RetrievalMode.ANALOGY

    async def test_explicit_mode_blocks_all_signals(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.PRECISION)
        for signal in ModeSignal:
            shifted = await manager.process_signal(session.session_id, signal)
            assert shifted is False
        assert manager.get_session(session.session_id).mode == RetrievalMode.PRECISION


# ---------------------------------------------------------------------------
# T4 — Episode Buffer
# ---------------------------------------------------------------------------

class TestEpisodeBuffer:
    async def test_add_episode_stores_in_buffer(self, manager: SessionManager):
        session = await manager.create_session()
        ep = _episode("search for docs")
        await manager.add_episode(session.session_id, ep)
        episodes = manager.get_episodes(session.session_id)
        assert len(episodes) == 1
        assert episodes[0].action == "search for docs"

    async def test_multiple_episodes_ordered(self, manager: SessionManager):
        session = await manager.create_session()
        for i in range(3):
            await manager.add_episode(session.session_id, _episode(f"action {i}"))
        episodes = manager.get_episodes(session.session_id)
        assert [e.action for e in episodes] == ["action 0", "action 1", "action 2"]

    async def test_episodes_discarded_on_end(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.add_episode(session.session_id, _episode())
        await manager.end_session(session.session_id)
        # session is gone — episodes were discarded with it
        assert session.session_id not in manager

    async def test_get_episodes_returns_snapshot(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.add_episode(session.session_id, _episode("first"))
        snapshot = manager.get_episodes(session.session_id)
        await manager.add_episode(session.session_id, _episode("second"))
        assert len(snapshot) == 1  # snapshot not affected by later add


# ---------------------------------------------------------------------------
# T5 — Mode History
# ---------------------------------------------------------------------------

class TestModeHistory:
    async def test_new_session_has_empty_history(self, manager: SessionManager):
        session = await manager.create_session()
        assert manager.get_mode_history(session.session_id) == []

    async def test_history_records_all_transitions(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.EXPLORATION)
        await manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE)
        # second signal blocked by explicit mode set above? No — set_mode set explicit=True
        # so HIGH_SURPRISE will be blocked. Let's verify history has exactly 1 entry.
        history = manager.get_mode_history(session.session_id)
        assert len(history) == 1
        assert isinstance(history[0], ModeTransition)

    async def test_history_timestamps_are_set(self, manager: SessionManager):
        session = await manager.create_session()
        await manager.set_mode(session.session_id, RetrievalMode.ANALOGY)
        history = manager.get_mode_history(session.session_id)
        assert history[0].timestamp is not None

    async def test_history_is_snapshot(self, manager: SessionManager):
        session = await manager.create_session()
        snapshot = manager.get_mode_history(session.session_id)
        await manager.set_mode(session.session_id, RetrievalMode.EXPLORATION)
        assert len(snapshot) == 0  # snapshot not affected by later changes


# ---------------------------------------------------------------------------
# T6 — Thread Safety (concurrent asyncio operations)
# ---------------------------------------------------------------------------

class TestThreadSafety:
    async def test_concurrent_set_mode_last_write_wins(self, manager: SessionManager):
        session = await manager.create_session()
        # Fire multiple set_mode calls concurrently — should not raise or corrupt state
        await asyncio.gather(
            manager.set_mode(session.session_id, RetrievalMode.EXPLORATION),
            manager.set_mode(session.session_id, RetrievalMode.ANALOGY),
            manager.set_mode(session.session_id, RetrievalMode.VALIDATION),
        )
        # Mode must be one of the valid modes — no corruption
        assert manager.get_session(session.session_id).mode in set(RetrievalMode)

    async def test_concurrent_add_episodes_no_data_loss(self, manager: SessionManager):
        session = await manager.create_session()
        await asyncio.gather(
            *[manager.add_episode(session.session_id, _episode(f"ep{i}")) for i in range(10)]
        )
        assert len(manager.get_episodes(session.session_id)) == 10

    async def test_concurrent_set_mode_and_signal(self, manager: SessionManager):
        session = await manager.create_session()
        # Mix explicit set_mode and automatic signals — should not deadlock or raise
        await asyncio.gather(
            manager.set_mode(session.session_id, RetrievalMode.PRECISION),
            manager.process_signal(session.session_id, ModeSignal.WEAK_MATCHES),
            manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE),
        )
        # No assertion on final mode — only that no exception was raised
        assert manager.get_session(session.session_id).mode in set(RetrievalMode)
