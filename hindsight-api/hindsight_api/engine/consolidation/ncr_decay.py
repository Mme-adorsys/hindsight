"""
NCR Phase 1 — Decay.

Weakens Engrams each NCR cycle. Engrams below the archive threshold are marked
as archived across all three storage systems. Links are preserved — Schema
Formation (NCR Phase 3) may still reference archived Engrams.

Biological mapping:
  SWS / Sharp-Wave Ripples — weak synaptic connections are pruned during slow-wave sleep.
  Frequent activation counteracts Decay (synaptic consolidation via LTP Late).
  Ref: concept.md ch. 12 — NCR Phase 1 (Decay)

Epic 12, Story 02.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.engram_storage import EngramStorageInterface
from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_BATCH_SIZE = 200
_DEFAULT_DECAY_RATE = 0.9
_ARCHIVE_THRESHOLD = 0.05
_EXTRA_DECAY_BASE = 0.95
_EXTRA_DECAY_DAYS_UNIT = 30


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecayConfig:
    """Configurable parameters for NCR Decay.

    decay_rate: Fraction of strength retained per cycle (0.9 = 10% loss).
    archive_threshold: Strength below which an Engram is archived.
    """

    decay_rate: float = _DEFAULT_DECAY_RATE
    archive_threshold: float = _ARCHIVE_THRESHOLD


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class DecayResult:
    """Summary of a single NCR Decay run."""

    total: int = 0
    decayed: int = 0
    archived: int = 0
    unchanged: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DecayProcessor:
    """
    NCR Phase 1: applies decay formula to all active buffer/neocortex Engrams.

    Decay formula (per Engram):
        frequency_bonus  = 1.0 + log10(1 + access_count) / 10
        new_strength     = strength * decay_rate * frequency_bonus
        extra decay if days_since_access > 30:
            new_strength *= 0.95 ^ (days_since_access / 30)

    Archiving (strength < archive_threshold):
        - engram_dictionary: layer='archived', status='archived'
        - Neo4j node:        archived=true
        - Qdrant payload:    archived=true
        Links are preserved for Schema Formation (NCR Phase 3).

    Args:
        pool:            asyncpg connection pool (PostgreSQL).
        storage_service: EngramStorageInterface — mirrors layer/status to Neo4j.
        qdrant:          QdrantEngineClient — for payload-only archive flag.
        config:          DecayConfig with tunable parameters.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        storage_service: EngramStorageInterface,
        qdrant: QdrantEngineClient,
        config: DecayConfig | None = None,
    ) -> None:
        self._pool = pool
        self._storage = storage_service
        self._qdrant = qdrant
        self._config = config or DecayConfig()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def process(self, bank_id: str) -> DecayResult:
        """
        Run NCR Decay for all active buffer/neocortex Engrams of a bank.

        Processes in batches of 200. Each batch is independent — a failure in
        one entry does not block subsequent entries.

        Args:
            bank_id: The memory bank to process.

        Returns:
            DecayResult with counts of decayed, archived, unchanged, errors.
        """
        result = DecayResult()
        offset = 0

        while True:
            batch = await dict_repo.list_active_for_decay(
                self._pool,
                bank_id,
                batch_size=_BATCH_SIZE,
                offset=offset,
            )
            if not batch:
                break

            result.total += len(batch)
            batch_result = await self._process_batch(batch)
            result.decayed += batch_result.decayed
            result.archived += batch_result.archived
            result.unchanged += batch_result.unchanged
            result.errors += batch_result.errors
            result.error_ids.extend(batch_result.error_ids)

            # Archived entries are removed from subsequent queries (status filter).
            # Error/unchanged entries remain — advance offset past them.
            offset += batch_result.unchanged + batch_result.errors

            logger.info(
                "[NCRDecay] bank=%s batch=%d decayed=%d archived=%d errors=%d",
                bank_id,
                len(batch),
                batch_result.decayed,
                batch_result.archived,
                batch_result.errors,
            )

        logger.info(
            "[NCRDecay] Done. bank=%s total=%d decayed=%d archived=%d unchanged=%d errors=%d",
            bank_id,
            result.total,
            result.decayed,
            result.archived,
            result.unchanged,
            result.errors,
        )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(self, batch: list[dict]) -> DecayResult:
        result = DecayResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                current_strength = float(entry.get("strength") or 0.0)
                access_count = int(entry.get("access_count") or 0)
                last_accessed = entry.get("last_accessed")

                new_strength = _apply_decay_formula(
                    strength=current_strength,
                    access_count=access_count,
                    last_accessed=last_accessed,
                    decay_rate=self._config.decay_rate,
                )

                if new_strength < self._config.archive_threshold:
                    await self._archive_engram(engram_id)
                    result.archived += 1
                elif abs(new_strength - current_strength) < 1e-9:
                    result.unchanged += 1
                else:
                    await self._storage.update_metadata(engram_id, {"strength": new_strength})
                    result.decayed += 1

            except Exception as exc:
                logger.error("[NCRDecay] Failed engram=%s: %s", engram_id, exc)
                result.errors += 1
                result.error_ids.append(engram_id)

        return result

    async def _archive_engram(self, engram_id: str) -> None:
        """Mark an Engram as archived in all three systems."""
        # Dictionary + Neo4j mirror (via StorageService)
        await self._storage.update_metadata(
            engram_id,
            {"layer": "archived", "status": "archived"},
        )
        # Qdrant: payload-only flag (no re-embedding needed)
        try:
            await self._qdrant.set_payload_fields(engram_id, {"archived": True})
        except Exception as exc:
            # Non-fatal: Dictionary/Neo4j already updated — log and continue
            logger.warning("[NCRDecay] Qdrant archive flag failed engram=%s: %s", engram_id, exc)


# ---------------------------------------------------------------------------
# Decay formula (pure function — testable without DB)
# ---------------------------------------------------------------------------


def _apply_decay_formula(
    strength: float,
    access_count: int,
    last_accessed: datetime | None,
    decay_rate: float = _DEFAULT_DECAY_RATE,
) -> float:
    """
    Compute new strength after one NCR Decay cycle.

        frequency_bonus = 1.0 + log10(1 + access_count) / 10
        new_strength    = strength * decay_rate * frequency_bonus

    Extra decay for stale Engrams (last_accessed > 30 days ago):
        new_strength   *= 0.95 ^ (days_since_access / 30)

    Args:
        strength:      Current Engram strength (0.0–1.0).
        access_count:  Number of times the Engram has been recalled.
        last_accessed: Timestamp of last access (timezone-aware or naive UTC).
        decay_rate:    Base decay fraction (default 0.9).

    Returns:
        New strength after decay (may exceed current strength for very frequently
        accessed Engrams — NCR Phase 2 will cap at 1.0 during strengthening).
    """
    frequency_bonus = 1.0 + math.log10(1 + access_count) / 10
    new_strength = strength * decay_rate * frequency_bonus

    if last_accessed is not None:
        now = datetime.now(timezone.utc)
        # Normalise naive datetimes to UTC
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        days_since = max(0, (now - last_accessed).days)
        if days_since > _EXTRA_DECAY_DAYS_UNIT:
            new_strength *= _EXTRA_DECAY_BASE ** (days_since / _EXTRA_DECAY_DAYS_UNIT)

    return new_strength
