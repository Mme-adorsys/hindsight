"""NCR Orchestrator — Nightly Consolidation Run (Epic 25 3-phase shape).

Three independent consolidation phases with separate triggers and schedules:

  C1 (Working → Buffer)            — runs at session end, no cooldown
  C2 (Pattern Recognition + Decay) — runs periodically (default 24h), 1h cooldown
  C3 (Schema Restructure R3 + R5)  — runs periodically (default 168h/7d), 6h cooldown

Each phase can be triggered independently via the ``phases`` parameter.
When ``phases`` is None, all phases run.

Biological mapping:
  C1 = Sharp-Wave Ripples during quiet wakefulness (post-session replay)
  C2 = SWS slow-wave decay + cluster→schema pattern recognition (R1–R4)
  C3 = REM-like schema restructuring (R3 hyper-schema + R5 schema death)
  Ref: concept.md §12 (NCR) + §13 (R1–R5)

Epic 25 Story 18 retired the legacy 5-phase shape (C1, C2a Decay,
C2b Strengthen, C3 SchemaCompression, Shared) along with its
``DecayProcessor`` / ``StrengthenProcessor`` / ``SchemaProcessor`` classes.
Engrams no longer live in a ``neocortex`` layer (Story 02), so the buffer→
neocortex promotion phase no longer exists. Schema-emergence is now a
function-composition pipeline in :mod:`c2_pattern_recognition` /
:mod:`c2_schema_writer` / :mod:`c2_decay`; schema-restructuring is in
:mod:`c3_schema_restructure`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import asyncpg

from hindsight_api.engine.consolidation.c2_decay import DecayReport, decay_reevaluate_buffer
from hindsight_api.engine.consolidation.c2_pattern_recognition import (
    attach_descriptions,
    detect_clusters,
    filter_matured,
    mature_clusters,
    partition_for_consolidation,
    prepare_creation_payloads,
)
from hindsight_api.engine.consolidation.c2_schema_writer import (
    persist_creation_payloads,
    reinforce_matched,
)
from hindsight_api.engine.consolidation.c3_schema_restructure import (
    R3Report,
    R5Report,
    archive_dead_schemas,
    run_r3_hyper_schema,
)
from hindsight_api.engine.consolidation.consolidation1 import Consolidation1Service, ConsolidationResult
from hindsight_api.engine.db_utils import acquire_with_retry
from hindsight_api.engine.multi_bank.schema_promoter import SchemaPromotionResult, promote_schemas_batch
from hindsight_api.engine.schema.schema_repository import get_schema as get_schema_node
from hindsight_api.engine.tracer import PipelineTracer
from hindsight_api.engine.utils import fq_table

if TYPE_CHECKING:
    from hindsight_api.engine.consolidation.schema_description import DescriptionLLMCaller
    from hindsight_api.engine.neo4j_client import Neo4jEngineClient
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
# C2 phase report — bundles the pipeline + decay sub-reports
# ---------------------------------------------------------------------------


@dataclass
class C2Report:
    """Composite report for a C2 run (R1 detection → R4 reinforce/create + decay)."""

    bank_id: str
    candidates_detected: int = 0
    matured: int = 0
    reinforced: int = 0
    created: int = 0
    decay: DecayReport | None = None


@dataclass
class C3Report:
    """Composite report for a C3 run (R3 hyper-schema + R5 schema death)."""

    bank_id: str
    r3: R3Report | None = None
    r5: R5Report | None = None


# ---------------------------------------------------------------------------
# NCR Report
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
        c2:           Composite C2 report (pattern-recognition + decay).
        c3:           Composite C3 report (R3 hyper-schema + R5 schema death).
        promotion:    Optional shared-bank promotion result (Epic 14 B5).
        errors:       Phase-level error messages (phase failures, lock conflicts).
    """

    bank_id: str
    started_at: datetime
    completed_at: datetime | None = None
    phases_requested: list[str] | None = None
    consolidation: ConsolidationResult | None = None
    c2: C2Report | None = None
    c3: C3Report | None = None
    promotion: SchemaPromotionResult | None = None
    errors: list[str] = field(default_factory=list)
    # Story 18 dropped buffer→neocortex promotion. The reactivate/downgrade
    # counters from Epic 24 stay surfaced here for back-compat with the
    # NCR-runs dashboard but are now sourced from the decay sub-report.
    reactivated_count: int = 0
    downgraded_count: int = 0
    composite_distribution: dict[str, int] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# C2 + C3 phase runners — pure async functions, no class state
# ---------------------------------------------------------------------------


