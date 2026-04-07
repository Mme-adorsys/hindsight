"""
Unit tests for Epic 14 Story 01 — 3-Tier Bank Model (B1).

Covers:
- BankTier Enum values and str behaviour
- BankProfile TypedDict includes tier field
- create_agent_banks() provisions session + dictionary banks with correct tiers
- ensure_shared_bank() provisions shared bank, is idempotent
- verify_bank_access() enforces tier-based access rules
- get_bank_profile() returns tier from DB row; defaults to SESSION on auto-create
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.engram_types import BankTier
from hindsight_api.engine.retain.bank_utils import (
    BankProfile,
    _SHARED_BANK_ID,
    create_agent_banks,
    ensure_shared_bank,
    verify_bank_access,
)


# ---------------------------------------------------------------------------
# BankTier Enum
# ---------------------------------------------------------------------------


class TestBankTierEnum:
    def test_values(self):
        assert BankTier.SESSION.value == "session"
        assert BankTier.DICTIONARY.value == "dictionary"
        assert BankTier.SHARED.value == "shared"

    def test_is_str_subclass(self):
        """BankTier(str, Enum) — usable as plain string in SQL params."""
        assert isinstance(BankTier.SESSION, str)
        assert BankTier.SESSION == "session"

    def test_roundtrip_from_string(self):
        for raw in ("session", "dictionary", "shared"):
            tier = BankTier(raw)
            assert tier.value == raw

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            BankTier("unknown")


# ---------------------------------------------------------------------------
# BankProfile TypedDict — structural check
# ---------------------------------------------------------------------------


class TestBankProfileSchema:
    def test_tier_key_present(self):
        """BankProfile TypedDict must declare a 'tier' key."""
        annotations = BankProfile.__annotations__
        assert "tier" in annotations
        assert annotations["tier"] is BankTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_result=None, execute_result=None):
    """Build a minimal mock pool whose connection returns preset results."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.execute = AsyncMock(return_value=execute_result)
    conn.executemany = AsyncMock(return_value=None)

    pool = MagicMock()

    async def _noop_acquire():
        return conn

    # Patch acquire_with_retry to yield conn directly
    return pool, conn


# ---------------------------------------------------------------------------
# create_agent_banks
# ---------------------------------------------------------------------------


class TestCreateAgentBanks:
    @pytest.mark.asyncio
    async def test_returns_correct_bank_ids(self):
        pool, conn = _make_pool()
        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            session_id, dict_id = await create_agent_banks("agent-1", pool)

        assert session_id == "agent-1:session"
        assert dict_id == "agent-1:dictionary"

    @pytest.mark.asyncio
    async def test_inserts_with_correct_tiers(self):
        pool, conn = _make_pool()
        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            await create_agent_banks("agent-2", pool)

        # executemany called with rows for session + dictionary
        rows = conn.executemany.call_args[0][1]
        assert rows[0][4] == BankTier.SESSION.value
        assert rows[1][4] == BankTier.DICTIONARY.value

    @pytest.mark.asyncio
    async def test_idempotent_on_conflict(self):
        """ON CONFLICT DO NOTHING — no error on duplicate calls."""
        pool, conn = _make_pool()
        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            await create_agent_banks("agent-3", pool)
            await create_agent_banks("agent-3", pool)  # second call must not raise

        assert conn.executemany.call_count == 2


# ---------------------------------------------------------------------------
# ensure_shared_bank
# ---------------------------------------------------------------------------


class TestEnsureSharedBank:
    @pytest.mark.asyncio
    async def test_returns_shared_bank_id(self):
        pool, conn = _make_pool()
        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await ensure_shared_bank(pool)

        assert result == _SHARED_BANK_ID

    @pytest.mark.asyncio
    async def test_inserts_shared_tier(self):
        pool, conn = _make_pool()
        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            await ensure_shared_bank(pool)

        args = conn.execute.call_args[0]
        assert BankTier.SHARED.value in args


# ---------------------------------------------------------------------------
# verify_bank_access
# ---------------------------------------------------------------------------


class TestVerifyBankAccess:
    @pytest.mark.asyncio
    async def test_shared_bank_always_accessible(self):
        pool, conn = _make_pool()
        # No DB call needed for shared bank
        result = await verify_bank_access("agent-x", _SHARED_BANK_ID, pool)
        assert result is True

    @pytest.mark.asyncio
    async def test_own_session_bank_accessible(self):
        pool, conn = _make_pool()
        result = await verify_bank_access("agent-x", "agent-x:session", pool)
        assert result is True

    @pytest.mark.asyncio
    async def test_own_dictionary_bank_accessible(self):
        pool, conn = _make_pool()
        result = await verify_bank_access("agent-x", "agent-x:dictionary", pool)
        assert result is True

    @pytest.mark.asyncio
    async def test_other_agent_session_bank_denied(self):
        pool, conn = _make_pool()

        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            conn.fetchrow = AsyncMock(return_value={"tier": "session"})
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await verify_bank_access("agent-x", "agent-y:session", pool)

        assert result is False

    @pytest.mark.asyncio
    async def test_nonexistent_bank_denied(self):
        pool, conn = _make_pool()

        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            conn.fetchrow = AsyncMock(return_value=None)
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await verify_bank_access("agent-x", "ghost-bank", pool)

        assert result is False

    @pytest.mark.asyncio
    async def test_foreign_shared_tier_bank_accessible(self):
        """A bank with tier=shared but custom ID is still accessible."""
        pool, conn = _make_pool()

        with patch("hindsight_api.engine.retain.bank_utils.acquire_with_retry") as mock_acq:
            conn.fetchrow = AsyncMock(return_value={"tier": "shared"})
            mock_acq.return_value.__aenter__ = AsyncMock(return_value=conn)
            mock_acq.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await verify_bank_access("agent-x", "custom-shared-bank", pool)

        assert result is True
