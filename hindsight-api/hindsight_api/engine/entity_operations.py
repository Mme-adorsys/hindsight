"""
EntityOperations — Entity observation and state management.

Extracted from MemoryEngine (God Object decomposition).
All methods delegate shared infrastructure to EngineContext.

Covers:
  - Entity observation retrieval (single and batch)
  - Entity listing, state, and full details
  - Observation regeneration (sync helper + background task handler)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from .response_models import EntityObservation, EntityState, MemoryFact
from .retain import embedding_utils
from .search import observation_utils
from .utils import acquire_with_retry, fq_table, utcnow

if TYPE_CHECKING:
    from hindsight_api.models import RequestContext

    from .engine_context import EngineContext

logger = logging.getLogger(__name__)


class EntityOperations:
    """Entity observation and state management methods."""

    def __init__(self, ctx: "EngineContext") -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Entity observation retrieval
    # ------------------------------------------------------------------

    async def get_entity_observations(
        self,
        bank_id: str,
        entity_id: str,
        *,
        limit: int = 10,
        request_context: "RequestContext",
    ) -> list[Any]:
        """Get observations linked to an entity."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT mu.text, mu.mentioned_at
                FROM {fq_table("memory_units")} mu
                JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
                WHERE mu.bank_id = $1
                  AND mu.fact_type = 'observation'
                  AND ue.entity_id = $2
                ORDER BY mu.mentioned_at DESC
                LIMIT $3
                """,
                bank_id,
                uuid.UUID(entity_id),
                limit,
            )
            return [
                EntityObservation(
                    text=row["text"],
                    mentioned_at=row["mentioned_at"].isoformat() if row["mentioned_at"] else None,
                )
                for row in rows
            ]

    async def get_entity_observations_batch(
        self,
        bank_id: str,
        entity_ids: list[str],
        *,
        limit_per_entity: int = 5,
        request_context: "RequestContext",
    ) -> dict[str, list[Any]]:
        """Get observations for multiple entities in a single query."""
        if not entity_ids:
            return {}
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                WITH ranked AS (
                    SELECT
                        ue.entity_id,
                        mu.text,
                        mu.mentioned_at,
                        ROW_NUMBER() OVER (PARTITION BY ue.entity_id ORDER BY mu.mentioned_at DESC) as rn
                    FROM {fq_table("memory_units")} mu
                    JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
                    WHERE mu.bank_id = $1
                      AND mu.fact_type = 'observation'
                      AND ue.entity_id = ANY($2::uuid[])
                )
                SELECT entity_id, text, mentioned_at
                FROM ranked
                WHERE rn <= $3
                ORDER BY entity_id, rn
                """,
                bank_id,
                [uuid.UUID(eid) for eid in entity_ids],
                limit_per_entity,
            )
            result: dict[str, list[Any]] = {eid: [] for eid in entity_ids}
            for row in rows:
                entity_id_str = str(row["entity_id"])
                mentioned_at = row["mentioned_at"].isoformat() if row["mentioned_at"] else None
                result[entity_id_str].append(EntityObservation(text=row["text"], mentioned_at=mentioned_at))
            return result

    # ------------------------------------------------------------------
    # Entity listing and state
    # ------------------------------------------------------------------

    async def list_entities(
        self,
        bank_id: str,
        *,
        limit: int = 100,
        request_context: "RequestContext",
    ) -> list[dict[str, Any]]:
        """List all entities for a bank."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, canonical_name, mention_count, first_seen, last_seen, metadata
                FROM {fq_table("entities")}
                WHERE bank_id = $1
                ORDER BY mention_count DESC, last_seen DESC
                LIMIT $2
                """,
                bank_id,
                limit,
            )
            entities = []
            for row in rows:
                metadata = row["metadata"]
                if metadata is None:
                    metadata = {}
                elif isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {}
                entities.append(
                    {
                        "id": str(row["id"]),
                        "canonical_name": row["canonical_name"],
                        "mention_count": row["mention_count"],
                        "first_seen": row["first_seen"].isoformat() if row["first_seen"] else None,
                        "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                        "metadata": metadata,
                    }
                )
            return entities

    async def get_entity_state(
        self,
        bank_id: str,
        entity_id: str,
        entity_name: str,
        *,
        limit: int = 10,
        request_context: "RequestContext",
    ) -> EntityState:
        """Get the current state (mental model) of an entity."""
        observations = await self.get_entity_observations(
            bank_id, entity_id, limit=limit, request_context=request_context
        )
        return EntityState(entity_id=entity_id, canonical_name=entity_name, observations=observations)

    async def get_entity(
        self,
        bank_id: str,
        entity_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any] | None:
        """Get entity details including metadata and observations."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            entity_row = await conn.fetchrow(
                f"""
                SELECT id, canonical_name, mention_count, first_seen, last_seen, metadata
                FROM {fq_table("entities")}
                WHERE bank_id = $1 AND id = $2
                """,
                bank_id,
                uuid.UUID(entity_id),
            )
        if not entity_row:
            return None
        observations = await self.get_entity_observations(bank_id, entity_id, limit=20, request_context=request_context)
        return {
            "id": str(entity_row["id"]),
            "canonical_name": entity_row["canonical_name"],
            "mention_count": entity_row["mention_count"],
            "first_seen": entity_row["first_seen"].isoformat() if entity_row["first_seen"] else None,
            "last_seen": entity_row["last_seen"].isoformat() if entity_row["last_seen"] else None,
            "metadata": entity_row["metadata"] or {},
            "observations": observations,
        }

    # ------------------------------------------------------------------
    # Observation regeneration
    # ------------------------------------------------------------------

    async def regenerate_entity_observations(
        self,
        bank_id: str,
        entity_id: str,
        entity_name: str,
        *,
        version: str | None = None,
        conn=None,
        request_context: "RequestContext",
    ) -> None:
        """
        Regenerate observations for an entity.

        Steps:
        1. Check version for deduplication (if provided)
        2. Fetch all facts mentioning the entity
        3. LLM-synthesise observations (no personality)
        4. Delete old observations for this entity
        5. Store new observations linked to the entity
        """
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        entity_uuid = uuid.UUID(entity_id)

        async def fetch_with_conn(query, *args):
            if conn is not None:
                return await conn.fetch(query, *args)
            async with acquire_with_retry(pool) as acquired_conn:
                return await acquired_conn.fetch(query, *args)

        async def fetchval_with_conn(query, *args):
            if conn is not None:
                return await conn.fetchval(query, *args)
            async with acquire_with_retry(pool) as acquired_conn:
                return await acquired_conn.fetchval(query, *args)

        # Step 1: Version check for deduplication
        if version:
            current_last_seen = await fetchval_with_conn(
                f"SELECT last_seen FROM {fq_table('entities')} WHERE id = $1 AND bank_id = $2",
                entity_uuid,
                bank_id,
            )
            if current_last_seen and current_last_seen.isoformat() != version:
                return []

        # Step 2: Fetch facts mentioning this entity
        rows = await fetch_with_conn(
            f"""
            SELECT mu.id, mu.text, mu.context, mu.occurred_start, mu.fact_type
            FROM {fq_table("memory_units")} mu
            JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
            WHERE mu.bank_id = $1
              AND ue.entity_id = $2
              AND mu.fact_type IN ('world', 'experience')
            ORDER BY mu.occurred_start DESC
            LIMIT 50
            """,
            bank_id,
            entity_uuid,
        )
        if not rows:
            return []

        facts = [
            MemoryFact(
                id=str(row["id"]),
                text=row["text"],
                fact_type=row["fact_type"],
                context=row["context"],
                occurred_start=row["occurred_start"].isoformat() if row["occurred_start"] else None,
            )
            for row in rows
        ]

        # Step 3: Extract observations using LLM
        observations = await observation_utils.extract_observations_from_facts(
            self._ctx.llm_registry.get_llm("retain", "observation_synthesis"), entity_name, facts
        )
        if not observations:
            return []

        # Steps 4–5: Delete old + insert new observations
        async def do_db_operations(db_conn):
            await db_conn.execute(
                f"""
                DELETE FROM {fq_table("memory_units")}
                WHERE id IN (
                    SELECT mu.id
                    FROM {fq_table("memory_units")} mu
                    JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
                    WHERE mu.bank_id = $1
                      AND mu.fact_type = 'observation'
                      AND ue.entity_id = $2
                )
                """,
                bank_id,
                entity_uuid,
            )
            embeddings = await embedding_utils.generate_embeddings_batch(self._ctx.embeddings, observations)
            current_time = utcnow()
            created_ids = []
            for obs_text, embedding in zip(observations, embeddings):
                result = await db_conn.fetchrow(
                    f"""
                    INSERT INTO {fq_table("memory_units")} (
                        bank_id, text, embedding, context, event_date,
                        occurred_start, occurred_end, mentioned_at,
                        fact_type, access_count
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'observation', 0)
                    RETURNING id
                    """,
                    bank_id,
                    obs_text,
                    str(embedding),
                    f"observation about {entity_name}",
                    current_time,
                    current_time,
                    current_time,
                    current_time,
                )
                obs_id = str(result["id"])
                created_ids.append(obs_id)
                await db_conn.execute(
                    f"INSERT INTO {fq_table('unit_entities')} (unit_id, entity_id) VALUES ($1, $2)",
                    uuid.UUID(obs_id),
                    entity_uuid,
                )
            return created_ids

        if conn is not None:
            return await do_db_operations(conn)
        else:
            async with acquire_with_retry(pool) as acquired_conn:
                async with acquired_conn.transaction():
                    return await do_db_operations(acquired_conn)

    async def _regenerate_observations_sync(
        self,
        bank_id: str,
        entity_ids: list[str],
        min_facts: int | None = None,
        conn=None,
        request_context: "RequestContext | None" = None,
    ) -> None:
        """
        Regenerate observations for entities synchronously (called during retain).

        Processes entities in PARALLEL for faster execution.
        """
        if not bank_id or not entity_ids:
            return

        if min_facts is None:
            from ..config import get_config

            min_facts = get_config().observation_min_facts

        entity_uuids = [uuid.UUID(eid) if isinstance(eid, str) else eid for eid in entity_ids]

        if conn is not None:
            entity_rows = await conn.fetch(
                f"SELECT id, canonical_name FROM {fq_table('entities')} WHERE id = ANY($1) AND bank_id = $2",
                entity_uuids,
                bank_id,
            )
            entity_names = {row["id"]: row["canonical_name"] for row in entity_rows}
            fact_counts_rows = await conn.fetch(
                f"""
                SELECT ue.entity_id, COUNT(*) as cnt
                FROM {fq_table("unit_entities")} ue
                JOIN {fq_table("memory_units")} mu ON ue.unit_id = mu.id
                WHERE ue.entity_id = ANY($1) AND mu.bank_id = $2
                GROUP BY ue.entity_id
                """,
                entity_uuids,
                bank_id,
            )
            entity_fact_counts = {row["entity_id"]: row["cnt"] for row in fact_counts_rows}
        else:
            pool = await self._ctx.get_pool()
            async with pool.acquire() as acquired_conn:
                entity_rows = await acquired_conn.fetch(
                    f"SELECT id, canonical_name FROM {fq_table('entities')} WHERE id = ANY($1) AND bank_id = $2",
                    entity_uuids,
                    bank_id,
                )
                entity_names = {row["id"]: row["canonical_name"] for row in entity_rows}
                fact_counts_rows = await acquired_conn.fetch(
                    f"""
                    SELECT ue.entity_id, COUNT(*) as cnt
                    FROM {fq_table("unit_entities")} ue
                    JOIN {fq_table("memory_units")} mu ON ue.unit_id = mu.id
                    WHERE ue.entity_id = ANY($1) AND mu.bank_id = $2
                    GROUP BY ue.entity_id
                    """,
                    entity_uuids,
                    bank_id,
                )
                entity_fact_counts = {row["entity_id"]: row["cnt"] for row in fact_counts_rows}

        entities_to_process = []
        for entity_id in entity_ids:
            entity_uuid = uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
            if entity_uuid not in entity_names:
                continue
            if entity_fact_counts.get(entity_uuid, 0) >= min_facts:
                entities_to_process.append((entity_id, entity_names[entity_uuid]))

        if not entities_to_process:
            return

        from hindsight_api.models import RequestContext as RC

        ctx = request_context if request_context is not None else RC()

        async def process_entity(entity_id: str, entity_name: str):
            try:
                await self.regenerate_entity_observations(
                    bank_id, entity_id, entity_name, version=None, conn=conn, request_context=ctx
                )
            except Exception as e:
                logger.error(f"[OBSERVATIONS] Error processing entity {entity_id}: {e}")

        await asyncio.gather(*[process_entity(eid, name) for eid, name in entities_to_process])

    async def _handle_regenerate_observations(self, task_dict: dict[str, Any]):
        """Background task handler for observation regeneration."""
        bank_id = task_dict.get("bank_id")
        from hindsight_api.models import RequestContext

        internal_context = RequestContext()

        if "entity_ids" in task_dict:
            entity_ids = task_dict.get("entity_ids", [])
            min_facts = task_dict.get("min_facts", 5)
            if not bank_id or not entity_ids:
                raise ValueError(f"[OBSERVATIONS] Missing required fields in task: {task_dict}")

            pool = await self._ctx.get_pool()
            async with pool.acquire() as conn:
                for entity_id in entity_ids:
                    try:
                        entity_uuid = uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
                        entity_exists = await conn.fetchrow(
                            f"SELECT canonical_name FROM {fq_table('entities')} WHERE id = $1 AND bank_id = $2",
                            entity_uuid,
                            bank_id,
                        )
                        if not entity_exists:
                            logger.debug(f"[OBSERVATIONS] Entity {entity_id} not yet in bank {bank_id}, skipping")
                            continue
                        entity_name = entity_exists["canonical_name"]
                        fact_count = (
                            await conn.fetchval(
                                f"SELECT COUNT(*) FROM {fq_table('unit_entities')} WHERE entity_id = $1",
                                entity_uuid,
                            )
                            or 0
                        )
                        if fact_count >= min_facts:
                            await self.regenerate_entity_observations(
                                bank_id, entity_id, entity_name, version=None, request_context=internal_context
                            )
                        else:
                            logger.debug(
                                f"[OBSERVATIONS] Skipping {entity_name} ({fact_count} facts < {min_facts} threshold)"
                            )
                    except Exception as e:
                        logger.error(f"[OBSERVATIONS] Error processing entity {entity_id}: {e}")
                        continue
        else:
            entity_id = task_dict.get("entity_id")
            entity_name = task_dict.get("entity_name")
            version = task_dict.get("version")
            if not all([bank_id, entity_id, entity_name]):
                raise ValueError(f"[OBSERVATIONS] Missing required fields in task: {task_dict}")
            assert isinstance(bank_id, str) and isinstance(entity_id, str) and isinstance(entity_name, str)
            await self.regenerate_entity_observations(
                bank_id, entity_id, entity_name, version=version, request_context=internal_context
            )
