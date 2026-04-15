"""
Consolidation 1 — Selective Working Memory → Buffer Promotion.

Evaluates each Working Memory Engram using the Epic 24 composite score:

  composite = thalamus_overall × decay(access_count, sessions_alive, r)
  r         = compute_equilibrium_rate(thalamus_scores, session_mode, bank_size)

Two gates control promotion:

  1. Novelty gate:  novelty < MIN_NOVELTY_FOR_PROMOTE → archive
     (known information is not worth consolidating; skipped for synthesized
     engrams where novelty is NULL)
  2. Hard gates:    access_count >= compute_min_access(bank_size)
                    AND novelty >= MIN_NOVELTY_FOR_PROMOTE
     (STC rehearsal requirement, normalized per bank size)
  3. Score gate:    composite >= get_promote_threshold_for_tags(tags)
     (tag-driven promote bar — facts strict, experiences/opinions lenient)

Biological mapping:
  Sharp-Wave Ripples (SWS) selectively replay high-salience traces from
  the hippocampus. Only traces that have been reactivated (recalled)
  sufficiently consolidate into semantic memory. Emotional significance
  and surprise lower the consolidation bar but cannot substitute for
  rehearsal — matching the Synaptic Tagging & Capture model.

  Ref: concept.md ch. 12 — Consolidation Pipeline, 4-Stufen-Modell
  Ref: concept.md §5.3 — Lifecycle Scoring

Epic 12 (original), refactored Epic 24 Story 06.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.scoring import (
    MIN_NOVELTY_FOR_PROMOTE,
    compute_composite,
    compute_equilibrium_rate,
    compute_min_access,
    get_promote_threshold_for_tags,
    sessions_alive,
)
from hindsight_api.engine.engram_storage import EngramStorageInterface
from hindsight_api.engine.engram_types import ThalamusScores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BATCH_SIZE = 100
_TIMEOUT_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationResult:
    """Summary of a Consolidation 1 run."""

    total: int = 0
    consolidated: int = 0
    skipped: int = 0  # stayed in Working Memory (below promote, above archive)
    archived: int = 0  # fell below archive threshold or novelty gate
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Consolidation1Service:
    """
    Selectively promotes Working Memory Engrams to the Buffer layer.

    Decision logic per Engram (4 outcomes):

    1. novelty < MIN_NOVELTY (0.2)
       → archive (known info, not worth keeping)

    2. access_count < MIN_ACCESS (5)
       → stay in WM (not yet rehearsed enough — STC capture pending)

    3. composite >= mode-dependent threshold
       → promote to buffer (earned through recall + saliency)

    4. otherwise
       → stay in WM (score too low, needs more recalls)

    Composite score:
        saliency = max(emotional_valence, surprise)
        recall_score = log(1 + access_count) / log(2 + cycles_alive)
        composite = recall_score + 0.3 * saliency

    Mode thresholds: precision=0.8, validation=0.7, analogy=0.6, exploration=0.5

    Args:
        pool:            asyncpg connection pool (PostgreSQL).
        storage_service: EngramStorageInterface — used to mirror layer to Neo4j.
    """

    def __init__(self, pool: asyncpg.Pool, storage_service: EngramStorageInterface) -> None:
        self._pool = pool
        self._storage = storage_service

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run(self, bank_id: str) -> ConsolidationResult:
        """
        Consolidate all pending Engrams for a bank.

        Runs until no more unconsolidated entries remain or timeout is reached.
        Each batch is processed independently — a failure in one batch does not
        block subsequent batches.

        Args:
            bank_id: The memory bank to consolidate.

        Returns:
            ConsolidationResult with counts.
        """
        result = ConsolidationResult()

        # Epic 24 Story 06: read bank-level scoring inputs once per run.
        # session_count drives sessions_alive, engram_count drives bank_factor
        # — both stay constant during the scoring pass, so one fetch is enough.
        bank_session_count = await dict_repo.get_bank_session_count(self._pool, bank_id)
        bank_size = await dict_repo.get_bank_engram_count(self._pool, bank_id)
        min_access = compute_min_access(bank_size)

        async def _run() -> None:
            offset = 0
            while True:
                batch = await dict_repo.list_unconsolidated(
                    self._pool,
                    bank_id,
                    batch_size=_BATCH_SIZE,
                    offset=offset,
                )
                if not batch:
                    break

                result.total += len(batch)
                batch_result = await self._process_batch(batch, bank_session_count, bank_size, min_access)
                result.consolidated += batch_result.consolidated
                result.skipped += batch_result.skipped
                result.archived += batch_result.archived
                result.errors += batch_result.errors
                result.error_ids.extend(batch_result.error_ids)

                # Advance offset for entries that did NOT change their layer filter
                # status. Promoted → layer='buffer' (exits IS NULL filter).
                # Archived → status='archived' (exits status='active' filter).
                # Skipped → stays in place, need to advance past it.
                offset += batch_result.skipped + batch_result.errors

                logger.info(
                    "[Consolidation1] bank=%s batch=%d promoted=%d skipped=%d archived=%d errors=%d",
                    bank_id,
                    len(batch),
                    batch_result.consolidated,
                    batch_result.skipped,
                    batch_result.archived,
                    batch_result.errors,
                )

        try:
            await asyncio.wait_for(_run(), timeout=_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("[Consolidation1] Timeout after %ds for bank=%s", _TIMEOUT_SECONDS, bank_id)

        logger.info(
            "[Consolidation1] Done. bank=%s total=%d promoted=%d skipped=%d archived=%d errors=%d",
            bank_id,
            result.total,
            result.consolidated,
            result.skipped,
            result.archived,
            result.errors,
        )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(
        self,
        batch: list[dict],
        bank_session_count: int,
        bank_size: int,
        min_access: int,
    ) -> ConsolidationResult:
        """Process a single batch of unconsolidated entries (Epic 24 Story 06)."""
        result = ConsolidationResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                # Note: novelty can legitimately be NULL for synthesized engrams
                # (observations from observation_regeneration). NULL means
                # "not applicable" — these are abstractions, not raw input that
                # would need a novelty check. We skip BOTH the novelty gate and
                # the novelty component of the hard gates for them.
                raw_novelty = entry.get("novelty")
                novelty_is_null = raw_novelty is None
                novelty = float(raw_novelty) if raw_novelty is not None else 0.0

                access_count = int(entry.get("access_count") or 0)
                created_at_session = int(entry.get("created_at_session") or 0)
                session_mode = entry.get("session_mode")
                tags = entry.get("tags") or None

                # Reconstruct ThalamusScores (ValueError-safe: all fields default
                # to 0.0 and get clamped into [0, 1] by the DB write path).
                thalamus_scores = ThalamusScores(
                    novelty=novelty,
                    surprise=float(entry.get("surprise") or 0.0),
                    task_relevance=float(entry.get("task_relevance") or 0.0),
                    emotional_valence=float(entry.get("emotional_valence") or 0.0),
                    overall=float(entry.get("thalamus_overall") or 0.0),
                )

                # Gate 1: Novelty — known info is not worth consolidating.
                # Skipped for synthesized engrams (NULL novelty) — they have
                # no novelty score because they're abstractions, not raw input.
                if not novelty_is_null and novelty < MIN_NOVELTY_FOR_PROMOTE:
                    await self._storage.update_metadata(
                        engram_id,
                        {"status": "archived", "strength": 0.0},
                    )
                    result.archived += 1
                    continue

                # Composite score via new Epic 24 formula.
                sa = sessions_alive(bank_session_count, created_at_session)
                r = compute_equilibrium_rate(thalamus_scores, session_mode, bank_size)
                composite = compute_composite(
                    thalamus_scores.overall,
                    access_count,
                    sa,
                    r,
                )

                # Hard gates: access + novelty (novelty check skipped for
                # synthesized engrams, matching the Gate 1 bypass).
                access_ok = access_count >= min_access
                novelty_ok = novelty_is_null or novelty >= MIN_NOVELTY_FOR_PROMOTE
                hard_gates_passed = access_ok and novelty_ok

                threshold = get_promote_threshold_for_tags(tags)

                if hard_gates_passed and composite >= threshold:
                    await self._storage.update_metadata(
                        engram_id,
                        {"layer": "buffer", "strength": composite},
                    )
                    result.consolidated += 1
                else:
                    await self._storage.update_metadata(
                        engram_id,
                        {"strength": composite},
                    )
                    result.skipped += 1

            except Exception as exc:
                logger.error("[Consolidation1] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result
