"""
Unit tests for Session Layer — MemoryEngine Integration (Epic 06 Story 03).

Tests validate WITHOUT requiring a running database or LLM:
- _resolve_session_config returns correct ModeConfig for each mode
- _resolve_session_config returns Precision default when session is None
- _get_session_manager lazy-creates SessionManager on first call
- _get_session_manager returns injected instance when provided
- _session_from_mode (API helper) constructs Session correctly
- _session_from_mode returns None for None input
- _session_from_mode raises HTTPException for invalid mode
- MemoryEngine constructor accepts session_manager kwarg

Note (2026-04-09): Updated after the MemoryEngine God-Object decomposition.
`_resolve_session_config` and `_get_session_manager` are now thin facades
that delegate to `self._ctx` (EngineContext). The test helpers construct a
bare EngineContext via `object.__new__` and populate only the session-manager
attributes — no DB pool, LLM client, or embeddings needed.
"""

import pytest

from hindsight_api.api.http import _session_from_mode
from hindsight_api.engine.response_models import RetrievalMode, Session
from hindsight_api.engine.session.mode_config import MODE_PROFILES, ModeConfig
from hindsight_api.engine.session.session_manager import SessionManager


def _make_minimal_context(session_manager=None):
    """Create a bare EngineContext with only the session-manager attributes.

    The real EngineContext constructor pulls config, DB pool, LLM clients
    etc. For tests that only exercise `resolve_session_config()` and
    `get_session_manager()` we bypass __init__ entirely and populate the
    exact attributes those two methods touch.
    """
    import threading

    from hindsight_api.engine.engine_context import EngineContext

    ctx = object.__new__(EngineContext)
    ctx._session_manager = session_manager
    ctx._session_manager_lock = threading.Lock()
    return ctx


def _make_minimal_engine(session_manager=None):
    """Create a minimal MemoryEngine with only the `_ctx` attribute set.

    Good enough for tests that call `_resolve_session_config` or
    `_get_session_manager`, both of which delegate to `self._ctx`.
    """
    from hindsight_api.engine.memory_engine import MemoryEngine

    engine = object.__new__(MemoryEngine)
    engine._ctx = _make_minimal_context(session_manager=session_manager)
    return engine


# ---------------------------------------------------------------------------
# _resolve_session_config (T3)
# ---------------------------------------------------------------------------


class TestResolveSessionConfig:
    """Test the _resolve_session_config helper without spinning up MemoryEngine."""

    def _make_minimal_engine(self):
        """Create a minimal MemoryEngine-like object with just the helper method."""
        return _make_minimal_engine()

    def test_none_session_returns_precision_config(self):
        engine = self._make_minimal_engine()
        config = engine._resolve_session_config(None)
        assert config == MODE_PROFILES[RetrievalMode.PRECISION]

    def test_precision_session_returns_precision_config(self):
        engine = self._make_minimal_engine()
        session = Session(mode=RetrievalMode.PRECISION)
        config = engine._resolve_session_config(session)
        assert config == MODE_PROFILES[RetrievalMode.PRECISION]

    def test_exploration_session_returns_exploration_config(self):
        engine = self._make_minimal_engine()
        session = Session(mode=RetrievalMode.EXPLORATION)
        config = engine._resolve_session_config(session)
        assert config == MODE_PROFILES[RetrievalMode.EXPLORATION]
        # Exploration threshold lowered to 0.0 on 2026-04-09 hardening so the
        # mode can see fresh buffer Engrams (strength ≈ 0.1) during retrieval.
        assert config.strength_pre_filter == 0.0
        assert config.weak_link_policy == "follow"

    def test_analogy_session_returns_analogy_config(self):
        engine = self._make_minimal_engine()
        session = Session(mode=RetrievalMode.ANALOGY)
        config = engine._resolve_session_config(session)
        assert config == MODE_PROFILES[RetrievalMode.ANALOGY]
        assert config.weak_link_policy == "prefer"

    def test_validation_session_returns_validation_config(self):
        engine = self._make_minimal_engine()
        session = Session(mode=RetrievalMode.VALIDATION)
        config = engine._resolve_session_config(session)
        assert config == MODE_PROFILES[RetrievalMode.VALIDATION]
        assert config.reconsolidation_level == "aggressive"

    def test_returns_modeconfig_instance(self):
        engine = self._make_minimal_engine()
        config = engine._resolve_session_config(None)
        assert isinstance(config, ModeConfig)

    def test_all_modes_resolve(self):
        engine = self._make_minimal_engine()
        for mode in RetrievalMode:
            session = Session(mode=mode)
            config = engine._resolve_session_config(session)
            assert isinstance(config, ModeConfig)
            assert config == MODE_PROFILES[mode]


# ---------------------------------------------------------------------------
# _get_session_manager (T2)
# ---------------------------------------------------------------------------


class TestGetSessionManager:
    def _make_minimal_engine(self, session_manager=None):
        return _make_minimal_engine(session_manager=session_manager)

    def test_lazy_creates_session_manager_on_first_call(self):
        engine = self._make_minimal_engine()
        # Session manager lives on the context now, not on the engine.
        assert engine._ctx._session_manager is None
        manager = engine._get_session_manager()
        assert isinstance(manager, SessionManager)
        assert engine._ctx._session_manager is manager  # cached after first call

    def test_returns_injected_session_manager(self):
        injected = SessionManager()
        engine = self._make_minimal_engine(session_manager=injected)
        manager = engine._get_session_manager()
        assert manager is injected

    def test_repeated_calls_return_same_instance(self):
        engine = self._make_minimal_engine()
        m1 = engine._get_session_manager()
        m2 = engine._get_session_manager()
        assert m1 is m2


# ---------------------------------------------------------------------------
# _session_from_mode API helper (T7)
# ---------------------------------------------------------------------------


class TestSessionFromMode:
    def test_none_returns_none(self):
        result = _session_from_mode(None)
        assert result is None

    def test_precision_string_returns_session(self):
        session = _session_from_mode("precision")
        assert isinstance(session, Session)
        assert session.mode == RetrievalMode.PRECISION

    def test_exploration_string_returns_session(self):
        session = _session_from_mode("exploration")
        assert session.mode == RetrievalMode.EXPLORATION

    def test_analogy_string_returns_session(self):
        session = _session_from_mode("analogy")
        assert session.mode == RetrievalMode.ANALOGY

    def test_validation_string_returns_session(self):
        session = _session_from_mode("validation")
        assert session.mode == RetrievalMode.VALIDATION

    def test_uppercase_mode_is_normalised(self):
        session = _session_from_mode("EXPLORATION")
        assert session.mode == RetrievalMode.EXPLORATION

    def test_mixed_case_is_normalised(self):
        session = _session_from_mode("Precision")
        assert session.mode == RetrievalMode.PRECISION

    def test_invalid_mode_raises_http_exception(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _session_from_mode("turbo")
        assert exc_info.value.status_code == 400
        assert "turbo" in exc_info.value.detail

    def test_empty_string_raises_http_exception(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _session_from_mode("")


# ---------------------------------------------------------------------------
# Backward compatibility: no mode → engine uses Precision default
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_resolve_none_equals_precision_profile(self):
        engine = _make_minimal_engine()

        config_none = engine._resolve_session_config(None)
        config_precision = engine._resolve_session_config(Session(mode=RetrievalMode.PRECISION))
        assert config_none == config_precision

    def test_session_from_mode_none_is_backward_compat(self):
        """None mode → None session → engine uses its own default (Precision)."""
        assert _session_from_mode(None) is None
