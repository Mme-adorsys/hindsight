"""
Repository for the engram_dictionary table (Hippocampal Pointer Index).

Provides CRUD + filter operations via the existing asyncpg pool.
Uses acquire_with_retry() from db_utils.py — no new connection management needed.

The Dictionary is read-only from outside the Engram Storage Service (Story 04).
These methods are called by the EngramStorageService, not directly by endpoints.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from hindsight_api.engine.db_utils import acquire_with_retry

logger = logging.getLogger(__name__)


async def insert_entry(pool: asyncpg.Pool, engram_data: dict[str, Any]) -> None:
    """
    Insert a new entry into engram_dictionary.

    Args:
        pool: asyncpg connection pool.
        engram_data: Dict with at minimum engram_id and bank_id.
                     Optional fields: strength, layer, abstraction_level, tags,
                     novelty, surprise, task_relevance, emotional_valence,
                     thalamus_overall, status, confidence_score, session_ref,
                     expectation, outcome.
    """
    async with acquire_with_retry(pool) as conn:
        await conn.execute(
            """
            INSERT INTO engram_dictionary (
                engram_id, bank_id, strength, layer, abstraction_level, tags,
                novelty, surprise, task_relevance, emotional_valence, thalamus_overall,
                created_at, status, confidence_score, session_ref,
                expectation, outcome
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11,
                $12, $13, $14, $15,
                $16, $17
            )
            """,
            UUID(str(engram_data["engram_id"])),
            engram_data["bank_id"],
            engram_data.get("strength", 0.0),
            engram_data.get("layer"),
            engram_data.get("abstraction_level", 0.0),
            engram_data.get("tags"),
            engram_data.get("novelty"),
            engram_data.get("surprise"),
            engram_data.get("task_relevance"),
            engram_data.get("emotional_valence"),
            engram_data.get("thalamus_overall"),
            engram_data.get("created_at", datetime.now(timezone.utc)),
            engram_data.get("status", "active"),
            engram_data.get("confidence_score"),
            UUID(str(engram_data["session_ref"])) if engram_data.get("session_ref") else None,
            engram_data.get("expectation"),
            engram_data.get("outcome"),
        )


async def update_entry(pool: asyncpg.Pool, engram_id: str, updates: dict[str, Any]) -> None:
    """
    Update fields of an existing engram_dictionary entry.

    Only the keys present in `updates` are changed.

    Args:
        pool: asyncpg connection pool.
        engram_id: UUID string of the Engram to update.
        updates: Dict of field names → new values.
    """
    if not updates:
        return

    allowed = {
        "strength",
        "layer",
        "abstraction_level",
        "tags",
        "novelty",
        "surprise",
        "task_relevance",
        "emotional_valence",
        "thalamus_overall",
        "last_accessed",
        "access_count",
        "status",
        "confidence_score",
        "session_ref",
        "ncr_cycles_survived",
        "promoted_at",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        logger.warning(f"update_entry: no valid fields in updates for engram_id={engram_id}")
        return

    set_clauses = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(filtered))
    values = list(filtered.values())

    async with acquire_with_retry(pool) as conn:
        await conn.execute(
            f"UPDATE engram_dictionary SET {set_clauses} WHERE engram_id = $1",
            UUID(engram_id),
            *values,
        )


async def get_by_id(pool: asyncpg.Pool, engram_id: str) -> dict[str, Any] | None:
    """
    Retrieve a single entry by engram_id.

    Returns:
        Dict with all engram_dictionary columns, or None if not found.
    """
    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM engram_dictionary WHERE engram_id = $1",
            UUID(engram_id),
        )
    return dict(row) if row else None


async def delete_by_id(pool: asyncpg.Pool, engram_id: str) -> None:
    """Delete an entry by engram_id."""
    async with acquire_with_retry(pool) as conn:
        await conn.execute(
            "DELETE FROM engram_dictionary WHERE engram_id = $1",
            UUID(engram_id),
        )


async def filter_entries(
    pool: asyncpg.Pool,
    bank_id: str,
    strength_min: float = 0.0,
    layer: str | None = None,
    status: str | None = "active",
    tags: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Filter engram_dictionary entries by bank + optional predicates.

    Args:
        pool: asyncpg connection pool.
        bank_id: Required — all queries are bank-scoped.
        strength_min: Minimum strength threshold (0.0 = no filter).
        layer: Optional layer filter ('buffer' or 'neocortex').
        status: Optional status filter (default: 'active').
        tags: Optional list — returns entries where tags @> provided list (superset).
        limit: Maximum number of results.

    Returns:
        List of matching entries as dicts.
    """
    conditions = ["bank_id = $1"]
    params: list[Any] = [bank_id]

    if strength_min > 0.0:
        params.append(strength_min)
        conditions.append(f"strength >= ${len(params)}")

    if layer is not None:
        params.append(layer)
        conditions.append(f"layer = ${len(params)}")

    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")

    if tags:
        params.append(tags)
        conditions.append(f"tags @> ${len(params)}")

    params.append(limit)
    where_clause = " AND ".join(conditions)

    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM engram_dictionary WHERE {where_clause} ORDER BY strength DESC LIMIT ${len(params)}",
            *params,
        )
    return [dict(row) for row in rows]


