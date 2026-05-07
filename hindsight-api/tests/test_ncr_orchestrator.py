"""Unit tests for the NCR orchestrator + scheduler (Epic 25 Story 18 shape).

The legacy 5-phase tests (DecayProcessor / StrengthenProcessor /
SchemaProcessor mocks) were removed when Stories 09–14 + 18 retired those
classes. These tests now mock the function-level composers
``run_c2_phase`` / ``run_c3_phase`` and verify the orchestrator's wiring,
phase routing, advisory-lock behaviour, and persistence call.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.c2_decay import DecayReport
from hindsight_api.engine.consolidation.c3_schema_restructure import R3Report, R5Report
from hindsight_api.engine.consolidation.consolidation1 import ConsolidationResult
from hindsight_api.engine.consolidation.ncr_orchestrator import (
    VALID_PHASES,
    C2Report,
    C3Report,
    NCROrchestrator,
    NCRReport,
    NCRScheduler,
    _ncr_lock_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wired_pool(lock_acquired: bool = True):
    """Return (pool, _ctx, conn) where the conn fakes the advisory-lock dance."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=lock_acquired)
    conn.execute = AsyncMock()
    pool = MagicMock()

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    return pool, _ctx, conn


def _make_orchestrator(
    pool,
    *,
    consolidation_raises: Exception | None = None,
    qdrant=None,
    neo4j=None,
):
    consolidation = AsyncMock()
    if consolidation_raises:
        consolidation.run.side_effect = consolidation_raises
    else:
        consolidation.run.return_value = ConsolidationResult(total=5, consolidated=5)

    return NCROrchestrator(
        pool=pool,
        consolidation=consolidation,
        qdrant_client=qdrant or MagicMock(),
        neo4j_client=neo4j or MagicMock(),
        description_llm_caller=None,
        shared_bank_id=None,
    )


# ---------------------------------------------------------------------------
# Lock id determinism
# ---------------------------------------------------------------------------


class TestLockId:
    def test_deterministic_per_bank(self):
        assert _ncr_lock_id("bank-a") == _ncr_lock_id("bank-a")
        assert _ncr_lock_id("bank-a") != _ncr_lock_id("bank-b")

    def test_within_signed_int32_range(self):
        for bank in ("bank-a", "bank-b", "x" * 100):
            lock = _ncr_lock_id(bank)
            assert 0 <= lock < 2**31


# ---------------------------------------------------------------------------
# Composite report dataclasses
# ---------------------------------------------------------------------------


class TestReports:
    def test_c2_report_defaults(self):
        r = C2Report(bank_id="b1")
        assert r.candidates_detected == 0
        assert r.matured == 0
        assert r.reinforced == 0
        assert r.created == 0
        assert r.decay is None

    def test_c3_report_defaults(self):
        r = C3Report(bank_id="b1")
        assert r.r3 is None
        assert r.r5 is None

    def test_ncr_report_duration(self):
        from datetime import datetime, timezone

        r = NCRReport(bank_id="b1", started_at=datetime.now(timezone.utc))
        assert r.duration_seconds is None
        r.completed_at = r.started_at  # same instant → 0
        assert r.duration_seconds == 0

    def test_valid_phases_is_4(self):
        assert VALID_PHASES == {"c1", "c2", "c3", "shared"}


