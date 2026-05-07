"""C2 decay re-evaluation pass for buffer engrams (Epic 25 Story 11).

Replaces the legacy split between C2a (Decay) and C2b (Strengthen). In the
new CLS architecture C2 first does Pattern Recognition (Stories 04–10) and
**then** ages the buffer in a single sweep:

    1. atomically bump ``banks.session_count`` (the bank's clock)
    2. list active buffer engrams for the bank
    3. recompute composite = thalamus_overall × decay using the new clock
    4. archive everything below ``BUFFER_ARCHIVE_COMPOSITE_THRESHOLD``

Concept §13 — engrams age purely by time + access pattern; schemas don't
"steal" engrams. The cortex (schemas) and the buffer (engrams) decay
independently. Concurrent C2 runs are guarded by a per-bank Postgres
advisory lock so a misbehaving scheduler can't double-tick the clock.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..db_utils import acquire_with_retry
from ..engram_dictionary import filter_entries, increment_bank_session_count
from .constants import BUFFER_ARCHIVE_COMPOSITE_THRESHOLD
from .scoring import compute_composite, compute_equilibrium_rate

if TYPE_CHECKING:
    import asyncpg

    from ..engram_types import ThalamusScores  # noqa: F401  # only typed reference

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecayReport:
    """Per-run summary surfaced by ``decay_reevaluate_buffer``."""

    bank_id: str
    total: int
    archived: int
    retained: int
    skipped_locked: bool = False


def _bank_advisory_lock_key(bank_id: str) -> int:
    """Stable 63-bit signed int for ``pg_try_advisory_lock`` per bank.

    Postgres advisory locks accept either a single bigint or a pair of
    int4. We hash the bank id and clamp to the bigint range; collisions
    only matter across banks doing concurrent C2 (very rare, harmless
    short delay). No security property — just a coordination key.
    """
    digest = hashlib.blake2b(bank_id.encode("utf-8"), digest_size=8).digest()
    raw = int.from_bytes(digest, byteorder="big", signed=False)
    # Postgres bigint range is [-2^63, 2^63-1]; mask to fit signed range.
    return raw & ((1 << 63) - 1)


async def decay_reevaluate_buffer(
    bank_id: str,
    pool: "asyncpg.Pool",
    *,
    bank_size_hint: int | None = None,
    threshold: float = BUFFER_ARCHIVE_COMPOSITE_THRESHOLD,
    limit: int = 10_000,
) -> DecayReport:
    """Bump the bank clock and archive sub-threshold buffer engrams.

    Args:
        bank_id: Target bank.
        pool: asyncpg pool.
        bank_size_hint: Pre-computed bank engram count for ``compute_equilibrium_rate``.
            When omitted, falls back to the number of active buffer entries
            we already fetched — close enough for re-evaluation purposes.
        threshold: Composite cutoff. Default 0.05 (concept §13).
        limit: Pre-filter cap; banks larger than this batch C2 across runs.

    Returns a :class:`DecayReport` even on the no-op path (lock taken,
    nothing to do, etc.).
    """
    lock_key = _bank_advisory_lock_key(bank_id)

    async with acquire_with_retry(pool) as conn:
        got_lock = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
        if not got_lock:
            logger.info("decay_reevaluate_buffer bank=%s skipped — advisory lock busy", bank_id)
            return DecayReport(bank_id=bank_id, total=0, archived=0, retained=0, skipped_locked=True)

        try:
            new_session_count = await increment_bank_session_count(pool, bank_id)
            entries = await filter_entries(
                pool,
                bank_id=bank_id,
                layer="buffer",
                status="active",
                limit=limit,
            )
            bank_size = bank_size_hint if bank_size_hint is not None else len(entries)

            archived_ids: list = []
            for entry in entries:
                composite = _composite_for(entry, new_session_count, bank_size)
                if composite < threshold:
                    archived_ids.append(entry["engram_id"])

            if archived_ids:
                await conn.execute(
                    """
                    UPDATE engram_dictionary
                    SET status = 'archived', last_accessed = COALESCE(last_accessed, now())
                    WHERE engram_id = ANY($1::uuid[])
                    """,
                    archived_ids,
                )

            archived_count = len(archived_ids)
            retained_count = len(entries) - archived_count
            logger.info(
                "decay_reevaluate_buffer bank=%s session_count=%d total=%d archived=%d retained=%d",
                bank_id,
                new_session_count,
                len(entries),
                archived_count,
                retained_count,
            )
            return DecayReport(
                bank_id=bank_id,
                total=len(entries),
                archived=archived_count,
                retained=retained_count,
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", lock_key)


def _composite_for(entry: dict, session_count: int, bank_size: int) -> float:
    """Recompute composite for an engram_dictionary row.

    Pulled into a helper so unit tests can drive the math without rebuilding
    the whole pipeline. ``ThalamusScores`` is imported lazily to avoid a
    cycle through ``engine.engram_types`` at module import time.
    """
    from ..engram_types import ThalamusScores  # local import keeps dep graph clean

    thalamus_overall = float(entry.get("thalamus_overall") or 0.0)
    access_count = int(entry.get("access_count") or 0)
    created_at_session = int(entry.get("created_at_session") or 0)
    sessions_alive = max(0, session_count - created_at_session)

    scores = ThalamusScores(
        novelty=float(entry.get("novelty") or 0.0),
        surprise=float(entry.get("surprise") or 0.0),
        task_relevance=float(entry.get("task_relevance") or 0.0),
        emotional_valence=float(entry.get("emotional_valence") or 0.0),
        overall=thalamus_overall,
    )
    rate = compute_equilibrium_rate(scores, mode=None, bank_size=max(1, bank_size))
    return compute_composite(
        thalamus_overall=thalamus_overall,
        access_count=access_count,
        sessions_alive=sessions_alive,
        r=rate,
    )

    # Note: when the orchestrator (Story 19+) wires this up it can pass
    # the engram's session_mode through the entry dict so the equilibrium
    # rate respects the mode-specific R_BASE. For now we use None → DEFAULT_R_BASE.
