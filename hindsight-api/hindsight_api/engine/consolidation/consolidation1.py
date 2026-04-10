"""
Consolidation 1 — Selective Working Memory → Buffer Promotion.

Evaluates each Working Memory Engram using the composite strength formula
(Epic 24) and promotes only those that exceed the PROMOTE_THRESHOLD to
the Buffer layer. Engrams below ARCHIVE_THRESHOLD_WM are archived.
Items that fall between thresholds remain in Working Memory until the
next NCR cycle, giving them more time to accumulate recall-hits.

Biological mapping:
  Sharp-Wave Ripples (SWS) selectively replay high-salience traces from
  the hippocampus. Only replayed traces consolidate into semantic memory.
  The composite score combines synaptic tagging (Thalamus) with
  rehearsal-dependent capture (access frequency) — without rehearsal
  even a strong initial tag eventually fades.

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
    PROMOTE_THRESHOLD,
    compute_composite_strength,
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
    archived: int = 0  # fell below archive threshold
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Consolidation1Service:
    """
    Selectively promotes Working Memory Engrams to the Buffer layer.

    For each unconsolidated Engram, computes:
        composite = 0.7 × thalamus_overall + 0.3 × recount_score
    where recount_score = log(1 + access_count) / log(2 + cycles_alive).

    - composite >= PROMOTE_THRESHOLD (0.4) → layer = 'buffer'
    - composite <  ARCHIVE_THRESHOLD_WM (0.08) → status = 'archived'
    - otherwise → stays in Working Memory for the next cycle

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
                thalamus = entry.get("thalamus_overall")
                access_count = entry.get("access_count") or 0
                created_at_op = entry.get("created_at_op") or 0
                cycles_alive = max(0, bank_op_count - created_at_op)

                strength = compute_composite_strength(thalamus, access_count, cycles_alive)

                if strength >= PROMOTE_THRESHOLD:
                    # Promote to buffer — the Engram earned its place
                    await self._storage.update_metadata(
                        engram_id,
                        {"layer": "buffer", "strength": strength},
                    )
                    result.consolidated += 1
                elif strength < ARCHIVE_THRESHOLD_WM:
                    # Archive — too weak to ever consolidate
                    await self._storage.update_metadata(
                        engram_id,
                        {"status": "archived", "strength": strength},
                    )
                    result.archived += 1
                else:
                    # Stay in Working Memory — not yet strong enough, but not
                    # dead either. Update strength so the next cycle sees the
                    # current composite score.
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