async def batch_insert(pool: asyncpg.Pool, entries: list[dict[str, Any]]) -> None:
    """
    Insert multiple entries in a single transaction.

    Args:
        pool: asyncpg connection pool.
        entries: List of engram_data dicts (see insert_entry for schema).
    """
    async with acquire_with_retry(pool) as conn:
        async with conn.transaction():
            for entry in entries:
                await conn.execute(
                    """
                    INSERT INTO engram_dictionary (
                        engram_id, bank_id, strength, layer, abstraction_level, tags,
                        novelty, surprise, task_relevance, emotional_valence, thalamus_overall,
                        created_at, status, confidence_score, session_ref
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    """,
                    UUID(str(entry["engram_id"])),
                    entry["bank_id"],
                    entry.get("strength", 0.0),
                    entry.get("layer"),
                    entry.get("abstraction_level", 0.0),
                    entry.get("tags"),
                    entry.get("novelty"),
                    entry.get("surprise"),
                    entry.get("task_relevance"),
                    entry.get("emotional_valence"),
                    entry.get("thalamus_overall"),
                    entry.get("created_at", datetime.now(timezone.utc)),
                    entry.get("status", "active"),
                    entry.get("confidence_score"),
                    UUID(str(entry["session_ref"])) if entry.get("session_ref") else None,
                )


async def list_active_for_decay(
    pool: asyncpg.Pool,
    bank_id: str,
    batch_size: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Return active Engrams in buffer or neocortex layer for NCR Decay processing.

    Used by DecayProcessor to iterate over all Engrams that participate in the
    Nightly Consolidation Run. Archived entries are excluded.

    Args:
        pool: asyncpg connection pool.
        bank_id: Required — all queries are bank-scoped.
        batch_size: Maximum number of entries per call.
        offset: Pagination offset for batch processing.

    Returns:
        List of dicts ordered by strength ASC (weakest first — fail-fast on archives).
    """
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM engram_dictionary
            WHERE bank_id = $1
              AND layer IN ('buffer', 'neocortex')
              AND status = 'active'
            ORDER BY strength ASC
            LIMIT $2 OFFSET $3
            """,
            bank_id,
            batch_size,
            offset,
        )
    return [dict(row) for row in rows]


async def list_unconsolidated(
    pool: asyncpg.Pool,
    bank_id: str,
    batch_size: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Return engram_dictionary entries in Working Memory (candidates for Consolidation 1).

    Used by Consolidation1Service to find Engrams eligible for selective
    promotion to the Buffer. Includes both `layer='working'` (new style,
    Epic 24) and `layer IS NULL` (legacy entries created before the
    default-layer fix).

    Args:
        pool: asyncpg connection pool.
        bank_id: Required — all queries are bank-scoped.
        batch_size: Maximum number of entries per call.
        offset: Pagination offset for batch processing.

    Returns:
        List of dicts ordered by created_at ASC (oldest first).
    """
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM engram_dictionary
            WHERE bank_id = $1
              AND (layer IS NULL OR layer = 'working')
              AND status = 'active'
            ORDER BY created_at ASC
            LIMIT $2 OFFSET $3
            """,
            bank_id,
            batch_size,
            offset,
        )
    return [dict(row) for row in rows]


