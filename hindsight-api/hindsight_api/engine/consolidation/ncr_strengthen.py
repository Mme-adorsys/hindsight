"""
NCR Phase 2 — Strengthen.

Promotes buffer Engrams to the neocortex layer when they meet all promotion
criteria. Also increments ncr_cycles_survived for every surviving Engram.

Epic 24 Story 06 promotion logic:
  - composite = ``strength`` (already recomputed in C2a)
  - passes_hard_gates(access_count, novelty, bank_size) must hold
  - composite ≥ get_promote_threshold_for_tags(tags)
  - ncr_cycles_survived ≥ promotion_ncr_cycles (karenz period)
  - Kein künstlicher +0.1 Boost mehr — der composite > 1.0 regime übernimmt
    die Verstärkungs-Rolle.

Biological mapping:
  SWS replay strengthens important memories — repeated reactivation across
  multiple sleep cycles leads to stable long-term storage (LTP Late).
  Promotion to neocortex = transfer from hippocampal buffer to cortical storage.
  Ref: concept.md ch. 12 — NCR Phase 2 (Strengthen)
  Ref: concept.md §5.3 — Lifecycle Scoring

Epic 12 Story 03 (original), refactored Epic 24 Story 06.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.scoring import (
    get_promote_threshold_for_tags,
    log_lifecycle_transition,
    passes_hard_gates,
)
from hindsight_api.engine.engram_storage import EngramStorageInterface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_BATCH_SIZE = 100
_DEFAULT_PROMOTION_NCR_CYCLES = 2


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrengthenConfig:
    """Configurable thresholds for NCR Phase 2 promotion (Epic 24 Story 06).

    promotion_ncr_cycles: Karenzzeit — minimum NCR cycles a buffer Engram
        must survive before it can be promoted. Prevents immediate
        promotion of freshly buffered Engrams that got one lucky recall.
    """

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
    # Epic 24 Story 05: buffer→working downgrades for engrams whose composite
    # fell below their tag-based promote threshold after a karenz period.
    downgraded: int = 0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StrengthenProcessor:
    """NCR Phase 2 (C2b) — Epic 24 Story 06 composite-driven promotion.

    Promotion criteria (all three must be met):
        1. composite ≥ get_promote_threshold_for_tags(tags)
           The composite is read from ``strength`` — C2a already persisted the
           freshly computed value earlier in the same NCR cycle.
        2. passes_hard_gates(access_count, novelty, bank_size)
           Normalized STC access gate + novelty gate.
        3. ncr_cycles_survived ≥ promotion_ncr_cycles (karenz period)

    On promotion:
        - layer: 'buffer' → 'neocortex' (Dictionary + Neo4j mirror)
        - strength unchanged — the composite already reflects amplification via
          ``decay > 1.0``, so the old +0.1 boost is gone.
        - promoted_at: current UTC timestamp

    For all surviving non-archived buffer/neocortex Engrams:
        - ncr_cycles_survived += 1

    Args:
        pool:            asyncpg connection pool (PostgreSQL).
        storage_service: EngramStorageInterface — mirrors layer/strength to Neo4j.
        config:          StrengthenConfig with the karenz threshold.
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
        """Run NCR Strengthen for all active buffer Engrams of a bank.

        Bank-size is fetched once per run and reused for the normalized
        hard gates. Each batch is independent — a failure in one entry does
        not block subsequent entries.

        Args:
            bank_id: The memory bank to process.

        Returns:
            StrengthenResult with counts of promoted, incremented, errors.
        """
        result = StrengthenResult()

        # Epic 24 Story 06: bank size is stable during the run → single fetch.
        bank_size = await dict_repo.get_bank_engram_count(self._pool, bank_id)

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
            batch_result = await self._process_batch(batch, bank_size)
            result.promoted += batch_result.promoted
            result.incremented += batch_result.incremented
            result.errors += batch_result.errors
            result.error_ids.extend(batch_result.error_ids)
            result.downgraded += batch_result.downgraded

            # Advance offset by the rows that stay in the ``layer='buffer'``
            # filter. Promoted entries move to neocortex and downgraded
            # entries (Story 05) move back to working, so both drop out of
            # subsequent fetches. Incremented and errored entries stay in
            # place — without advancing past them we'd loop forever,
            # re-incrementing the same rows.
            # (Discovered in live testing: ncr_cycles_survived ran up to 183k
            # on 5 engrams before the process was killed.)
            offset += len(batch) - batch_result.promoted - batch_result.downgraded

            logger.info(
                "[NCRStrengthen] bank=%s batch=%d promoted=%d incremented=%d downgraded=%d errors=%d",
                bank_id,
                len(batch),
                batch_result.promoted,
                batch_result.incremented,
                batch_result.downgraded,
                batch_result.errors,
            )

        logger.info(
            "[NCRStrengthen] Done. bank=%s total=%d promoted=%d incremented=%d downgraded=%d errors=%d",
            bank_id,
            result.total,
            result.promoted,
            result.incremented,
            result.downgraded,
            result.errors,
        )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(self, batch: list[dict], bank_size: int) -> StrengthenResult:
        result = StrengthenResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                # Neocortex guard — these should never appear in the buffer
                # query, but the bidirectional lifecycle could theoretically
                # route an odd row here. Skip defensively.
                if entry.get("layer") == "neocortex":
                    continue

                # Epic 24 Story 05: Buffer → Working downgrade. A buffer
                # Engram whose composite fell below the tag-based promote
                # threshold despite sitting in the buffer for at least
                # ``promotion_ncr_cycles`` cycles is demoted back to working
                # memory. Fresh promotions (within the karenz window) are
                # left alone so one bad cycle doesn't immediately bounce them.
                if self._should_downgrade(entry):
                    composite = float(entry.get("strength") or 0.0)
                    await self._downgrade(engram_id, composite)
                    result.downgraded += 1
                    continue

                if self._meets_promotion_criteria(entry, bank_size):
                    composite = float(entry.get("strength") or 0.0)
                    await self._promote(engram_id, composite)
                    log_lifecycle_transition(
                        engram_id,
                        from_layer="buffer",
                        to_layer="neocortex",
                        composite=composite,
                        trigger="promote",
                    )
                    result.promoted += 1
                else:
                    await dict_repo.increment_ncr_cycles(self._pool, engram_id)
                    result.incremented += 1
            except Exception as exc:
                logger.error("[NCRStrengthen] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result

    def _meets_promotion_criteria(self, entry: dict, bank_size: int) -> bool:
        """Return True if all three Epic 24 promotion criteria are satisfied."""
        composite = float(entry.get("strength") or 0.0)
        access_count = int(entry.get("access_count") or 0)
        ncr_cycles = int(entry.get("ncr_cycles_survived") or 0)
        novelty = float(entry.get("novelty") or 0.0)
        tags = entry.get("tags") or None

        if ncr_cycles < self._config.promotion_ncr_cycles:
            return False
        if not passes_hard_gates(access_count, novelty, bank_size):
            return False
        threshold = get_promote_threshold_for_tags(tags)
        return composite >= threshold

    def _should_downgrade(self, entry: dict) -> bool:
        """Return True if a buffer Engram should drop back to working memory.

        Applied only after the karenz period has passed (``ncr_cycles_survived
        ≥ promotion_ncr_cycles``) so freshly buffered Engrams get a protected
        observation window before any bounces. Neocortex is already guarded
        by the caller.
        """
        ncr_cycles = int(entry.get("ncr_cycles_survived") or 0)
        if ncr_cycles < self._config.promotion_ncr_cycles:
            return False
        composite = float(entry.get("strength") or 0.0)
        tags = entry.get("tags") or None
        threshold = get_promote_threshold_for_tags(tags)
        return composite < threshold

    async def _promote(self, engram_id: str, current_strength: float) -> None:
        """Promote a buffer Engram to neocortex layer (no strength boost)."""
        await self._storage.update_metadata(
            engram_id,
            {
                "layer": "neocortex",
                "strength": current_strength,
                "promoted_at": datetime.now(timezone.utc),
            },
        )

    async def _downgrade(self, engram_id: str, current_strength: float) -> None:
        """Demote a buffer Engram back to the working layer (Story 05)."""
        await self._storage.update_metadata(
            engram_id,
            {"layer": "working", "strength": current_strength},
        )
        log_lifecycle_transition(
            engram_id,
            from_layer="buffer",
            to_layer="working",
            composite=current_strength,
            trigger="downgrade",
        )
