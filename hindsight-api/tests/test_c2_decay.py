"""Unit tests for Epic 25 Story 11 — C2 decay re-evaluation.

Pure unit: asyncpg connection mocked. The composite math runs for real
through ``compute_equilibrium_rate`` + ``compute_composite`` so the test
exercises the real scoring contract; only the database round-trips are
mocked. Advisory lock behaviour is asserted via the SQL the helper emits.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.c2_decay import (
    DecayReport,
    _bank_advisory_lock_key,
    _composite_for,
    decay_reevaluate_buffer,
)
from hindsight_api.engine.consolidation.constants import BUFFER_ARCHIVE_COMPOSITE_THRESHOLD


def _conn(*, lock_acquired: bool = True, entries: list[dict] | None = None, session_count: int = 5) -> AsyncMock:
    """Build a MagicMock connection that walks through decay_reevaluate_buffer."""
    conn = AsyncMock()
    # SELECT pg_try_advisory_lock returns boolean; SELECT pg_advisory_unlock returns void.
    conn.fetchval = AsyncMock(return_value=lock_acquired)
    # filter_entries / increment_bank_session_count get patched separately;
    # only the UPDATE archive call hits conn.execute.
    conn.execute = AsyncMock()
    return conn


def _wired_pool(conn: AsyncMock):
    pool = MagicMock()

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    return pool, _ctx


def _patch_acquire(ctx):
    return patch("hindsight_api.engine.consolidation.c2_decay.acquire_with_retry", new=ctx)


# Quickly synthesise a `engram_dictionary` row for the helpers under test.
def _entry(*, thalamus_overall: float, access_count: int, created_at_session: int) -> dict:
    return {
        "engram_id": uuid.uuid4(),
        "thalamus_overall": thalamus_overall,
        "access_count": access_count,
        "created_at_session": created_at_session,
        "novelty": 0.5,
        "surprise": 0.5,
        "task_relevance": 0.5,
        "emotional_valence": 0.5,
    }


# ---------------------------------------------------------------------------
# Drift guard + lock-key sanity
# ---------------------------------------------------------------------------


def test_threshold_locked_to_concept():
    assert BUFFER_ARCHIVE_COMPOSITE_THRESHOLD == 0.05


class TestAdvisoryLockKey:
    def test_deterministic(self):
        # Same bank id always yields the same key — banks must be stable
        # across processes.
        assert _bank_advisory_lock_key("bank-A") == _bank_advisory_lock_key("bank-A")

    def test_within_pg_bigint_range(self):
        # Postgres bigint is signed 64-bit. Our key must fit positive 63-bit
        # so pg_try_advisory_lock(int8) accepts it.
        key = _bank_advisory_lock_key("bank-A")
        assert 0 <= key < (1 << 63)

    def test_distinct_banks_yield_distinct_keys(self):
        # Hash collisions are theoretically possible but vanishingly unlikely
        # for 8-byte digests; the helper just needs to spread reasonably.
        assert _bank_advisory_lock_key("bank-A") != _bank_advisory_lock_key("bank-B")


# ---------------------------------------------------------------------------
# _composite_for — math runs through real scoring helpers
# ---------------------------------------------------------------------------


class TestComposite:
    def test_fresh_engram_keeps_thalamus_value(self):
        # session_count == created_at_session → sessions_alive=0 → decay=1.0.
        e = _entry(thalamus_overall=0.7, access_count=3, created_at_session=5)
        composite = _composite_for(e, session_count=5, bank_size=10)
        # decay can be > 1.0 when access dominates, so compare around the
        # birth value rather than equality.
        assert composite >= 0.5

    def test_old_unaccessed_engram_falls_below_threshold(self):
        # Many sessions old, low thalamus, no access → composite ≈ 0.
        e = _entry(thalamus_overall=0.1, access_count=0, created_at_session=0)
        composite = _composite_for(e, session_count=200, bank_size=100)
        assert composite < BUFFER_ARCHIVE_COMPOSITE_THRESHOLD

    def test_zero_thalamus_yields_zero(self):
        e = _entry(thalamus_overall=0.0, access_count=10, created_at_session=0)
        composite = _composite_for(e, session_count=100, bank_size=10)
        assert composite == 0.0


# ---------------------------------------------------------------------------
# decay_reevaluate_buffer — full pipeline
# ---------------------------------------------------------------------------


class TestDecayReevaluate:
    async def test_high_composite_engrams_retained(self):
        # Two fresh engrams with strong thalamus_overall → both retained.
        entries = [
            _entry(thalamus_overall=0.8, access_count=5, created_at_session=10),
            _entry(thalamus_overall=0.7, access_count=3, created_at_session=10),
        ]
        conn = _conn()
        pool, ctx = _wired_pool(conn)
        with (
            _patch_acquire(ctx),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.increment_bank_session_count",
                new=AsyncMock(return_value=11),  # session_count after bump
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.filter_entries",
                new=AsyncMock(return_value=entries),
            ),
        ):
            report = await decay_reevaluate_buffer("bank-A", pool)
        assert isinstance(report, DecayReport)
        assert report.total == 2
        assert report.archived == 0
        assert report.retained == 2
        # No archive UPDATE issued.
        assert not any("UPDATE engram_dictionary" in str(call.args[0]) for call in conn.execute.await_args_list[:1])

    async def test_low_composite_engrams_archived(self):
        # Both engrams are old + low thalamus → both archived.
        entries = [
            _entry(thalamus_overall=0.05, access_count=0, created_at_session=0),
            _entry(thalamus_overall=0.02, access_count=0, created_at_session=0),
        ]
        conn = _conn()
        pool, ctx = _wired_pool(conn)
        with (
            _patch_acquire(ctx),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.increment_bank_session_count",
                new=AsyncMock(return_value=200),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.filter_entries",
                new=AsyncMock(return_value=entries),
            ),
        ):
            report = await decay_reevaluate_buffer("bank-A", pool)
        assert report.archived == 2
        assert report.retained == 0
        # Verify the archive UPDATE was issued with both ids.
        archive_call = next(
            call for call in conn.execute.await_args_list if "UPDATE engram_dictionary" in str(call.args[0])
        )
        sql = archive_call.args[0]
        assert "status = 'archived'" in sql
        archived_ids = archive_call.args[1]
        assert len(archived_ids) == 2

    async def test_advisory_lock_busy_returns_skipped_report(self):
        conn = _conn(lock_acquired=False)
        pool, ctx = _wired_pool(conn)
        # filter_entries / increment must not even be called when the lock is busy.
        with (
            _patch_acquire(ctx),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.increment_bank_session_count",
                new=AsyncMock(),
            ) as bump,
            patch(
                "hindsight_api.engine.consolidation.c2_decay.filter_entries",
                new=AsyncMock(),
            ) as fetch,
        ):
            report = await decay_reevaluate_buffer("bank-A", pool)
        assert report.skipped_locked is True
        assert report.total == 0
        bump.assert_not_called()
        fetch.assert_not_called()

    async def test_lock_released_in_finally_path(self):
        # Even on error inside the locked section, pg_advisory_unlock must run.
        conn = _conn()
        pool, ctx = _wired_pool(conn)
        with (
            _patch_acquire(ctx),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.increment_bank_session_count",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.filter_entries",
                new=AsyncMock(),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await decay_reevaluate_buffer("bank-A", pool)
        # Last conn.execute call must be the unlock (UPDATE etc. never ran).
        unlocks = [c for c in conn.execute.await_args_list if "pg_advisory_unlock" in str(c.args[0])]
        assert len(unlocks) == 1

    async def test_empty_bank_returns_zero_report(self):
        conn = _conn()
        pool, ctx = _wired_pool(conn)
        with (
            _patch_acquire(ctx),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.increment_bank_session_count",
                new=AsyncMock(return_value=1),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_decay.filter_entries",
                new=AsyncMock(return_value=[]),
            ),
        ):
            report = await decay_reevaluate_buffer("bank-A", pool)
        assert report == DecayReport(bank_id="bank-A", total=0, archived=0, retained=0)
