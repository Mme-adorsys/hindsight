"""
NCR Orchestrator — Nightly Consolidation Run.

Runs all three NCR phases sequentially:
  Phase 1 (Decay)   → Phase 2 (Strengthen) → Phase 3 (Schema)

Each phase is fault-tolerant: a failure is logged and the next phase continues.
A PostgreSQL advisory lock prevents parallel NCR runs on the same bank.

Biological mapping:
  Full SWS + REM sleep cycle — slow-wave decay/strengthen followed by REM
  schema compression. Each NCR cycle is one "night" of memory consolidation.
  Ref: concept.md ch. 12 — Nightly Consolidation Run (NCR)

Epic 12, Story 05.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.consolidation1 import Consolidation1Service, ConsolidationResult
from hindsight_api.engine.consolidation.ncr_decay import DecayProcessor, DecayResult
from hindsight_api.engine.consolidation.ncr_strengthen import StrengthenProcessor, StrengthenResult
from hindsight_api.engine.consolidation.schema_processor import SchemaProcessor, SchemaResult
from hindsight_api.engine.db_utils import acquire_with_retry

if TYPE_CHECKING:
    from hindsight_api.engine.engram_storage import EngramStorageInterface
    from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# Advisory lock ID for NCR (unique, outside migrations' range)
_NCR_ADVISORY_LOCK_BASE = 987654321


def _ncr_lock_id(bank_id: str) -> int:
    """Deterministic per-bank advisory lock ID."""
    import hashlib

    h = hashlib.sha256(bank_id.encode()).digest()[:8]
    return (_NCR_ADVISORY_LOCK_BASE + int.from_bytes(h, "big")) % (2**31)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class NCRReport:
    """Full report of a single NCR run across all phases.

    Attributes:
        bank_id:      The memory bank that was processed.
        started_at:   UTC timestamp when the run began.
        completed_at: UTC timestamp when the run finished (None if still running).
        consolidation: Result from Consolidation 1 (Session → Buffer).
        phase1:       Result from NCR Phase 1 (Decay).
        phase2:       Result from NCR Phase 2 (Strengthen).
        phase3:       Result from NCR Phase 3 (Schema Compression).
        errors:       Phase-level error messages (phase failures, lock conflicts).
    """

    bank_id: str
    started_at: datetime
    completed_at: datetime | None = None
    consolidation: ConsolidationResult | None = None
    phase1: DecayResult | None = None
    phase2: StrengthenResult | None = None
    phase3: SchemaResult | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class NCROrchestrator:
    """
    Orchestrates all NCR phases for a single bank.

    Sequence:
        Consolidation 1 → Decay (Phase 1) → Strengthen (Phase 2) → Schema (Phase 3)

    Each phase runs inside its own try/except so a failure in one phase does
    not prevent subsequent phases from running.

    Advisory locking:
        A PostgreSQL advisory lock prevents parallel NCR runs on the same bank.
        If the lock cannot be acquired (another run is active), the method
        returns immediately with an error in the report.

    Args:
        pool:         asyncpg connection pool.
        consolidation: Consolidation1Service instance.
        decay:        DecayProcessor instance.
        strengthen:   StrengthenProcessor instance.
        schema:       SchemaProcessor implementation (NoOp until Epic 13).
        qdrant:       QdrantEngineClient — passed through to phase processors.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        consolidation: Consolidation1Service,
        decay: DecayProcessor,
        strengthen: StrengthenProcessor,
        schema: SchemaProcessor,
    ) -> None:
        self._pool = pool
        self._consolidation = consolidation
        self._decay = decay
        self._strengthen = strengthen
        self._schema = schema

    async def run(self, bank_id: str) -> NCRReport:
        """
        Run the full NCR pipeline for a bank.

        Acquires an advisory lock, runs all phases, releases lock.
        Each phase result is recorded in the returned NCRReport.

        Args:
            bank_id: The memory bank to process.

        Returns:
            NCRReport with results from all phases.
        """
        report = NCRReport(bank_id=bank_id, started_at=datetime.now(timezone.utc))
        lock_id = _ncr_lock_id(bank_id)

        async with acquire_with_retry(self._pool) as conn:
            # Try to acquire advisory lock (non-blocking)
            acquired: bool = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
            if not acquired:
                msg = f"NCR already running for bank={bank_id} (advisory lock held)"
                logger.warning("[NCR] %s", msg)
                report.errors.append(msg)
                report.completed_at = datetime.now(timezone.utc)
                return report

        logger.info("[NCR] Starting run for bank=%s (lock=%d)", bank_id, lock_id)
        try:
            await self._run_phases(bank_id, report)
        finally:
            async with acquire_with_retry(self._pool) as conn:
                await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
            report.completed_at = datetime.now(timezone.utc)
            logger.info(
                "[NCR] Completed bank=%s duration=%.1fs errors=%d",
                bank_id,
                report.duration_seconds or 0,
                len(report.errors),
            )

        return report

    async def _run_phases(self, bank_id: str, report: NCRReport) -> None:
        # Consolidation 1: Session → Buffer
        try:
            report.consolidation = await self._consolidation.run(bank_id)
            logger.info("[NCR] Consolidation1 done: consolidated=%d", report.consolidation.consolidated)
        except Exception as exc:
            msg = f"Consolidation1 failed: {exc}"
            logger.error("[NCR] %s", msg)
            report.errors.append(msg)

        # Phase 1: Decay
        try:
            report.phase1 = await self._decay.process(bank_id)
            logger.info(
                "[NCR] Phase1/Decay done: archived=%d decayed=%d", report.phase1.archived, report.phase1.decayed
            )
        except Exception as exc:
            msg = f"Phase1/Decay failed: {exc}"
            logger.error("[NCR] %s", msg)
            report.errors.append(msg)

        # Phase 2: Strengthen
        try:
            report.phase2 = await self._strengthen.process(bank_id)
            logger.info("[NCR] Phase2/Strengthen done: promoted=%d", report.phase2.promoted)
        except Exception as exc:
            msg = f"Phase2/Strengthen failed: {exc}"
            logger.error("[NCR] %s", msg)
            report.errors.append(msg)

        # Phase 3: Schema Compression (fetch neocortex Engrams, pass to processor)
        try:
            neocortex_entries = await dict_repo.filter_entries(
                self._pool, bank_id, layer="neocortex", status="active", limit=10000
            )
            # Phase 3 receives FullEngram list; pass lightweight dicts for now
            # Epic 13 will enrich with full Engram objects
            report.phase3 = await self._schema.process(bank_id, engrams=[])  # type: ignore[arg-type]
            logger.info(
                "[NCR] Phase3/Schema done: neocortex_count=%d created=%d",
                len(neocortex_entries),
                report.phase3.created,
            )
        except Exception as exc:
            msg = f"Phase3/Schema failed: {exc}"
            logger.error("[NCR] %s", msg)
            report.errors.append(msg)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class NCRScheduler:
    """
    Periodic background scheduler that runs the NCR at a configurable interval.

    Designed to run as an asyncio background task during application lifespan.
    Graceful shutdown is handled by cancelling the task.

    Args:
        orchestrator:    NCROrchestrator to call each cycle.
        bank_ids:        List of bank IDs to process on each cycle.
        interval_hours:  Cycle interval in hours (default 24).
        enabled:         If False, the scheduler loop exits immediately.
    """

    def __init__(
        self,
        orchestrator: NCROrchestrator,
        bank_ids: list[str],
        interval_hours: float = 24.0,
        enabled: bool = True,
    ) -> None:
        self._orchestrator = orchestrator
        self._bank_ids = bank_ids
        self._interval_seconds = interval_hours * 3600
        self._enabled = enabled
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Launch the scheduler as an asyncio background task."""
        if not self._enabled:
            logger.info("[NCRScheduler] Disabled — not starting")
            return
        self._task = asyncio.create_task(self._loop(), name="ncr-scheduler")
        logger.info(
            "[NCRScheduler] Started — interval=%.1fh banks=%s",
            self._interval_seconds / 3600,
            self._bank_ids,
        )

    async def stop(self) -> None:
        """Cancel the scheduler task gracefully."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[NCRScheduler] Stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            for bank_id in self._bank_ids:
                try:
                    report = await self._orchestrator.run(bank_id)
                    logger.info(
                        "[NCRScheduler] Cycle complete bank=%s duration=%.1fs",
                        bank_id,
                        report.duration_seconds or 0,
                    )
                except Exception as exc:
                    logger.error("[NCRScheduler] Cycle error bank=%s: %s", bank_id, exc)
