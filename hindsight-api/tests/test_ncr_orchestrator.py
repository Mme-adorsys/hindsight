"""
Unit tests for NCR Orchestrator & Scheduler.

Epic 12, Story 05, Task T6.

Tests cover:
- NCRReport dataclass
- NCROrchestrator: all phases run, phase failure does not block others,
  advisory lock prevents parallel runs, report fully populated
- NCRScheduler: start/stop, enabled/disabled
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.consolidation1 import ConsolidationResult
from hindsight_api.engine.consolidation.ncr_decay import DecayResult
from hindsight_api.engine.consolidation.ncr_orchestrator import (
    NCROrchestrator,
    NCRReport,
    NCRScheduler,
    _ncr_lock_id,
)
from hindsight_api.engine.consolidation.ncr_strengthen import StrengthenResult
from hindsight_api.engine.consolidation.schema_processor import SchemaResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    lock_acquired: bool = True,
    consolidation_raises: Exception | None = None,
    decay_raises: Exception | None = None,
    strengthen_raises: Exception | None = None,
    schema_raises: Exception | None = None,
):
    pool = MagicMock()

    consolidation = AsyncMock()
    if consolidation_raises:
        consolidation.run.side_effect = consolidation_raises
    else:
        consolidation.run.return_value = ConsolidationResult(total=5, consolidated=5)

    decay = AsyncMock()
    if decay_raises:
        decay.process.side_effect = decay_raises
    else:
        decay.process.return_value = DecayResult(total=5, decayed=3, archived=1)

    strengthen = AsyncMock()
    if strengthen_raises:
        strengthen.process.side_effect = strengthen_raises
    else:
        strengthen.process.return_value = StrengthenResult(total=4, promoted=2)

    schema = AsyncMock()
    if schema_raises:
        schema.process.side_effect = schema_raises
    else:
        schema.process.return_value = SchemaResult(created=0)

    conn = AsyncMock()
    conn.fetchval.return_value = lock_acquired
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    return pool, consolidation, decay, strengthen, schema, conn


# ---------------------------------------------------------------------------
# NCRReport
# ---------------------------------------------------------------------------


class TestNCRReport:
    def test_defaults(self):
        r = NCRReport(bank_id="b", started_at=datetime.now(timezone.utc))
        assert r.completed_at is None
        assert r.phase1 is None
        assert r.phase2 is None
        assert r.phase3 is None
        assert r.errors == []

    def test_duration_none_if_not_completed(self):
        r = NCRReport(bank_id="b", started_at=datetime.now(timezone.utc))
        assert r.duration_seconds is None

    def test_duration_computed(self):
        from datetime import timedelta

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(seconds=42)
        r = NCRReport(bank_id="b", started_at=start, completed_at=end)
        assert r.duration_seconds == pytest.approx(42.0)

    def test_errors_not_shared(self):
        r1 = NCRReport(bank_id="a", started_at=datetime.now(timezone.utc))
        r2 = NCRReport(bank_id="b", started_at=datetime.now(timezone.utc))
        r1.errors.append("x")
        assert r2.errors == []


# ---------------------------------------------------------------------------
# _ncr_lock_id
# ---------------------------------------------------------------------------


class TestNcrLockId:
    def test_deterministic(self):
        assert _ncr_lock_id("bank-a") == _ncr_lock_id("bank-a")

    def test_different_banks_different_ids(self):
        assert _ncr_lock_id("bank-a") != _ncr_lock_id("bank-b")

    def test_within_postgres_int_range(self):
        lid = _ncr_lock_id("any-bank")
        assert 0 <= lid < 2**31


# ---------------------------------------------------------------------------
# NCROrchestrator
# ---------------------------------------------------------------------------


class TestNCROrchestrator:
    @pytest.mark.asyncio
    async def test_all_phases_run(self):
        pool, consolidation, decay, strengthen, schema, conn = _make_orchestrator()

        with patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry",
            return_value=conn,
        ), patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.dict_repo.filter_entries",
            new_callable=AsyncMock,
            return_value=[],
        ):
            orch = NCROrchestrator(pool, consolidation, decay, strengthen, schema)
            report = await orch.run("test-bank")

        consolidation.run.assert_called_once_with("test-bank")
        decay.process.assert_called_once_with("test-bank")
        strengthen.process.assert_called_once_with("test-bank")
        schema.process.assert_called_once()
        assert report.phase1 is not None
        assert report.phase2 is not None
        assert report.phase3 is not None
        assert report.completed_at is not None
        assert len(report.errors) == 0

    @pytest.mark.asyncio
    async def test_decay_failure_does_not_stop_strengthen(self):
        pool, consolidation, decay, strengthen, schema, conn = _make_orchestrator(
            decay_raises=RuntimeError("Decay down")
        )

        with patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry",
            return_value=conn,
        ), patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.dict_repo.filter_entries",
            new_callable=AsyncMock,
            return_value=[],
        ):
            orch = NCROrchestrator(pool, consolidation, decay, strengthen, schema)
            report = await orch.run("test-bank")

        strengthen.process.assert_called_once()
        schema.process.assert_called_once()
        assert any("Decay" in e for e in report.errors)
        assert report.phase1 is None
        assert report.phase2 is not None

    @pytest.mark.asyncio
    async def test_lock_not_acquired_returns_early(self):
        pool, consolidation, decay, strengthen, schema, conn = _make_orchestrator(lock_acquired=False)

        with patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry",
            return_value=conn,
        ):
            orch = NCROrchestrator(pool, consolidation, decay, strengthen, schema)
            report = await orch.run("test-bank")

        consolidation.run.assert_not_called()
        decay.process.assert_not_called()
        assert len(report.errors) == 1
        assert "advisory lock" in report.errors[0].lower() or "already running" in report.errors[0].lower()

    @pytest.mark.asyncio
    async def test_consolidation_failure_continues_ncr(self):
        pool, consolidation, decay, strengthen, schema, conn = _make_orchestrator(
            consolidation_raises=RuntimeError("Consolidation error")
        )

        with patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry",
            return_value=conn,
        ), patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.dict_repo.filter_entries",
            new_callable=AsyncMock,
            return_value=[],
        ):
            orch = NCROrchestrator(pool, consolidation, decay, strengthen, schema)
            report = await orch.run("test-bank")

        decay.process.assert_called_once()
        assert any("Consolidation" in e for e in report.errors)

    @pytest.mark.asyncio
    async def test_report_has_bank_id_and_timestamps(self):
        pool, consolidation, decay, strengthen, schema, conn = _make_orchestrator()

        with patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry",
            return_value=conn,
        ), patch(
            "hindsight_api.engine.consolidation.ncr_orchestrator.dict_repo.filter_entries",
            new_callable=AsyncMock,
            return_value=[],
        ):
            orch = NCROrchestrator(pool, consolidation, decay, strengthen, schema)
            report = await orch.run("my-bank")

        assert report.bank_id == "my-bank"
        assert report.started_at is not None
        assert report.completed_at is not None
        assert report.duration_seconds is not None
        assert report.duration_seconds >= 0


# ---------------------------------------------------------------------------
# NCRScheduler
# ---------------------------------------------------------------------------


class TestNCRScheduler:
    def test_disabled_scheduler_does_not_start(self):
        orch = MagicMock()
        scheduler = NCRScheduler(orch, bank_ids=["b"], interval_hours=1, enabled=False)
        scheduler.start()
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_scheduler_can_be_stopped(self):
        orch = AsyncMock()
        orch.run.return_value = NCRReport(bank_id="b", started_at=datetime.now(timezone.utc))
        scheduler = NCRScheduler(orch, bank_ids=["b"], interval_hours=100, enabled=True)
        scheduler.start()
        assert scheduler._task is not None
        await scheduler.stop()
        assert scheduler._task.done()

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_started(self):
        orch = MagicMock()
        scheduler = NCRScheduler(orch, bank_ids=[], enabled=False)
        await scheduler.stop()  # should not raise