# ---------------------------------------------------------------------------
# Orchestrator — phase routing
# ---------------------------------------------------------------------------


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_unknown_phase_raises_value_error(self):
        pool, _ctx, _conn = _wired_pool()
        orch = _make_orchestrator(pool)
        with pytest.raises(ValueError, match="Unknown phases"):
            await orch.run("bank-a", phases={"c2", "schemastuff"})

    @pytest.mark.asyncio
    async def test_lock_busy_returns_early(self):
        pool, _ctx, _conn = _wired_pool(lock_acquired=False)
        orch = _make_orchestrator(pool)
        with patch("hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry", new=_ctx):
            report = await orch.run("bank-a")
        assert report.errors  # one entry: "lock held"
        assert "lock" in report.errors[0].lower()
        assert report.consolidation is None
        assert report.c2 is None
        assert report.c3 is None

    @pytest.mark.asyncio
    async def test_run_all_phases_calls_all_composers(self):
        pool, _ctx, _conn = _wired_pool()
        orch = _make_orchestrator(pool)

        c2_report = C2Report(
            bank_id="bank-a",
            candidates_detected=3,
            matured=2,
            reinforced=1,
            created=1,
            decay=DecayReport(bank_id="bank-a", total=10, archived=2, retained=8),
        )
        c3_report = C3Report(
            bank_id="bank-a",
            r3=R3Report(bank_id="bank-a", schemas_scanned=5, hyper_schemas_created=1),
            r5=R5Report(bank_id="bank-a", schemas_scanned=5),
        )

        with (
            patch("hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry", new=_ctx),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c2_phase",
                new=AsyncMock(return_value=c2_report),
            ) as c2_mock,
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c3_phase",
                new=AsyncMock(return_value=c3_report),
            ) as c3_mock,
        ):
            report = await orch.run("bank-a")

        assert report.consolidation is not None
        assert report.consolidation.consolidated == 5
        assert report.c2 is c2_report
        assert report.c3 is c3_report
        c2_mock.assert_awaited_once()
        c3_mock.assert_awaited_once()
        # Composite distribution surfaces the decay archive count at the top.
        assert report.composite_distribution == {"archived": 2, "retained": 8}

    @pytest.mark.asyncio
    async def test_phase_subset_skips_others(self):
        pool, _ctx, _conn = _wired_pool()
        orch = _make_orchestrator(pool)

        with (
            patch("hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry", new=_ctx),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c2_phase",
                new=AsyncMock(return_value=C2Report(bank_id="bank-a")),
            ) as c2_mock,
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c3_phase",
                new=AsyncMock(return_value=C3Report(bank_id="bank-a")),
            ) as c3_mock,
        ):
            report = await orch.run("bank-a", phases={"c2"})

        # only C2 ran; C1 and C3 stayed None
        assert report.consolidation is None
        assert report.c2 is not None
        assert report.c3 is None
        c2_mock.assert_awaited_once()
        c3_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_c1_failure_does_not_block_c2_c3(self):
        pool, _ctx, _conn = _wired_pool()
        orch = _make_orchestrator(pool, consolidation_raises=RuntimeError("boom"))

        with (
            patch("hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry", new=_ctx),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c2_phase",
                new=AsyncMock(return_value=C2Report(bank_id="bank-a")),
            ),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c3_phase",
                new=AsyncMock(return_value=C3Report(bank_id="bank-a")),
            ),
        ):
            report = await orch.run("bank-a")

        assert report.consolidation is None
        assert any("C1" in e for e in report.errors)
        assert report.c2 is not None
        assert report.c3 is not None

    @pytest.mark.asyncio
    async def test_persistence_writes_decay_and_schema_columns(self):
        pool, _ctx, conn = _wired_pool()
        orch = _make_orchestrator(pool)

        with (
            patch("hindsight_api.engine.consolidation.ncr_orchestrator.acquire_with_retry", new=_ctx),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c2_phase",
                new=AsyncMock(return_value=C2Report(bank_id="bank-a")),
            ),
            patch(
                "hindsight_api.engine.consolidation.ncr_orchestrator.run_c3_phase",
                new=AsyncMock(return_value=C3Report(bank_id="bank-a")),
            ),
        ):
            await orch.run("bank-a")

        # _persist_report calls conn.execute with the INSERT — confirm the
        # legacy strengthen_stats column gets NULL (Story 18 invariant).
        insert_calls = [
            call for call in conn.execute.call_args_list if "INSERT INTO" in (call.args[0] if call.args else "")
        ]
        assert insert_calls, "expected a persist INSERT"
        args = insert_calls[0].args
        # args[0] = SQL, args[1..] = parameters in order; strengthen_stats is param 8
        # SQL order: bank_id, trigger, started_at, completed_at, duration_seconds,
        # consolidation_stats(6), decay_stats(7), strengthen_stats(8), schema_stats(9), promotion_stats(10), errors(11), trace(12)
        assert args[8] is None  # strengthen_stats permanently NULL


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestScheduler:
    @pytest.mark.asyncio
    async def test_disabled_does_not_start_tasks(self):
        sched = NCRScheduler(orchestrator=MagicMock(), bank_ids=[], enabled=False)
        sched.start()
        assert sched._c2_task is None
        assert sched._c3_task is None

    @pytest.mark.asyncio
    async def test_enabled_creates_two_loop_tasks(self):
        sched = NCRScheduler(orchestrator=MagicMock(), bank_ids=[], enabled=True)
        sched.start()
        try:
            assert sched._c2_task is not None
            assert sched._c3_task is not None
            assert sched._c2_task.get_name() == "ncr-scheduler-c2"
            assert sched._c3_task.get_name() == "ncr-scheduler-c3"
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        sched = NCRScheduler(orchestrator=MagicMock(), bank_ids=[], enabled=True)
        sched.start()
        await sched.stop()
        assert sched._c2_task.done()
        assert sched._c3_task.done()
        # Quick sanity: cancellation propagated cleanly.
        with pytest.raises(asyncio.CancelledError):
            sched._c2_task.result()
