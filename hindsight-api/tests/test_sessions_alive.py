"""
Unit tests for the Sessions-Alive Taktgeber (Epic 24 Story 01).

Covers:
- ``sessions_alive()`` pure helper — correctness + race-condition guard.
- ``SessionManager.end_session`` raises on double-close → protects the
  idempotency contract of ``MemoryEngine.end_session_async`` which only
  calls ``increment_bank_session_count`` after a successful pop.
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.consolidation.scoring import sessions_alive
from hindsight_api.engine.session.session_manager import SessionManager


# ---------------------------------------------------------------------------
# sessions_alive() pure helper
# ---------------------------------------------------------------------------


class TestSessionsAlive:
    def test_fresh_engram_returns_zero(self) -> None:
        assert sessions_alive(bank_session_count=0, engram_created_at_session=0) == 0

    def test_fresh_engram_mid_bank_returns_zero(self) -> None:
        assert sessions_alive(bank_session_count=42, engram_created_at_session=42) == 0

    def test_engram_survives_several_sessions(self) -> None:
        assert sessions_alive(bank_session_count=10, engram_created_at_session=3) == 7

    def test_engram_newer_than_bank_clamped_to_zero(self) -> None:
        # Race condition: bank counter briefly behind the Engram's snapshot.
        assert sessions_alive(bank_session_count=5, engram_created_at_session=7) == 0

    def test_large_difference(self) -> None:
        assert sessions_alive(bank_session_count=1_000_000, engram_created_at_session=1) == 999_999


# ---------------------------------------------------------------------------
# Idempotency: SessionManager.end_session raises on double-close
# ---------------------------------------------------------------------------


class TestDoubleCloseRaises:
    async def test_end_session_twice_raises_keyerror(self) -> None:
        manager = SessionManager()
        session = await manager.create_session()
        await manager.end_session(session.session_id)
        with pytest.raises(KeyError):
            await manager.end_session(session.session_id)
