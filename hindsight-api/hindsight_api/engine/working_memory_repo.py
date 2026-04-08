"""
Working Memory Repository — asyncpg CRUD for persistent working memory.

Loads and saves the WorkingMemory state (JSONB) from/to the working_memory
PostgreSQL table. One row per bank_id.

Pattern: mirrors BankModelConfigRepo (engine/bank_model_config.py) — no
cache, asyncpg connections, fq_table() for schema safety.

Bio mapping: no direct bio analogue — this is the persistence layer for
the PFC working memory that survives session boundaries.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .session.working_memory import WorkingMemory
from .utils import fq_table

logger = logging.getLogger(__name__)


class WorkingMemoryRepository:
    """
    CRUD repository for per-bank persistent working memory.

    Each bank has at most one row in the working_memory table. The state is
    stored as JSONB and deserialized via WorkingMemory.from_dict().

    No in-memory cache: WorkingMemory is loaded once at session start and
    saved at session end (and periodically). The infrequent access pattern
    does not benefit from a TTL cache.

    Usage::

        repo = WorkingMemoryRepository()
        wm = await repo.load(conn, bank_id)
        # ... modify wm ...
        await repo.save(conn, wm)
    """

    async def load(self, conn: Any, bank_id: str) -> WorkingMemory:
        """
        Load WorkingMemory for a bank from the database.

        Returns WorkingMemory.default(bank_id) if no row exists (new bank).

        Args:
            conn: asyncpg connection.
            bank_id: Target memory bank.
        """
        row = await conn.fetchrow(
            f"SELECT state, schema_version FROM {fq_table('working_memory')} WHERE bank_id = $1",
            bank_id,
        )
        if row is None:
            logger.debug("WorkingMemoryRepo: no row for bank_id=%r — returning default", bank_id)
            return WorkingMemory.default(bank_id)

        state: dict[str, Any] = dict(row["state"]) if row["state"] else {}
        # Inject bank_id and schema_version so from_dict has all it needs
        state["bank_id"] = bank_id
        state["schema_version"] = row["schema_version"]
        wm = WorkingMemory.from_dict(state)
        logger.debug(
            "WorkingMemoryRepo: loaded bank_id=%r schema_v=%d goals=%d engrams_focus=%d",
            bank_id,
            wm.schema_version,
            len(wm.goal_stack),
            len(wm.active_engrams.focus),
        )
        return wm

    async def save(self, conn: Any, wm: WorkingMemory) -> None:
        """
        Upsert WorkingMemory to the database (INSERT … ON CONFLICT UPDATE).

        Args:
            conn: asyncpg connection.
            wm: WorkingMemory instance to persist.
        """
        state_dict = wm.to_dict()
        # Remove top-level keys stored as columns — only the content goes in JSONB
        state_dict.pop("bank_id", None)
        state_dict.pop("schema_version", None)
        state_json = json.dumps(state_dict)

        await conn.execute(
            f"""
            INSERT INTO {fq_table("working_memory")} (bank_id, state, schema_version, updated_at)
            VALUES ($1, $2::jsonb, $3, NOW())
            ON CONFLICT (bank_id) DO UPDATE
                SET state = $2::jsonb,
                    schema_version = $3,
                    updated_at = NOW()
            """,
            wm.bank_id,
            state_json,
            wm.schema_version,
        )
        logger.debug(
            "WorkingMemoryRepo: saved bank_id=%r schema_v=%d goals=%d",
            wm.bank_id,
            wm.schema_version,
            len(wm.goal_stack),
        )

    async def exists(self, conn: Any, bank_id: str) -> bool:
        """
        Return True if a working_memory row exists for the given bank_id.

        Args:
            conn: asyncpg connection.
            bank_id: Target memory bank.
        """
        row = await conn.fetchrow(
            f"SELECT 1 FROM {fq_table('working_memory')} WHERE bank_id = $1",
            bank_id,
        )
        return row is not None