async def run_c2_phase(
    bank_id: str,
    *,
    pool: asyncpg.Pool,
    qdrant: "QdrantEngineClient",
    neo4j: "Neo4jEngineClient",
    llm_caller: "DescriptionLLMCaller | None" = None,
) -> C2Report:
    """End-to-end C2: R1 detect → R2 mature → R4 reinforce/create + decay re-eval.

    Best-effort throughout — sub-step failures are logged via the underlying
    helpers and surface as zero counts in the report, never as raised
    exceptions. Decay re-eval runs unconditionally even if pattern
    recognition produced nothing — buffer engrams should still be re-scored.
    """
    report = C2Report(bank_id=bank_id)

    # --- R1: HDBSCAN cluster detection ---
    try:
        candidates = await detect_clusters(bank_id, pool=pool, qdrant=qdrant)
        report.candidates_detected = len(candidates)
    except Exception as exc:
        logger.warning("[NCR/C2] detect_clusters failed bank=%s: %s", bank_id, exc)
        candidates = []

    # --- R2: Maturation — fingerprint store cycles ≥ 2 ---
    if candidates:
        try:
            matured_all = await mature_clusters(candidates, pool=pool, bank_id=bank_id)
            matured = filter_matured(matured_all)
            report.matured = len(matured)
        except Exception as exc:
            logger.warning("[NCR/C2] mature_clusters failed bank=%s: %s", bank_id, exc)
            matured = []
    else:
        matured = []

    # --- Schema lookup is injected as an awaitable so we keep partition
    # decoupled from the Neo4j client (Stories 06/09 pattern). ---
    async def _schema_lookup(schema_id, label="Schema"):
        return await get_schema_node(neo4j, schema_id, label=label)

    # --- R4 split: reinforce existing schemas, create new ones for the rest ---
    if matured:
        try:
            plan = await partition_for_consolidation(
                matured,
                bank_id=bank_id,
                qdrant=qdrant,
                schema_lookup=_schema_lookup,
            )
        except Exception as exc:
            logger.warning("[NCR/C2] partition_for_consolidation failed bank=%s: %s", bank_id, exc)
            plan = None

        if plan is not None:
            if plan.reinforcement:
                try:
                    reinforced = await reinforce_matched(
                        plan.reinforcement, bank_id=bank_id, neo4j=neo4j, qdrant=qdrant, pool=pool
                    )
                    report.reinforced = len(reinforced)
                except Exception as exc:
                    logger.warning("[NCR/C2] reinforce_matched failed bank=%s: %s", bank_id, exc)

            if plan.creation:
                try:
                    payloads = prepare_creation_payloads(plan.creation)
                    payloads = await attach_descriptions(payloads, llm_caller=llm_caller)
                    created = await persist_creation_payloads(
                        payloads, bank_id=bank_id, neo4j=neo4j, qdrant=qdrant, pool=pool
                    )
                    report.created = len(created)
                except Exception as exc:
                    logger.warning("[NCR/C2] schema-creation pipeline failed bank=%s: %s", bank_id, exc)

    # --- Buffer-engram decay re-evaluation (always runs) ---
    try:
        report.decay = await decay_reevaluate_buffer(bank_id, pool=pool)
    except Exception as exc:
        logger.warning("[NCR/C2] decay_reevaluate_buffer failed bank=%s: %s", bank_id, exc)

    logger.info(
        "[NCR/C2] bank=%s candidates=%d matured=%d reinforced=%d created=%d decay=%s",
        bank_id,
        report.candidates_detected,
        report.matured,
        report.reinforced,
        report.created,
        f"archived={report.decay.archived}" if report.decay else "skipped",
    )
    return report


