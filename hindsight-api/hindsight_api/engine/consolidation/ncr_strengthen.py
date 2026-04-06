"""
NCR Phase 2 — Strengthen.

Promotes buffer Engrams to the neocortex layer when they meet all promotion
criteria. Also increments ncr_cycles_survived for every surviving Engram.

Biological mapping:
  SWS replay strengthens important memories — repeated reactivation across
  multiple sleep cycles leads to stable long-term storage (LTP Late).
  Promotion to neocortex = transfer from hippocampal buffer to cortical storage.
  Ref: concept.md ch. 12 — NCR Phase 2 (Strengthen)

Epic 12, Story 03.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.engram_storage import EngramStorageInterface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_BATCH_SIZE = 100
_DEFAULT_PROMOTION_STRENGTH = 0.4
_DEFAULT_PROMOTION_ACCESS = 3
_DEFAULT_PROMOTION_NCR_CYCLES = 2
_PROMOTION_STRENGTH_BOOST = 0.1
_STRENGTH_MAX = 1.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrengthenConfig:
    """Configurable thresholds for NCR Phase 2 promotion.

    promotion_strength_threshold: Minimum strength for neocortex promotion.
    promotion_access_threshold:   Minimum access_count for promotion.
    promotion_ncr_cycles:         Minimum NCR cycles survived for promotion.
    """

    promotion_strength_threshold: float = _DEFAULT_PROMOTION_STRENGTH
    promotion_access_threshold: int = _DEFAULT_PROMOTION_ACCESS
    promotion_ncr_cycles: int = _DEFAULT_PROMOTION_NCR_CYCLES


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class StrengthenResult:
    """Summary of a single NCR Strengthen run."""

    total: int = 0
    promoted: int = 0
    incremented: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StrengthenProcessor:
    """
    NCR Phase 2: promotes eligible buffer Engrams to neocortex layer.

    Promotion criteria (all three must be met):
        1. strength >= promotion_strength_threshold  (default 0.4)
        2. access_count >= promotion_access_threshold (default 3)
        3. ncr_cycles_survived >= promotion_ncr_cycles (default 2)

    On promotion:
        - layer: 'buffer' → 'neocortex' (Dictionary + Neo4j mirror)
        - strength += PROMOTION_STRENGTH_BOOST (capped at 1.0)
        - promoted_at: current UTC timestamp

    For all surviving non-archived buffer/neocortex Engrams:
        - ncr_cycles_survived += 1

    Args:
        pool:            asyncpg connection pool (PostgreSQL).
        storage_service: EngramStorageInterface — mirrors layer/strength to Neo4j.
        config:          StrengthenConfig with tunable thresholds.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        storage_service: EngramStorageInterface,
        config: StrengthenConfig | None = None,
    ) -> None:
        self._pool = pool
        self._storage = storage_service
        self._config = config or StrengthenConfig()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def process(self, bank_id: str) -> StrengthenResult:
        """
        Run NCR Strengthen for all active buffer Engrams of a bank.

        Two passes:
          1. Promotion pass — check buffer Engrams against promotion criteria
          2. Cycle increment — increment ncr_cycles_survived for all survivors

        The cycle increment runs via a single bulk UPDATE for efficiency.

        Args:
            bank_id: The memory bank to process.

        Returns:
            StrengthenResult with counts of promoted, incremented, errors.
        """
        result = StrengthenResult()
        offset = 0

        while True:
            batch = await dict_repo.list_buffer_for_strengthen(
                self._pool,
                bank_id,
                batch_size=_BATCH_SIZE,
                offset=offset,
            )
            if not batch:
                break

            result.total += len(batch)
            batch_result = await self._process_batch(batch)
            result.promoted += batch_result.promoted
            result.incremented += batch_result.incremented
            result.errors += batch_result.errors
            result.error_ids.extend(batch_result.error_ids)

            # Promoted entries move to neocortex — excluded from next buffer query.
            # Only failed entries remain at same offset.
            offset += batch_result.errors

            logger.info(
                "[NCRStrengthen] bank=%s batch=%d promoted=%d incremented=%d errors=%d",
                bank_id,
                len(batch),
                batch_result.promoted,
                batch_result.incremented,
                batch_result.errors,
            )

        logger.info(
            "[NCRStrengthen] Done. bank=%s total=%d promoted=%d incremented=%d errors=%d",
            bank_id,
            result.total,
            result.promoted,
            result.incremented,
            result.errors,
        )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(self, batch: list[dict]) -> StrengthenResult:
        result = StrengthenResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                if self._meets_promotion_criteria(entry):
                    await self._promote(engram_id, float(entry.get("strength") or 0.0))
                    result.promoted += 1
                else:
                    await dict_repo.increment_ncr_cycles(self._pool, engram_id)
                    result.incremented += 1
            except Exception as exc:
                logger.error("[NCRStrengthen] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result

    def _meets_promotion_criteria(self, entry: dict) -> bool:
        """Return True if all three promotion criteria are satisfied."""
        strength = float(entry.get("strength") or 0.0)
        access_count = int(entry.get("access_count") or 0)
        ncr_cycles = int(entry.get("ncr_cycles_survived") or 0)

        return (
            strength >= self._config.promotion_strength_threshold
            and access_count >= self._config.promotion_access_threshold
            and ncr_cycles >= self._config.promotion_ncr_cycles
        )

    async def _promote(self, engram_id: str, current_strength: float) -> None:
        """Promote a buffer Engram to neocortex layer."""
        new_strength = min(current_strength + _PROMOTION_STRENGTH_BOOST, _STRENGTH_MAX)
        await self._storage.update_metadata(
            engram_id,
            {
                "layer": "neocortex",
                "strength": new_strength,
                "promoted_at": datetime.now(timezone.utc),
            },
        )
