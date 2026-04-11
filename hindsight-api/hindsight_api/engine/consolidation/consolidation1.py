"""
Consolidation 1 — Selective Working Memory → Buffer Promotion.

Evaluates each Working Memory Engram using a recall-driven composite
score with saliency boost and two hard gates:

  1. Novelty gate:  novelty < MIN_NOVELTY_FOR_PROMOTE → archive
     (known information is not worth consolidating)
  2. Access gate:   access_count < MIN_ACCESS_FOR_PROMOTE → stay in WM
     (STC: rehearsal is required for capture)
  3. Score gate:    composite >= mode-dependent threshold → promote
     composite = recall_score + SALIENCY_WEIGHT * max(emotional_valence, surprise)

The promote threshold depends on the session_mode at Engram creation:
  precision=0.8, validation=0.7, analogy=0.6, exploration=0.5

Biological mapping:
  Sharp-Wave Ripples (SWS) selectively replay high-salience traces from
  the hippocampus. Only traces that have been reactivated (recalled)
  sufficiently consolidate into semantic memory. Emotional significance
  and surprise lower the consolidation bar but cannot substitute for
  rehearsal — matching the Synaptic Tagging & Capture model.

  Ref: concept.md ch. 12 — Consolidation Pipeline, 4-Stufen-Modell

Epic 12 (original), refactored Epic 24.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.scoring import (
    ARCHIVE_THRESHOLD_WM,
    MIN_ACCESS_FOR_PROMOTE,
    MIN_NOVELTY_FOR_PROMOTE,
    compute_composite_strength,
    get_promote_threshold,
)
from hindsight_api.engine.engram_storage import EngramStorageInterface

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

        # Read current bank.op_count once — used for cycles_alive per Engram.
        bank_op_count = await dict_repo.get_bank_op_count(self._pool, bank_id)

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
                batch_result = await self._process_batch(batch, bank_op_count)
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

    async def _process_batch(self, batch: list[dict], bank_op_count: int) -> ConsolidationResult:
        """Process a single batch of unconsolidated entries."""
        result = ConsolidationResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                novelty = entry.get("novelty") or 0.0
                emotional_valence = entry.get("emotional_valence") or 0.0
                surprise = entry.get("surprise") or 0.0
                access_count = entry.get("access_count") or 0
                created_at_op = entry.get("created_at_op") or 0
                session_mode = entry.get("session_mode")
                cycles_alive = max(0, bank_op_count - created_at_op)

                # Gate 1: Novelty — known info is not worth consolidating
                if novelty < MIN_NOVELTY_FOR_PROMOTE:
                    await self._storage.update_metadata(
                        engram_id,
                        {"status": "archived", "strength": 0.0},
                    )
                    result.archived += 1
                    continue

                # Gate 2: Minimum recalls — STC capture requires rehearsal
                if access_count < MIN_ACCESS_FOR_PROMOTE:
                    strength = compute_composite_strength(emotional_valence, surprise, access_count, cycles_alive)
                    await self._storage.update_metadata(
                        engram_id,
                        {"strength": strength},
                    )
                    result.skipped += 1
                    continue

                # Score + mode-dependent threshold
                strength = compute_composite_strength(emotional_valence, surprise, access_count, cycles_alive)
                threshold = get_promote_threshold(session_mode)

                if strength >= threshold:
                    await self._storage.update_metadata(
                        engram_id,
                        {"layer": "buffer", "strength": strength},
                    )
                    result.consolidated += 1
                else:
                    await self._storage.update_metadata(
                        engram_id,
                        {"strength": strength},
                    )
                    result.skipped += 1

            except Exception as exc:
                logger.error("[Consolidation1] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result
