"""
NCR Phase 1 — Decay.

Recomputes each active Engram's composite score each NCR cycle using the
Epic 24 formula:

    composite = thalamus_overall × compute_decay(access_count, sessions_alive, r)

The result is persisted as ``strength``. Engrams whose composite falls below
the layer-appropriate archive threshold are marked archived across all three
storage systems. Links are preserved — Schema Formation (NCR Phase 3) may
still reference archived Engrams. Neocortex-layer Engrams are skipped
entirely (they are consolidated long-term memory and should not decay).

Biological mapping:
  SWS / Sharp-Wave Ripples — weak synaptic connections are pruned during
  slow-wave sleep. Frequent activation counteracts decay (LTP Late).
  Ref: concept.md ch. 12 — NCR Phase 1 (Decay)
  Ref: concept.md §5.3 — Lifecycle Scoring

Epic 12 Story 02 (original), refactored Epic 24 Story 06.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.consolidation.scoring import (
    ARCHIVE_THRESHOLD_BUFFER,
    ARCHIVE_THRESHOLD_WM,
    compute_composite,
    compute_equilibrium_rate,
    compute_min_access,
    get_promote_threshold_for_tags,
    log_lifecycle_transition,
    passes_hard_gates,
    sessions_alive,
)
from hindsight_api.engine.engram_storage import EngramStorageInterface
from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_BATCH_SIZE = 200


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecayConfig:
    """Configurable parameters for NCR Decay (Epic 24 Story 06).

    The decay formula itself is no longer configurable — it lives in
    ``scoring.compute_composite`` and is derived from the Thalamus birth value,
    access count, sessions alive, and equilibrium rate. Only the two archive
    thresholds can still be overridden, e.g. for integration tests that want
    tighter or looser boundaries.

    archive_threshold_wm:     composite floor for ``layer='working'`` Engrams.
    archive_threshold_buffer: composite floor for ``layer='buffer'`` Engrams.
    """

    archive_threshold_wm: float = ARCHIVE_THRESHOLD_WM
    archive_threshold_buffer: float = ARCHIVE_THRESHOLD_BUFFER


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
    # Epic 24 Story 05: reactivation counters.
    # ``reactivated_to_working`` / ``reactivated_to_buffer`` split out so the
    # dashboard can distinguish "came back" from "came back strong".
    reactivated_to_working: int = 0
    reactivated_to_buffer: int = 0
    # Epic 24 Story 06: histogram of composite scores observed during the
    # decay pass. Populated inline as each Engram is scored so we don't need
    # a second query. Buckets: "<0.1", "0.1-0.3", "0.3-0.5", "0.5-0.7",
    # "0.7-1.0", ">1.0". Surfaced via NCRReport.composite_distribution.
    composite_distribution: dict[str, int] = field(
        default_factory=lambda: {
            "<0.1": 0,
            "0.1-0.3": 0,
            "0.3-0.5": 0,
            "0.5-0.7": 0,
            "0.7-1.0": 0,
            ">1.0": 0,
        }
    )

    @property
    def reactivated_count(self) -> int:
        return self.reactivated_to_working + self.reactivated_to_buffer


def _bucket_composite(value: float) -> str:
    """Return the histogram bucket for a composite score."""
    if value < 0.1:
        return "<0.1"
    if value < 0.3:
        return "0.1-0.3"
    if value < 0.5:
        return "0.3-0.5"
    if value < 0.7:
        return "0.5-0.7"
    if value <= 1.0:
        return "0.7-1.0"
    return ">1.0"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DecayProcessor:
    """NCR Phase 1 (C2a) — Epic 24 Story 06 composite-based decay.

    For every active ``buffer`` / ``working`` Engram in the bank the processor
    reconstructs the Thalamus birth value, derives the per-Engram equilibrium
    rate, computes the new composite, and persists it as ``strength``. Engrams
    whose composite falls below the layer-appropriate archive threshold are
    archived in all three storage systems. ``neocortex`` Engrams are guarded
    against decay entirely — they are already consolidated long-term memory.

    Archive behavior (unchanged):
        - engram_dictionary: layer='archived', status='archived'
        - Neo4j node:        archived=true
        - Qdrant payload:    archived=true
        Links are preserved for Schema Formation (NCR Phase 3).

    Args:
        pool:            asyncpg connection pool (PostgreSQL).
        storage_service: EngramStorageInterface — mirrors layer/status to Neo4j.
        qdrant:          QdrantEngineClient — for payload-only archive flag.
        config:          DecayConfig with tunable archive thresholds.
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
        """Run NCR Decay for all active buffer/working Engrams of a bank.

        Processes in batches of 200. Each batch is independent — a failure in
        one entry does not block subsequent entries. ``bank.session_count`` and
        the bank engram count are fetched once at the start and reused for the
        entire run (they are stable during the pass).

        Args:
            bank_id: The memory bank to process.

        Returns:
            DecayResult with counts of decayed, archived, unchanged, errors.
        """
        result = DecayResult()

        # Epic 24 Story 06: bank-level scoring inputs fetched once per run.
        bank_session_count = await dict_repo.get_bank_session_count(self._pool, bank_id)
        bank_size = await dict_repo.get_bank_engram_count(self._pool, bank_id)

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
            batch_result = await self._process_batch(batch, bank_session_count, bank_size)
            result.decayed += batch_result.decayed
            result.archived += batch_result.archived
            result.unchanged += batch_result.unchanged
            result.errors += batch_result.errors
            result.error_ids.extend(batch_result.error_ids)
            for bucket, count in batch_result.composite_distribution.items():
                result.composite_distribution[bucket] += count

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

        # Epic 24 Story 05 — Reactivation pass. After the active engrams have
        # had their composite recomputed, scan the archived tombstones and see
        # whether any have climbed back above the WM archive threshold (for
        # instance because a recent recall bumped their access_count). Strong
        # reactivators that also pass the promote gate skip straight to buffer.
        await self._reactivate_archived_engrams(
            bank_id,
            bank_session_count,
            bank_size,
            result,
        )

        logger.info(
            "[NCRDecay] Done. bank=%s total=%d decayed=%d archived=%d unchanged=%d reactivated=%d errors=%d",
            bank_id,
            result.total,
            result.decayed,
            result.archived,
            result.unchanged,
            result.reactivated_count,
            result.errors,
        )
        return result

    async def _reactivate_archived_engrams(
        self,
        bank_id: str,
        bank_session_count: int,
        bank_size: int,
        result: DecayResult,
    ) -> None:
        """Re-score archived Engrams and lift those that recovered (Story 05).

        Walks the archived pool in batches, recomputes the Epic 24 composite,
        and restores rows to the active pool when their new score crosses
        ``ARCHIVE_THRESHOLD_WM``. Strong reactivators that also pass the
        tag-based promote threshold plus hard gates skip straight to buffer
        — matching the bio intuition that a surprising, well-rehearsed
        long-dormant trace goes directly into short-term consolidation.
        """
        min_access = compute_min_access(bank_size)
        offset = 0

        while True:
            batch = await dict_repo.list_archived_for_reactivation(
                self._pool,
                bank_id,
                batch_size=_BATCH_SIZE,
                offset=offset,
            )
            if not batch:
                break

            # ``offset`` advances by the count of rows we leave in the archived
            # pool this round — rows we reactivate disappear from the filter on
            # the next fetch, so we only skip past the survivors + errors.
            batch_survivors = 0

            for entry in batch:
                engram_id = str(entry["engram_id"])
                try:
                    access_count = int(entry.get("access_count") or 0)
                    created_at_session = int(entry.get("created_at_session") or 0)
                    session_mode = entry.get("session_mode")
                    tags = entry.get("tags") or None
                    raw_novelty = entry.get("novelty")
                    novelty_is_null = raw_novelty is None
                    novelty = float(raw_novelty) if raw_novelty is not None else 0.0

                    thalamus_scores = ThalamusScores(
                        novelty=novelty,
                        surprise=float(entry.get("surprise") or 0.0),
                        task_relevance=float(entry.get("task_relevance") or 0.0),
                        emotional_valence=float(entry.get("emotional_valence") or 0.0),
                        overall=float(entry.get("thalamus_overall") or 0.0),
                    )

                    sa = sessions_alive(bank_session_count, created_at_session)
                    r = compute_equilibrium_rate(thalamus_scores, session_mode, bank_size)
                    composite = compute_composite(
                        thalamus_scores.overall,
                        access_count,
                        sa,
                        r,
                    )

                    # Did it climb back above the WM floor at all?
                    if composite < ARCHIVE_THRESHOLD_WM:
                        batch_survivors += 1
                        continue

                    # Strong reactivator → straight to buffer if it also clears
                    # the promote threshold and the normalized hard gates. The
                    # novelty gate is skipped for synthesized engrams.
                    promote_threshold = get_promote_threshold_for_tags(tags)
                    access_ok = access_count >= min_access
                    novelty_ok = novelty_is_null or novelty >= 0.2
                    hard_gates_passed = access_ok and novelty_ok

                    if composite >= promote_threshold and hard_gates_passed:
                        await self._reactivate_engram(engram_id, "buffer", composite)
                        result.reactivated_to_buffer += 1
                    else:
                        await self._reactivate_engram(engram_id, "working", composite)
                        result.reactivated_to_working += 1

                except Exception as exc:
                    logger.error("[NCRDecay reactivation] Failed engram=%s: %s", engram_id, exc)
                    result.errors += 1
                    result.error_ids.append(engram_id)
                    batch_survivors += 1

            offset += batch_survivors

    async def _reactivate_engram(self, engram_id: str, target_layer: str, composite: float) -> None:
        """Lift an archived Engram back into the active pool.

        Updates Dictionary + Neo4j mirror to ``status='active'`` and the given
        layer, then clears the Qdrant ``archived`` payload flag so the recall
        path can see the row again. Logs a lifecycle transition so the NCR
        dashboard can reconstruct the flow.
        """
        await self._storage.update_metadata(
            engram_id,
            {"layer": target_layer, "status": "active", "strength": composite},
        )
        try:
            await self._qdrant.set_payload_fields(engram_id, {"archived": False})
        except Exception as exc:
            logger.warning("[NCRDecay] Qdrant reactivate flag failed engram=%s: %s", engram_id, exc)
        log_lifecycle_transition(
            engram_id,
            from_layer=None,
            to_layer=target_layer,
            composite=composite,
            trigger="reactivate",
        )

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _process_batch(
        self,
        batch: list[dict],
        bank_session_count: int,
        bank_size: int,
    ) -> DecayResult:
        result = DecayResult()

        for entry in batch:
            engram_id = str(entry["engram_id"])
            try:
                layer = entry.get("layer")
                # Neocortex guard — consolidated memories do not decay. Story 05
                # will add the bidirectional lifecycle which touches other
                # layers; neocortex stays stable through all of it.
                if layer == "neocortex":
                    result.unchanged += 1
                    continue

                current_strength = float(entry.get("strength") or 0.0)
                access_count = int(entry.get("access_count") or 0)
                created_at_session = int(entry.get("created_at_session") or 0)
                session_mode = entry.get("session_mode")

                thalamus_scores = ThalamusScores(
                    novelty=float(entry.get("novelty") or 0.0),
                    surprise=float(entry.get("surprise") or 0.0),
                    task_relevance=float(entry.get("task_relevance") or 0.0),
                    emotional_valence=float(entry.get("emotional_valence") or 0.0),
                    overall=float(entry.get("thalamus_overall") or 0.0),
                )

                sa = sessions_alive(bank_session_count, created_at_session)
                r = compute_equilibrium_rate(thalamus_scores, session_mode, bank_size)
                new_strength = compute_composite(
                    thalamus_scores.overall,
                    access_count,
                    sa,
                    r,
                )

                # Epic 24 Story 06: feed the histogram — every scored Engram
                # gets one bucket increment regardless of outcome (archive /
                # unchanged / decayed).
                result.composite_distribution[_bucket_composite(new_strength)] += 1

                archive_threshold = (
                    self._config.archive_threshold_buffer if layer == "buffer" else self._config.archive_threshold_wm
                )

                if new_strength < archive_threshold:
                    await self._archive_engram(engram_id)
                    log_lifecycle_transition(
                        engram_id,
                        from_layer=layer,
                        to_layer=None,
                        composite=new_strength,
                        trigger="archive",
                    )
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