async def list_buffer_for_strengthen(
    pool: asyncpg.Pool,
    bank_id: str,
    batch_size: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Return active buffer-layer Engrams for NCR Phase 2 (Strengthen) processing.

    Used by StrengthenProcessor to find candidates for neocortex promotion.
    Only buffer Engrams are eligible for promotion; neocortex Engrams are already
    consolidated.

    Args:
        pool: asyncpg connection pool.
        bank_id: Required — all queries are bank-scoped.
        batch_size: Maximum number of entries per call.
        offset: Pagination offset for batch processing.

    Returns:
        List of dicts ordered by strength DESC (strongest first — promote best candidates).
    """
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM engram_dictionary
            WHERE bank_id = $1
              AND layer = 'buffer'
              AND status = 'active'
            ORDER BY strength DESC
            LIMIT $2 OFFSET $3
            """,
            bank_id,
            batch_size,
            offset,
        )
    return [dict(row) for row in rows]


async def increment_ncr_cycles(pool: asyncpg.Pool, engram_id: str) -> None:
    """
    Increment ncr_cycles_survived by 1 for an Engram that survived a NCR cycle.

    Called by NCR Phase 2 for all non-archived buffer/neocortex Engrams.
    Separate from update_entry for atomicity and performance (no SELECT needed).

    Args:
        pool: asyncpg connection pool.
        engram_id: UUID string of the Engram.
    """
    async with acquire_with_retry(pool) as conn:
        await conn.execute(
            """
            UPDATE engram_dictionary
            SET ncr_cycles_survived = ncr_cycles_survived + 1
            WHERE engram_id = $1
            """,
            UUID(engram_id),
        )


async def update_strength(pool: asyncpg.Pool, engram_id: str, new_strength: float) -> None:
    """Convenience method to update only the strength field."""
    await update_entry(pool, engram_id, {"strength": new_strength})


async def update_access(pool: asyncpg.Pool, engram_id: str) -> None:
    """Increment access_count and set last_accessed to now."""
    async with acquire_with_retry(pool) as conn:
        await conn.execute(
            """
            UPDATE engram_dictionary
            SET access_count = access_count + 1,
                last_accessed = $2
            WHERE engram_id = $1
            """,
            UUID(engram_id),
            datetime.now(timezone.utc),
        )


async def increment_bank_op_count(pool: asyncpg.Pool, bank_id: str) -> int:
    """Atomically increment banks.op_count and return the new value.

    Called once per retain_batch + once per recall_async to track the total
    session-cycle count for composite strength scoring (Epic 24).
    """
    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(
            """
            UPDATE banks
            SET op_count = op_count + 1
            WHERE bank_id = $1
            RETURNING op_count
            """,
            bank_id,
        )
        return row["op_count"] if row else 0


async def get_bank_op_count(pool: asyncpg.Pool, bank_id: str) -> int:
    """Read the current op_count for a bank (non-mutating)."""
    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(
            "SELECT op_count FROM banks WHERE bank_id = $1",
            bank_id,
        )
        return row["op_count"] if row else 0


async def increment_bank_session_count(pool: asyncpg.Pool, bank_id: str) -> int:
    """Atomically increment banks.session_count and return the new value.

    Called once per session close (Epic 24 Story 01) — the new aging metric.
    Idempotency is enforced by the caller: MemoryEngine.end_session_async runs
    this only after SessionManager.end_session() has successfully popped the
    session state, so a double-close raises KeyError before reaching this.
    """
    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(
            """
            UPDATE banks
            SET session_count = session_count + 1
            WHERE bank_id = $1
            RETURNING session_count
            """,
            bank_id,
        )
        return row["session_count"] if row else 0


async def get_bank_session_count(pool: asyncpg.Pool, bank_id: str) -> int:
    """Read the current session_count for a bank (non-mutating)."""
    async with acquire_with_retry(pool) as conn:
        row = await conn.fetchrow(
            "SELECT session_count FROM banks WHERE bank_id = $1",
            bank_id,
        )
        return row["session_count"] if row else 0
