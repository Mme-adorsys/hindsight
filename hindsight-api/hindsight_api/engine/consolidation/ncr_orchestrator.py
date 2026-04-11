"""
NCR Orchestrator — Nightly Consolidation Run.

Three independent consolidation phases with separate triggers and schedules:

  C1 (Working → Buffer)     — runs at session end, no cooldown
  C2 (Decay + Strengthen)   — runs periodically (default 24h), 1h cooldown
  C3 (Schema Compression)   — runs periodically (default 168h/7d), 6h cooldown

Each phase can be triggered independently via the ``phases`` parameter.
When ``phases`` is None, all phases run (backward compatibility).

Biological mapping:
  C1 = Sharp-Wave Ripples during quiet wakefulness (post-session replay)
  C2 = SWS slow-wave decay + strengthening (daily memory triage)
  C3 = REM schema compression (weekly structural reorganisation)
  Ref: concept.md ch. 12 — Nightly Consolidation Run (NCR)

Epic 12, Story 05 (original); refactored for phase independence.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.consolidation1 import Consolidation1Service, ConsolidationResult
from hindsight_api.engine.consolidation.multi_bank_promoter import PromotionResult, promote_batch
from hindsight_api.engine.consolidation.ncr_decay import DecayProcessor, DecayResult
from hindsight_api.engine.consolidation.ncr_strengthen import StrengthenProcessor, StrengthenResult
from hindsight_api.engine.consolidation.schema_processor import SchemaProcessor, SchemaResult
from hindsight_api.engine.db_utils import acquire_with_retry
from hindsight_api.engine.tracer import PipelineTracer
from hindsight_api.engine.utils import fq_table

if TYPE_CHECKING:
    from hindsight_api.engine.engram_storage import EngramStorageInterface
    from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# Advisory lock ID for NCR (unique, outside migrations' range)
_NCR_ADVISORY_LOCK_BASE = 987654321

# Valid phase identifiers
VALID_PHASES = {"c1", "c2", "c3", "shared"}


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
        phases_requested: Which phases were requested (None = all).
        consolidation: Result from C1 (Working → Buffer).
        phase1:       Result from C2/Decay.
        phase2:       Result from C2/Strengthen (Buffer → Neocortex).
        phase3:       Result from C3/Schema Compression.
        errors:       Phase-level error messages (phase failures, lock conflicts).
    """

    bank_id: str
    started_at: datetime
    completed_at: datetime | None = None
    phases_requested: list[str] | None = None
    consolidation: ConsolidationResult | None = None
    phase1: DecayResult | None = None
    phase2: StrengthenResult | None = None
    phase3: SchemaResult | None = None
    promotion: PromotionResult | None = None  # Phase 4: Shared Bank Promotion (Epic 14 B5)
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
    Orchestrates NCR phases for a single bank.

    Phases can be run independently or together:
        C1: Working Memory → Buffer (Consolidation 1)
        C2: Decay (Phase 1) + Strengthen (Phase 2, Buffer → Neocortex)
        C3: Schema Compression (Phase 3)
        Shared: Shared Bank Promotion (Phase 4, optional)

    Each phase runs inside its own try/except so a failure in one phase does
    not prevent subsequent phases from running.

    Advisory locking:
        A PostgreSQL advisory lock prevents parallel NCR runs on the same bank.
        If the lock cannot be acquired (another run is active), the method
        returns immediately with an error in the report.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        consolidation: Consolidation1Service,
        decay: DecayProcessor,
        strengthen: StrengthenProcessor,
        schema: SchemaProcessor,
        shared_bank_id: str | None = None,
        agent_bank_ids: list[str] | None = None,
        qdrant_client=None,
        neo4j_client=None,
        llm=None,
    ) -> None:
        self._pool = pool
        self._consolidation = consolidation
        self._decay = decay
        self._strengthen = strengthen
        self._schema = schema
        self._shared_bank_id = shared_bank_id
        self._agent_bank_ids = agent_bank_ids or []
        self._qdrant_client = qdrant_client
        self._neo4j_client = neo4j_client
        self._llm = llm

    async def run(
        self,
        bank_id: str,
        trigger: Literal["manual", "scheduled", "session_end"] = "manual",
        phases: set[str] | None = None,
    ) -> NCRReport:
        """
        Run selected NCR phases for a bank.

        Args:
            bank_id: The memory bank to process.
            trigger: What initiated this run (manual/scheduled/session_end).
            phases: Which phases to run. None = all phases.
                    Valid values: {"c1", "c2", "c3", "shared"}

        Returns:
            NCRReport with results from requested phases.
        """
        report = NCRReport(
            bank_id=bank_id,
            started_at=datetime.now(timezone.utc),
            phases_requested=sorted(phases) if phases else None,
        )
        lock_id = _ncr_lock_id(bank_id)

        tracer = PipelineTracer(pipeline="ncr", bank_id=bank_id)
        tracer.set_metadata("trigger", trigger)
        tracer.set_metadata("phases", sorted(phases) if phases else "all")

        async with acquire_with_retry(self._pool) as conn:
            acquired: bool = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
            if not acquired:
                msg = f"NCR already running for bank={bank_id} (advisory lock held)"
                logger.warning("[NCR] %s", msg)
                report.errors.append(msg)
                report.completed_at = datetime.now(timezone.utc)
                tracer.set_metadata("lock_acquired", False)
                await self._persist_report(report, trigger, trace_data=tracer.finalize().to_dict())
                return report

        tracer.set_metadata("lock_acquired", True)
        phases_label = ",".join(sorted(phases)) if phases else "all"
        logger.info(
            "[NCR] Starting run for bank=%s (lock=%d trigger=%s phases=%s)",
            bank_id,
            lock_id,
            trigger,
            phases_label,
        )
        try:
            await self._run_phases(bank_id, report, phases=phases, tracer=tracer)
        finally:
            async with acquire_with_retry(self._pool) as conn:
                await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
            report.completed_at = datetime.now(timezone.utc)
            logger.info(
                "[NCR] Completed bank=%s phases=%s duration=%.1fs errors=%d",
                bank_id,
                phases_label,
                report.duration_seconds or 0,
                len(report.errors),
            )
            await self._persist_report(report, trigger, trace_data=tracer.finalize().to_dict())

        return report

    async def _persist_report(
        self,
        report: NCRReport,
        trigger: str,
        trace_data: dict | None = None,
    ) -> None:
        """Persist an NCRReport to the ``ncr_runs`` table."""

        def _phase_to_json(phase) -> str | None:
            if phase is None:
                return None
            try:
                return json.dumps(asdict(phase), default=str)
            except Exception as exc:
                logger.warning("[NCR] failed to serialise phase for persistence: %s", exc)
                return None

        try:
            consolidation_json = _phase_to_json(report.consolidation)
            decay_json = _phase_to_json(report.phase1)
            strengthen_json = _phase_to_json(report.phase2)
            schema_json = _phase_to_json(report.phase3)
            promotion_json = _phase_to_json(report.promotion)
            errors_json = json.dumps(report.errors) if report.errors else None
            trace_json = json.dumps(trace_data, default=str) if trace_data else None

            async with acquire_with_retry(self._pool) as conn:
                await conn.execute(
                    f"""
                    INSERT INTO {fq_table("ncr_runs")} (
                        bank_id, trigger, started_at, completed_at, duration_seconds,
                        consolidation_stats, decay_stats, strengthen_stats,
                        schema_stats, promotion_stats, errors, trace_data
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6::jsonb, $7::jsonb, $8::jsonb,
                        $9::jsonb, $10::jsonb, $11::jsonb, $12::jsonb
                    )
                    """,
                    report.bank_id,
                    trigger,
                    report.started_at,
                    report.completed_at,
                    report.duration_seconds,
                    consolidation_json,
                    decay_json,
                    strengthen_json,
                    schema_json,
                    promotion_json,
                    errors_json,
                    trace_json,
                )
        except Exception as exc:
            logger.warning(
                "[NCR] failed to persist report bank=%s trigger=%s: %s",
                report.bank_id,
                trigger,
                exc,
            )

    async def _run_phases(
        self,
        bank_id: str,
        report: NCRReport,
        phases: set[str] | None = None,
        tracer: PipelineTracer | None = None,
    ) -> None:
        """Run requested phases. None = all phases."""
        _tracer = tracer if tracer is not None else PipelineTracer(pipeline="ncr", bank_id=bank_id, enabled=False)
        run_all = phases is None
        _phases = phases or set()  # empty set when None (run_all handles the logic)

        # ── C1: Working Memory → Buffer ────────────────────────────
        if run_all or "c1" in _phases:
            try:
                with _tracer.step("c1_consolidation") as _s:
                    report.consolidation = await self._consolidation.run(bank_id)
                    _s.set_output(asdict(report.consolidation))
                    _s.set_rationale(
                        f"promoted {report.consolidation.consolidated} engrams "
                        f"Working → Buffer (recall-driven + saliency boost)"
                    )
                    logger.info(
                        "[NCR] C1 done: promoted=%d skipped=%d archived=%d",
                        report.consolidation.consolidated,
                        report.consolidation.skipped,
                        report.consolidation.archived,
                    )
            except Exception as exc:
                msg = f"C1/Consolidation failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

        # ── C2: Decay + Strengthen (Buffer → Neocortex) ───────────
        if run_all or "c2" in _phases:
            # Phase 1: Decay
            try:
                with _tracer.step("c2_decay") as _s:
                    report.phase1 = await self._decay.process(bank_id)
                    _s.set_output(asdict(report.phase1))
                    _s.set_rationale(
                        f"{report.phase1.decayed} engrams decayed via strength * decay_rate, "
                        f"{report.phase1.archived} archived below threshold"
                    )
                    logger.info(
                        "[NCR] C2/Decay done: archived=%d decayed=%d",
                        report.phase1.archived,
                        report.phase1.decayed,
                    )
            except Exception as exc:
                msg = f"C2/Decay failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

            # Phase 2: Strengthen
            try:
                with _tracer.step("c2_strengthen") as _s:
                    report.phase2 = await self._strengthen.process(bank_id)
                    _s.set_output(asdict(report.phase2))
                    _s.set_rationale(
                        f"{report.phase2.promoted} engrams met neocortex promotion criteria "
                        f"(strength >= 0.4, access_count >= 3, ncr_cycles_survived >= 2)"
                    )
                    logger.info("[NCR] C2/Strengthen done: promoted=%d", report.phase2.promoted)
            except Exception as exc:
                msg = f"C2/Strengthen failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

        # ── C3: Schema Compression ────────────────────────────────
        if run_all or "c3" in _phases:
            try:
                with _tracer.step("c3_schema") as _s:
                    neocortex_entries = await dict_repo.filter_entries(
                        self._pool, bank_id, layer="neocortex", status="active", limit=10000
                    )
                    _s.set_input({"neocortex_count": len(neocortex_entries)})
                    report.phase3 = await self._schema.process(bank_id, engrams=neocortex_entries)  # type: ignore[arg-type]
                    _s.set_output(asdict(report.phase3))
                    _s.set_rationale(
                        f"Game-of-Life schema rules: {report.phase3.created} created, "
                        f"{getattr(report.phase3, 'strengthened', 0)} strengthened, "
                        f"{getattr(report.phase3, 'deleted', 0)} deleted"
                    )
                    logger.info(
                        "[NCR] C3/Schema done: neocortex_count=%d created=%d",
                        len(neocortex_entries),
                        report.phase3.created,
                    )
            except Exception as exc:
                msg = f"C3/Schema failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

        # ── Shared Bank Promotion (optional) ──────────────────────
        if (run_all or "shared" in _phases) and self._shared_bank_id and self._qdrant_client:
            try:
                with _tracer.step("shared_promotion") as _s:
                    _s.set_input({"shared_bank_id": self._shared_bank_id})
                    report.promotion = await promote_batch(
                        pool=self._pool,
                        qdrant_client=self._qdrant_client,
                        neo4j_client=self._neo4j_client,
                        bank_id=bank_id,
                        shared_bank_id=self._shared_bank_id,
                        agent_bank_ids=self._agent_bank_ids,
                        llm=self._llm,
                    )
                    _s.set_output(asdict(report.promotion))
                    _s.set_rationale(
                        f"{report.promotion.promoted} engrams met shared-bank promotion criteria, "
                        f"{report.promotion.reinforced} reinforced existing shared engrams"
                    )
                    logger.info(
                        "[NCR] Shared/Promotion done: promoted=%d reinforced=%d",
                        report.promotion.promoted,
                        report.promotion.reinforced,
                    )
            except Exception as exc:
                msg = f"Shared/Promotion failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)
        elif not (run_all or "shared" in _phases):
            pass  # phase not requested
        else:
            _tracer.record_step(
                name="shared_promotion",
                duration_ms=0.0,
                status="skipped",
                rationale="no shared_bank_id or qdrant_client configured — Shared phase disabled",
            )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class NCRScheduler:
    """
    Periodic background scheduler for C2 and C3 phases.

    C1 does not need a scheduler — it is triggered at session end.
    C2 (Decay + Strengthen) runs at ``c2_interval_hours`` (default 24h).
    C3 (Schema Compression) runs at ``c3_interval_hours`` (default 168h / 7 days).

    Args:
        orchestrator:      NCROrchestrator to call each cycle.
        bank_ids:          List of bank IDs to process on each cycle.
        c2_interval_hours: C2 cycle interval in hours (default 24).
        c3_interval_hours: C3 cycle interval in hours (default 168 = 7 days).
        enabled:           If False, the scheduler loop exits immediately.
    """

    def __init__(
        self,
        orchestrator: NCROrchestrator,
        bank_ids: list[str],
        c2_interval_hours: float = 24.0,
        c3_interval_hours: float = 168.0,
        enabled: bool = True,
    ) -> None:
        self._orchestrator = orchestrator
        self._bank_ids = bank_ids
        self._c2_interval = c2_interval_hours * 3600
        self._c3_interval = c3_interval_hours * 3600
        self._enabled = enabled
        self._c2_task: asyncio.Task | None = None
        self._c3_task: asyncio.Task | None = None

    def start(self) -> None:
        """Launch C2 and C3 scheduler loops as asyncio background tasks."""
        if not self._enabled:
            logger.info("[NCRScheduler] Disabled — not starting")
            return
        self._c2_task = asyncio.create_task(
            self._phase_loop("c2", self._c2_interval, {"c2"}),
            name="ncr-scheduler-c2",
        )
        self._c3_task = asyncio.create_task(
            self._phase_loop("c3", self._c3_interval, {"c3"}),
            name="ncr-scheduler-c3",
        )
        logger.info(
            "[NCRScheduler] Started — C2 every %.1fh, C3 every %.1fh, banks=%s",
            self._c2_interval / 3600,
            self._c3_interval / 3600,
            self._bank_ids,
        )

    async def stop(self) -> None:
        """Cancel all scheduler tasks gracefully."""
        for task in [self._c2_task, self._c3_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("[NCRScheduler] Stopped")

    async def _phase_loop(self, phase_name: str, interval: float, phases: set[str]) -> None:
        """Run a single phase on all banks at the given interval."""
        while True:
            await asyncio.sleep(interval)
            for bank_id in self._bank_ids:
                try:
                    report = await self._orchestrator.run(bank_id, trigger="scheduled", phases=phases)
                    logger.info(
                        "[NCRScheduler] %s cycle complete bank=%s duration=%.1fs",
                        phase_name.upper(),
                        bank_id,
                        report.duration_seconds or 0,
                    )
                except Exception as exc:
                    logger.error("[NCRScheduler] %s cycle error bank=%s: %s", phase_name.upper(), bank_id, exc)
