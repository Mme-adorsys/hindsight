"""
Consolidation 1 — Session → Buffer.

Promotes Engrams from Working Memory (layer=NULL) to Engram Buffer (layer='buffer').
Runs after a session ends or periodically. Idempotent: layer IS NULL is the pending filter.

Biological mapping:
  LTP Early (Pre-Engram Buffer, fragile) → LTP Late (Consolidated Engram, after NCR)
  Ref: concept.md ch. 12 — Consolidation Pipeline, 4-Stufen-Modell

Epic 12, Story 01.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.engram_storage import EngramStorageInterface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BATCH_SIZE = 100
_TIMEOUT_SECONDS = 300  # 5 minutes
_BASE_STRENGTH = 0.1
_BUFFER_STRENGTH_CAP = 0.5


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationResult:
    """Summary of a Consolidation 1 run."""

    total: int = 0
    consolidated: int = 0
    skipped: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Consolidation1Service:
    """
    Promotes unconsolidated Engrams (layer=NULL) to buffer layer.

    Strength is initialized from thalamus_overall:
        strength = min(thalamus_overall * 0.5 + BASE_STRENGTH, BUFFER_STRENGTH_CAP)

    If thalamus_overall is absent, strength defaults to BASE_STRENGTH.

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
            ConsolidationResult with counts of consolidated, skipped, errors.
        """
        result = ConsolidationResult()

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
                batch_result = await self._process_batch(batch)
                result.consolidated += batch_result.consolidated
                result.skipped += batch_result.skipped
                result.errors += batch_result.errors
                result.error_ids.extend(batch_result.error_ids)

                # Advance offset only for skipped/error entries —
                # consolidated entries no longer appear in subsequent queries
                # (layer IS NULL filter excludes them), so we only need to
                # skip past entries that failed to update.
                offset += batch_result.skipped + batch_result.errors

                logger.info(
                    "[Consolidation1] bank=%s batch_size=%d consolidated=%d errors=%d",
                    bank_id,
                    len(batch),
                    batch_result.consolidated,
                    batch_result.errors,
                )

        try:
            await asyncio.wait_for(_run(), timeout=_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("[Consolidation1] Timeout after %ds for bank=%s", _TIMEOUT_SECONDS, bank_id)

        logger.info(
            "[Consolidation1] Done. bank=%s total=%d consolidated=%d skipped=%d errors=%d",
            bank_id,
            result.total,
            result.consolidated,
            result.skipped,
            result.errors,
        )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(self, batch: list[dict]) -> ConsolidationResult:
        """Process a single batch of unconsolidated entries."""
        result = ConsolidationResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                strength = _compute_initial_strength(entry.get("thalamus_overall"))
                # Update Dictionary + mirror layer/strength to Neo4j via StorageService
                await self._storage.update_metadata(
                    engram_id,
                    {"layer": "buffer", "strength": strength},
                )
                result.consolidated += 1
            except Exception as exc:
                logger.error("[Consolidation1] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_initial_strength(thalamus_overall: float | None) -> float:
    """
    Compute initial buffer strength from thalamus overall score.

        strength = min(thalamus_overall * 0.5 + BASE_STRENGTH, BUFFER_STRENGTH_CAP)

    Buffer Engrams never start stronger than 0.5 — they must earn neocortex
    promotion through repeated activation during NCR.
    """
    if thalamus_overall is None:
        return _BASE_STRENGTH
    raw = thalamus_overall * 0.5 + _BASE_STRENGTH
    return min(raw, _BUFFER_STRENGTH_CAP)