async def run_c3_phase(
    bank_id: str,
    *,
    qdrant: "QdrantEngineClient",
    neo4j: "Neo4jEngineClient",
) -> C3Report:
    """End-to-end C3: R3 hyper-schema bildung + R5 schema death.

    Sub-step failures are logged but don't abort each other — R5 still runs
    if R3 raises and vice versa.
    """
    report = C3Report(bank_id=bank_id)
    try:
        report.r3 = await run_r3_hyper_schema(bank_id, neo4j=neo4j, qdrant=qdrant)
    except Exception as exc:
        logger.warning("[NCR/C3] run_r3_hyper_schema failed bank=%s: %s", bank_id, exc)
    try:
        report.r5 = await archive_dead_schemas(bank_id, neo4j=neo4j)
    except Exception as exc:
        logger.warning("[NCR/C3] archive_dead_schemas failed bank=%s: %s", bank_id, exc)
    return report


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class NCROrchestrator:
    """Runs C1/C2/C3 (+ optional shared promotion) for one bank with advisory locking."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        consolidation: Consolidation1Service,
        qdrant_client: "QdrantEngineClient",
        neo4j_client: "Neo4jEngineClient",
        *,
        description_llm_caller: "DescriptionLLMCaller | None" = None,
        shared_bank_id: str | None = None,
        agent_bank_ids: list[str] | None = None,
        promotion_llm=None,
    ) -> None:
        self._pool = pool
        self._consolidation = consolidation
        self._qdrant_client = qdrant_client
        self._neo4j_client = neo4j_client
        self._description_llm_caller = description_llm_caller
        self._shared_bank_id = shared_bank_id
        self._agent_bank_ids = agent_bank_ids or []
        self._promotion_llm = promotion_llm

    async def run(
        self,
        bank_id: str,
        trigger: Literal["manual", "scheduled", "session_end"] = "manual",
        phases: set[str] | None = None,
    ) -> NCRReport:
        """Run selected phases for ``bank_id``. ``phases=None`` means all."""
        if phases is not None:
            unknown = phases - VALID_PHASES
            if unknown:
                raise ValueError(f"Unknown phases: {sorted(unknown)}; valid={sorted(VALID_PHASES)}")

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
        _phases = phases or set()

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

        # ── C2: Pattern Recognition + Decay Re-Evaluation ─────────
        if run_all or "c2" in _phases:
            try:
                with _tracer.step("c2_pattern_decay") as _s:
                    report.c2 = await run_c2_phase(
                        bank_id,
                        pool=self._pool,
                        qdrant=self._qdrant_client,
                        neo4j=self._neo4j_client,
                        llm_caller=self._description_llm_caller,
                    )
                    _s.set_output(asdict(report.c2))
                    _s.set_rationale(
                        f"R1/2/4 detected={report.c2.candidates_detected} matured={report.c2.matured} "
                        f"reinforced={report.c2.reinforced} created={report.c2.created}; "
                        f"decay archived={report.c2.decay.archived if report.c2.decay else 0}"
                    )
                    if report.c2.decay is not None:
                        # Surface decay archive count at the top level — the
                        # NCR-runs dashboard reads this without reaching
                        # into the C2 sub-report.
                        report.composite_distribution = {
                            "archived": report.c2.decay.archived,
                            "retained": report.c2.decay.retained,
                        }
            except Exception as exc:
                msg = f"C2 failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

        # ── C3: Schema Restructure (R3 hyper-schema + R5 schema death) ──
        if run_all or "c3" in _phases:
            try:
                with _tracer.step("c3_schema_restructure") as _s:
                    report.c3 = await run_c3_phase(
                        bank_id,
                        qdrant=self._qdrant_client,
                        neo4j=self._neo4j_client,
                    )
                    _s.set_output(asdict(report.c3))
                    _s.set_rationale(
                        f"R3 hyper-schemas={report.c3.r3.hyper_schemas_created if report.c3.r3 else 0}; "
                        f"R5 archived={len(report.c3.r5.archived_ids) if report.c3.r5 else 0}"
                    )
            except Exception as exc:
                msg = f"C3 failed: {exc}"
                logger.error("[NCR] %s", msg)
                report.errors.append(msg)

        # ── Shared Bank Promotion (Story 26) ──────────────────────
        # Schema-only path now: legacy engram-based promotion was retired
        # in Story 26 once Story 02 made `layer='neocortex'` impossible.
        if (run_all or "shared" in _phases) and self._shared_bank_id and self._qdrant_client:
            try:
                with _tracer.step("shared_promotion") as _s:
                    _s.set_input({"shared_bank_id": self._shared_bank_id})
                    report.promotion = await promote_schemas_batch(
                        source_bank_id=bank_id,
                        shared_bank_id=self._shared_bank_id,
                        neo4j=self._neo4j_client,
                        qdrant=self._qdrant_client,
                    )
                    _s.set_output(asdict(report.promotion))
                    _s.set_rationale(
                        f"{report.promotion.promoted} schemas promoted, "
                        f"{report.promotion.reinforced} reinforced existing shared schemas, "
                        f"{report.promotion.disputed} disputed forks"
                    )
                    logger.info(
                        "[NCR] Shared/Promotion done: promoted=%d reinforced=%d disputed=%d",
                        report.promotion.promoted,
                        report.promotion.reinforced,
                        report.promotion.disputed,
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

    async def _persist_report(
        self,
        report: NCRReport,
        trigger: str,
        trace_data: dict | None = None,
    ) -> None:
        """Persist an NCRReport to the ``ncr_runs`` table.

        Schema is unchanged from Epic 12 — we map the new C2/C3 composite
        reports onto the legacy column names: ``decay_stats`` carries the
        full C2 bundle (pattern + decay), ``schema_stats`` carries the full
        C3 bundle (R3 + R5). ``strengthen_stats`` is permanently NULL —
        buffer→neocortex promotion no longer exists (Epic 25 Story 02).
        """

        def _to_json(payload: Any) -> str | None:
            if payload is None:
                return None
            try:
                return json.dumps(asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload, default=str)
            except Exception as exc:
                logger.warning("[NCR] failed to serialise payload for persistence: %s", exc)
                return None

        try:
            consolidation_json = _to_json(report.consolidation)
            decay_json = _to_json(report.c2)
            strengthen_json = None  # legacy column kept; no Strengthen phase
            schema_json = _to_json(report.c3)
            promotion_json = _to_json(report.promotion)
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


# ---------------------------------------------------------------------------
# Scheduler — unchanged from Epic 12 (3-phase shape happy path)
# ---------------------------------------------------------------------------


class NCRScheduler:
    """Periodic background scheduler for C2 and C3 phases.

    C1 does not need a scheduler — it is triggered at session end.
    C2 (Pattern Recognition + Decay) runs at ``c2_interval_hours`` (default 24h).
    C3 (Schema Restructure) runs at ``c3_interval_hours`` (default 168h / 7 days).
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
