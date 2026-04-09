"""
AdminOperations — CRUD, Bank, and Operations management.

Extracted from MemoryEngine (God Object decomposition).
All methods delegate shared infrastructure to EngineContext.

Covers:
  - Document / memory-unit / bank deletion
  - List and search views (memory_units, documents, banks, operations)
  - Graph data for visualisation
  - Bank profile / disposition / background management
  - Async-operation lifecycle (list, cancel, submit)
  - Bank statistics
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from .retain import bank_utils
from .utils import acquire_with_retry, fq_table

if TYPE_CHECKING:
    from hindsight_api.models import RequestContext

    from .engine_context import EngineContext

logger = logging.getLogger(__name__)


class AdminOperations:
    """CRUD, bank-management, and async-operation methods."""

    def __init__(self, ctx: "EngineContext") -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    async def get_document(
        self,
        document_id: str,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any] | None:
        """Retrieve document metadata and statistics."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            doc = await conn.fetchrow(
                f"""
                SELECT d.id, d.bank_id, d.original_text, d.content_hash,
                       d.created_at, d.updated_at, COUNT(mu.id) as unit_count
                FROM {fq_table("documents")} d
                LEFT JOIN {fq_table("memory_units")} mu ON mu.document_id = d.id
                WHERE d.id = $1 AND d.bank_id = $2
                GROUP BY d.id, d.bank_id, d.original_text, d.content_hash, d.created_at, d.updated_at
                """,
                document_id,
                bank_id,
            )
            if not doc:
                return None
            return {
                "id": doc["id"],
                "bank_id": doc["bank_id"],
                "original_text": doc["original_text"],
                "content_hash": doc["content_hash"],
                "memory_unit_count": doc["unit_count"],
                "created_at": doc["created_at"].isoformat() if doc["created_at"] else None,
                "updated_at": doc["updated_at"].isoformat() if doc["updated_at"] else None,
            }

    async def delete_document(
        self,
        document_id: str,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, int]:
        """Delete a document and all its associated memory units and links."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            async with conn.transaction():
                units_count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {fq_table('memory_units')} WHERE document_id = $1", document_id
                )
                deleted = await conn.fetchval(
                    f"DELETE FROM {fq_table('documents')} WHERE id = $1 AND bank_id = $2 RETURNING id",
                    document_id,
                    bank_id,
                )
                return {"document_deleted": 1 if deleted else 0, "memory_units_deleted": units_count if deleted else 0}

    # ------------------------------------------------------------------
    # Memory unit operations
    # ------------------------------------------------------------------

    async def delete_memory_unit(
        self,
        unit_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Delete a single memory unit and all its associated links."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            async with conn.transaction():
                deleted = await conn.fetchval(
                    f"DELETE FROM {fq_table('memory_units')} WHERE id = $1 RETURNING id", unit_id
                )
                return {
                    "success": deleted is not None,
                    "unit_id": str(deleted) if deleted else None,
                    "message": "Memory unit and all its links deleted successfully"
                    if deleted
                    else "Memory unit not found",
                }

    # ------------------------------------------------------------------
    # Bank deletion
    # ------------------------------------------------------------------

    async def delete_bank(
        self,
        bank_id: str,
        fact_type: str | None = None,
        *,
        request_context: "RequestContext",
    ) -> dict[str, int]:
        """Delete all data for a bank (optionally filtered by fact_type)."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL transaction_read_only TO off")
                try:
                    if fact_type:
                        units_count = await conn.fetchval(
                            f"SELECT COUNT(*) FROM {fq_table('memory_units')} WHERE bank_id = $1 AND fact_type = $2",
                            bank_id,
                            fact_type,
                        )
                        await conn.execute(
                            f"DELETE FROM {fq_table('memory_units')} WHERE bank_id = $1 AND fact_type = $2",
                            bank_id,
                            fact_type,
                        )
                        return {"memory_units_deleted": units_count, "entities_deleted": 0}
                    else:
                        units_count = await conn.fetchval(
                            f"SELECT COUNT(*) FROM {fq_table('memory_units')} WHERE bank_id = $1", bank_id
                        )
                        entities_count = await conn.fetchval(
                            f"SELECT COUNT(*) FROM {fq_table('entities')} WHERE bank_id = $1", bank_id
                        )
                        documents_count = await conn.fetchval(
                            f"SELECT COUNT(*) FROM {fq_table('documents')} WHERE bank_id = $1", bank_id
                        )
                        await conn.execute(f"DELETE FROM {fq_table('documents')} WHERE bank_id = $1", bank_id)
                        await conn.execute(f"DELETE FROM {fq_table('memory_units')} WHERE bank_id = $1", bank_id)
                        await conn.execute(f"DELETE FROM {fq_table('entities')} WHERE bank_id = $1", bank_id)
                        await conn.execute(f"DELETE FROM {fq_table('banks')} WHERE bank_id = $1", bank_id)
                        return {
                            "memory_units_deleted": units_count,
                            "entities_deleted": entities_count,
                            "documents_deleted": documents_count,
                            "bank_deleted": True,
                        }
                except Exception as e:
                    raise Exception(f"Failed to delete agent data: {str(e)}")

    # ------------------------------------------------------------------
    # Graph data
    # ------------------------------------------------------------------

    async def get_graph_data(
        self,
        bank_id: str | None = None,
        fact_type: str | None = None,
        *,
        limit: int = 1000,
        request_context: "RequestContext",
    ):
        """Get graph data for visualisation."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            query_conditions = []
            query_params = []
            param_count = 0

            if bank_id:
                param_count += 1
                query_conditions.append(f"bank_id = ${param_count}")
                query_params.append(bank_id)

            if fact_type:
                param_count += 1
                query_conditions.append(f"fact_type = ${param_count}")
                query_params.append(fact_type)

            where_clause = "WHERE " + " AND ".join(query_conditions) if query_conditions else ""

            total_count_result = await conn.fetchrow(
                f"SELECT COUNT(*) as total FROM {fq_table('memory_units')} {where_clause}", *query_params
            )
            total_count = total_count_result["total"] if total_count_result else 0

            param_count += 1
            units = await conn.fetch(
                f"""
                SELECT
                    mu.id, mu.text, mu.event_date, mu.context, mu.occurred_start, mu.occurred_end,
                    mu.mentioned_at, mu.document_id, mu.chunk_id, mu.fact_type,
                    ed.strength, ed.layer, ed.access_count,
                    ed.thalamus_overall, ed.novelty, ed.surprise,
                    ed.task_relevance, ed.emotional_valence
                FROM {fq_table("memory_units")} mu
                LEFT JOIN {fq_table("engram_dictionary")} ed ON ed.engram_id = mu.id
                {where_clause.replace("bank_id", "mu.bank_id").replace("fact_type", "mu.fact_type")}
                ORDER BY mu.mentioned_at DESC NULLS LAST, mu.event_date DESC
                LIMIT ${param_count}
                """,
                *query_params,
                limit,
            )

            unit_ids = [row["id"] for row in units]
            if unit_ids:
                links = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON (LEAST(ml.from_unit_id, ml.to_unit_id), GREATEST(ml.from_unit_id, ml.to_unit_id), ml.link_type, COALESCE(ml.entity_id, '00000000-0000-0000-0000-000000000000'::uuid))
                        ml.from_unit_id, ml.to_unit_id, ml.link_type, ml.weight, e.canonical_name as entity_name
                    FROM {fq_table("memory_links")} ml
                    LEFT JOIN {fq_table("entities")} e ON ml.entity_id = e.id
                    WHERE ml.from_unit_id = ANY($1::uuid[]) AND ml.to_unit_id = ANY($1::uuid[])
                    ORDER BY LEAST(ml.from_unit_id, ml.to_unit_id), GREATEST(ml.from_unit_id, ml.to_unit_id), ml.link_type, COALESCE(ml.entity_id, '00000000-0000-0000-0000-000000000000'::uuid), ml.weight DESC
                    """,
                    unit_ids,
                )
            else:
                links = []

            unit_entities = await conn.fetch(f"""
                SELECT ue.unit_id, e.canonical_name
                FROM {fq_table("unit_entities")} ue
                JOIN {fq_table("entities")} e ON ue.entity_id = e.id
                ORDER BY ue.unit_id
            """)

        entity_map: dict[Any, list[str]] = {}
        for row in unit_entities:
            entity_map.setdefault(row["unit_id"], []).append(row["canonical_name"])

        nodes = []
        for row in units:
            unit_id = row["id"]
            entities = entity_map.get(unit_id, [])
            entity_count = len(entities)
            color = "#e0e0e0" if entity_count == 0 else "#90caf9" if entity_count == 1 else "#42a5f5"
            text = row["text"]
            event_date = row["event_date"]
            nodes.append(
                {
                    "data": {
                        "id": str(unit_id),
                        "label": f"{text[:30]}..." if len(text) > 30 else text,
                        "text": text,
                        "date": event_date.isoformat() if event_date else "",
                        "context": row["context"] if row["context"] else "",
                        "entities": ", ".join(entities) if entities else "None",
                        "color": color,
                    }
                }
            )

        edges = []
        for row in links:
            from_id = str(row["from_unit_id"])
            to_id = str(row["to_unit_id"])
            link_type = row["link_type"]
            if link_type == "temporal":
                color, line_style = "#00bcd4", "dashed"
            elif link_type == "semantic":
                color, line_style = "#ff69b4", "solid"
            elif link_type == "entity":
                color, line_style = "#ffd700", "solid"
            else:
                color, line_style = "#999999", "solid"
            edges.append(
                {
                    "data": {
                        "id": f"{from_id}-{to_id}-{link_type}",
                        "source": from_id,
                        "target": to_id,
                        "linkType": link_type,
                        "weight": row["weight"],
                        "entityName": row["entity_name"] if row["entity_name"] else "",
                        "color": color,
                        "lineStyle": line_style,
                    }
                }
            )

        table_rows = []
        for row in units:
            unit_id = row["id"]
            entities = entity_map.get(unit_id, [])
            thalamus_scores = None
            if row["thalamus_overall"] is not None:
                thalamus_scores = {
                    "overall": row["thalamus_overall"],
                    "novelty": row["novelty"],
                    "surprise": row["surprise"],
                    "task_relevance": row["task_relevance"],
                    "emotional_valence": row["emotional_valence"],
                }
            table_rows.append(
                {
                    "id": str(unit_id),
                    "text": row["text"],
                    "context": row["context"] if row["context"] else "N/A",
                    "occurred_start": row["occurred_start"].isoformat() if row["occurred_start"] else None,
                    "occurred_end": row["occurred_end"].isoformat() if row["occurred_end"] else None,
                    "mentioned_at": row["mentioned_at"].isoformat() if row["mentioned_at"] else None,
                    "date": row["event_date"].strftime("%Y-%m-%d %H:%M") if row["event_date"] else "N/A",
                    "entities": ", ".join(entities) if entities else "None",
                    "document_id": row["document_id"],
                    "chunk_id": row["chunk_id"] if row["chunk_id"] else None,
                    "fact_type": row["fact_type"],
                    "strength": float(row["strength"]) if row["strength"] is not None else None,
                    "layer": row["layer"],
                    "access_count": row["access_count"],
                    "thalamus_scores": thalamus_scores,
                }
            )

        return {"nodes": nodes, "edges": edges, "table_rows": table_rows, "total_units": total_count, "limit": limit}

    # ------------------------------------------------------------------
    # List views
    # ------------------------------------------------------------------

    async def list_memory_units(
        self,
        bank_id: str,
        *,
        fact_type: str | None = None,
        search_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
        request_context: "RequestContext",
    ):
        """List memory units for table view with optional full-text search."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            query_conditions: list[str] = []
            query_params: list[Any] = []
            param_count = 0

            if bank_id:
                param_count += 1
                query_conditions.append(f"bank_id = ${param_count}")
                query_params.append(bank_id)

            if fact_type:
                param_count += 1
                query_conditions.append(f"fact_type = ${param_count}")
                query_params.append(fact_type)

            if search_query:
                param_count += 1
                query_conditions.append(f"(text ILIKE ${param_count} OR context ILIKE ${param_count})")
                query_params.append(f"%{search_query}%")

            where_clause = "WHERE " + " AND ".join(query_conditions) if query_conditions else ""

            count_result = await conn.fetchrow(
                f"SELECT COUNT(*) as total FROM {fq_table('memory_units')} {where_clause}", *query_params
            )
            total = count_result["total"]

            param_count += 1
            limit_param = f"${param_count}"
            query_params.append(limit)
            param_count += 1
            offset_param = f"${param_count}"
            query_params.append(offset)

            units = await conn.fetch(
                f"""
                SELECT
                    mu.id, mu.text, mu.event_date, mu.context, mu.fact_type,
                    mu.mentioned_at, mu.occurred_start, mu.occurred_end, mu.chunk_id,
                    ed.strength, ed.layer, ed.access_count,
                    ed.thalamus_overall, ed.novelty, ed.surprise,
                    ed.task_relevance, ed.emotional_valence,
                    ed.tags, ed.expectation, ed.outcome,
                    ed.session_mode, ed.task_context, ed.retain_context
                FROM {fq_table("memory_units")} mu
                LEFT JOIN {fq_table("engram_dictionary")} ed ON ed.engram_id = mu.id
                {where_clause.replace("bank_id", "mu.bank_id").replace("fact_type", "mu.fact_type")}
                ORDER BY mu.mentioned_at DESC NULLS LAST, mu.created_at DESC
                LIMIT {limit_param} OFFSET {offset_param}
                """,
                *query_params,
            )

            if units:
                unit_ids = [row["id"] for row in units]
                unit_entities = await conn.fetch(
                    f"""
                    SELECT ue.unit_id, e.canonical_name
                    FROM {fq_table("unit_entities")} ue
                    JOIN {fq_table("entities")} e ON ue.entity_id = e.id
                    WHERE ue.unit_id = ANY($1::uuid[])
                    ORDER BY ue.unit_id
                    """,
                    unit_ids,
                )
            else:
                unit_entities = []

            entity_map: dict[Any, list[str]] = {}
            for row in unit_entities:
                entity_map.setdefault(row["unit_id"], []).append(row["canonical_name"])

            items = []
            for row in units:
                unit_id = row["id"]
                entities = entity_map.get(unit_id, [])
                thalamus_scores = None
                if row["thalamus_overall"] is not None:
                    thalamus_scores = {
                        "overall": row["thalamus_overall"],
                        "novelty": row["novelty"],
                        "surprise": row["surprise"],
                        "task_relevance": row["task_relevance"],
                        "emotional_valence": row["emotional_valence"],
                    }

                # retain_context comes back from asyncpg as a string for JSONB
                # columns in some driver versions — normalise to dict here.
                retain_context_raw = row["retain_context"]
                if isinstance(retain_context_raw, str):
                    import json as _json

                    try:
                        retain_context_raw = _json.loads(retain_context_raw)
                    except Exception:
                        retain_context_raw = None

                items.append(
                    {
                        "id": str(unit_id),
                        "text": row["text"],
                        "context": row["context"] if row["context"] else "",
                        "date": row["event_date"].isoformat() if row["event_date"] else "",
                        "fact_type": row["fact_type"],
                        "mentioned_at": row["mentioned_at"].isoformat() if row["mentioned_at"] else None,
                        "occurred_start": row["occurred_start"].isoformat() if row["occurred_start"] else None,
                        "occurred_end": row["occurred_end"].isoformat() if row["occurred_end"] else None,
                        "entities": ", ".join(entities) if entities else "",
                        "chunk_id": row["chunk_id"] if row["chunk_id"] else None,
                        "strength": float(row["strength"]) if row["strength"] is not None else None,
                        "layer": row["layer"],
                        "access_count": row["access_count"],
                        "thalamus_scores": thalamus_scores,
                        # Phase A4 — retain provenance (2026-04-09)
                        "tags": row["tags"] if row["tags"] else None,
                        "expectation": row["expectation"],
                        "outcome": row["outcome"],
                        "session_mode": row["session_mode"],
                        "task_context": row["task_context"],
                        "retain_context": retain_context_raw,
                    }
                )

            return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def list_documents(
        self,
        bank_id: str,
        *,
        search_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
        request_context: "RequestContext",
    ):
        """List documents with optional search and pagination."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            query_conditions: list[str] = []
            query_params: list[Any] = []
            param_count = 0

            param_count += 1
            query_conditions.append(f"bank_id = ${param_count}")
            query_params.append(bank_id)

            if search_query:
                param_count += 1
                query_conditions.append(f"id ILIKE ${param_count}")
                query_params.append(f"%{search_query}%")

            where_clause = "WHERE " + " AND ".join(query_conditions) if query_conditions else ""

            count_result = await conn.fetchrow(
                f"SELECT COUNT(*) as total FROM {fq_table('documents')} {where_clause}", *query_params
            )
            total = count_result["total"]

            param_count += 1
            limit_param = f"${param_count}"
            query_params.append(limit)
            param_count += 1
            offset_param = f"${param_count}"
            query_params.append(offset)

            documents = await conn.fetch(
                f"""
                SELECT id, bank_id, content_hash, created_at, updated_at,
                       LENGTH(original_text) as text_length, retain_params
                FROM {fq_table("documents")}
                {where_clause}
                ORDER BY created_at DESC
                LIMIT {limit_param} OFFSET {offset_param}
                """,
                *query_params,
            )

            if documents:
                doc_ids = [(row["id"], row["bank_id"]) for row in documents]
                placeholders = []
                params_for_count: list[Any] = []
                for i, (doc_id, bank_id_val) in enumerate(doc_ids):
                    idx_doc = i * 2 + 1
                    idx_agent = i * 2 + 2
                    placeholders.append(f"(document_id = ${idx_doc} AND bank_id = ${idx_agent})")
                    params_for_count.extend([doc_id, bank_id_val])
                where_clause_count = " OR ".join(placeholders)
                unit_counts = await conn.fetch(
                    f"""
                    SELECT document_id, bank_id, COUNT(*) as unit_count
                    FROM {fq_table("memory_units")}
                    WHERE {where_clause_count}
                    GROUP BY document_id, bank_id
                    """,
                    *params_for_count,
                )
            else:
                unit_counts = []

            count_map = {(row["document_id"], row["bank_id"]): row["unit_count"] for row in unit_counts}

            items = []
            for row in documents:
                doc_id = row["id"]
                bank_id_val = row["bank_id"]
                items.append(
                    {
                        "id": doc_id,
                        "bank_id": bank_id_val,
                        "content_hash": row["content_hash"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else "",
                        "text_length": row["text_length"] or 0,
                        "memory_unit_count": count_map.get((doc_id, bank_id_val), 0),
                        "retain_params": row["retain_params"] if row["retain_params"] else None,
                    }
                )

            return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def get_chunk(
        self,
        chunk_id: str,
        *,
        request_context: "RequestContext",
    ):
        """Get a specific chunk by its ID."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            chunk = await conn.fetchrow(
                f"""
                SELECT chunk_id, document_id, bank_id, chunk_index, chunk_text, created_at
                FROM {fq_table("chunks")}
                WHERE chunk_id = $1
                """,
                chunk_id,
            )
            if not chunk:
                return None
            return {
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "bank_id": chunk["bank_id"],
                "chunk_index": chunk["chunk_index"],
                "chunk_text": chunk["chunk_text"],
                "created_at": chunk["created_at"].isoformat() if chunk["created_at"] else "",
            }

    # ------------------------------------------------------------------
    # Bank profile / disposition / background
    # ------------------------------------------------------------------

    async def get_bank_profile(
        self,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Get bank profile (name, disposition, background). Auto-creates if absent."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        profile = await bank_utils.get_bank_profile(pool, bank_id)
        return {
            "bank_id": bank_id,
            "name": profile["name"],
            "disposition": profile["disposition"],
            "background": profile["background"],
        }

    async def update_bank_disposition(
        self,
        bank_id: str,
        disposition: dict[str, int],
        *,
        request_context: "RequestContext",
    ) -> None:
        """Update bank disposition traits."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        await bank_utils.update_bank_disposition(pool, bank_id, disposition)

    async def merge_bank_background(
        self,
        bank_id: str,
        new_info: str,
        *,
        update_disposition: bool = True,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Merge new background information with existing background using LLM."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        return await bank_utils.merge_bank_background(
            pool, self._ctx.reflect_llm_config, bank_id, new_info, update_disposition
        )

    async def list_banks(
        self,
        *,
        request_context: "RequestContext",
    ) -> list[dict[str, Any]]:
        """List all banks in the system."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        return await bank_utils.list_banks(pool)

    async def update_bank(
        self,
        bank_id: str,
        *,
        name: str | None = None,
        background: str | None = None,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Update bank name and/or background."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            if name is not None:
                await conn.execute(
                    f"UPDATE {fq_table('banks')} SET name = $2, updated_at = NOW() WHERE bank_id = $1",
                    bank_id,
                    name,
                )
            if background is not None:
                await conn.execute(
                    f"UPDATE {fq_table('banks')} SET background = $2, updated_at = NOW() WHERE bank_id = $1",
                    bank_id,
                    background,
                )
        return await self.get_bank_profile(bank_id, request_context=request_context)

    # ------------------------------------------------------------------
    # Bank statistics
    # ------------------------------------------------------------------

    async def get_bank_stats(
        self,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Get statistics about memory nodes and links for a bank."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            node_stats = await conn.fetch(
                f"SELECT fact_type, COUNT(*) as count FROM {fq_table('memory_units')} WHERE bank_id = $1 GROUP BY fact_type",
                bank_id,
            )
            link_stats = await conn.fetch(
                f"""
                SELECT ml.link_type, COUNT(*) as count
                FROM {fq_table("memory_links")} ml
                JOIN {fq_table("memory_units")} mu ON ml.from_unit_id = mu.id
                WHERE mu.bank_id = $1
                GROUP BY ml.link_type
                """,
                bank_id,
            )
            link_fact_type_stats = await conn.fetch(
                f"""
                SELECT mu.fact_type, COUNT(*) as count
                FROM {fq_table("memory_links")} ml
                JOIN {fq_table("memory_units")} mu ON ml.from_unit_id = mu.id
                WHERE mu.bank_id = $1
                GROUP BY mu.fact_type
                """,
                bank_id,
            )
            link_breakdown_stats = await conn.fetch(
                f"""
                SELECT mu.fact_type, ml.link_type, COUNT(*) as count
                FROM {fq_table("memory_links")} ml
                JOIN {fq_table("memory_units")} mu ON ml.from_unit_id = mu.id
                WHERE mu.bank_id = $1
                GROUP BY mu.fact_type, ml.link_type
                """,
                bank_id,
            )
            ops_stats = await conn.fetch(
                f"SELECT status, COUNT(*) as count FROM {fq_table('async_operations')} WHERE bank_id = $1 GROUP BY status",
                bank_id,
            )
            return {
                "bank_id": bank_id,
                "node_counts": {row["fact_type"]: row["count"] for row in node_stats},
                "link_counts": {row["link_type"]: row["count"] for row in link_stats},
                "link_counts_by_fact_type": {row["fact_type"]: row["count"] for row in link_fact_type_stats},
                "link_breakdown": [
                    {"fact_type": row["fact_type"], "link_type": row["link_type"], "count": row["count"]}
                    for row in link_breakdown_stats
                ],
                "operations": {row["status"]: row["count"] for row in ops_stats},
            }

    async def get_engram_stats(
        self,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """
        Aggregated Engram lifecycle statistics for a bank.

        Counts active Engrams grouped by layer (working_memory = layer IS NULL,
        buffer, neocortex) with avg strength per layer, plus a global
        strength distribution (weak < 0.3, moderate 0.3–0.7, strong > 0.7).

        Reads from ``engram_dictionary`` (the canonical hippocampal pointer index,
        Epic 01 ch. 3) via SQL GROUP BY — the existing indices
        ``idx_engram_dictionary_bank_layer_status`` and
        ``idx_engram_dictionary_bank_strength`` make this constant-cost regardless
        of bank size. Only ``status = 'active'`` rows are counted; archived and
        decayed engrams are excluded.

        Returns a dict suitable for the ``GET /engrams/stats`` endpoint response.
        Empty banks return zero counts instead of raising.
        """
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            layer_rows = await conn.fetch(
                f"""
                SELECT
                    COALESCE(layer, 'working_memory') AS layer_name,
                    COUNT(*) AS count,
                    AVG(strength) AS avg_strength
                FROM {fq_table("engram_dictionary")}
                WHERE bank_id = $1 AND status = 'active'
                GROUP BY COALESCE(layer, 'working_memory')
                """,
                bank_id,
            )
            distribution_rows = await conn.fetch(
                f"""
                SELECT
                    CASE
                        WHEN strength < 0.3 THEN 'weak'
                        WHEN strength <= 0.7 THEN 'moderate'
                        ELSE 'strong'
                    END AS bucket,
                    COUNT(*) AS count
                FROM {fq_table("engram_dictionary")}
                WHERE bank_id = $1 AND status = 'active'
                GROUP BY bucket
                """,
                bank_id,
            )

        # Default zero-counts — ensures all three layers/buckets are always present.
        layers: dict[str, dict[str, float | int]] = {
            "working_memory": {"count": 0, "avg_strength": 0.0},
            "buffer": {"count": 0, "avg_strength": 0.0},
            "neocortex": {"count": 0, "avg_strength": 0.0},
        }
        for row in layer_rows:
            name = row["layer_name"]
            if name in layers:
                layers[name] = {
                    "count": int(row["count"]),
                    "avg_strength": float(row["avg_strength"] or 0.0),
                }

        strength_distribution: dict[str, int] = {"weak": 0, "moderate": 0, "strong": 0}
        for row in distribution_rows:
            bucket = row["bucket"]
            if bucket in strength_distribution:
                strength_distribution[bucket] = int(row["count"])

        total = sum(int(layer["count"]) for layer in layers.values())

        return {
            "bank_id": bank_id,
            "total": total,
            "layers": layers,
            "strength_distribution": strength_distribution,
        }

    # ------------------------------------------------------------------
    # Async operations
    # ------------------------------------------------------------------

    async def list_operations(
        self,
        bank_id: str,
        *,
        request_context: "RequestContext",
    ) -> list[dict[str, Any]]:
        """List async operations for a bank."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            operations = await conn.fetch(
                f"""
                SELECT operation_id, bank_id, operation_type, created_at, status, error_message, result_metadata
                FROM {fq_table("async_operations")}
                WHERE bank_id = $1
                ORDER BY created_at DESC
                """,
                bank_id,
            )

            def parse_metadata(metadata):
                if metadata is None:
                    return {}
                if isinstance(metadata, str):
                    return json.loads(metadata)
                return metadata

            return [
                {
                    "id": str(row["operation_id"]),
                    "task_type": row["operation_type"],
                    "items_count": parse_metadata(row["result_metadata"]).get("items_count", 0),
                    "document_id": parse_metadata(row["result_metadata"]).get("document_id"),
                    "created_at": row["created_at"].isoformat(),
                    "status": row["status"],
                    "error_message": row["error_message"],
                }
                for row in operations
            ]

    async def cancel_operation(
        self,
        bank_id: str,
        operation_id: str,
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Cancel a pending async operation."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        op_uuid = uuid.UUID(operation_id)
        async with acquire_with_retry(pool) as conn:
            result = await conn.fetchrow(
                f"SELECT bank_id FROM {fq_table('async_operations')} WHERE operation_id = $1 AND bank_id = $2",
                op_uuid,
                bank_id,
            )
            if not result:
                raise ValueError(f"Operation {operation_id} not found for bank {bank_id}")
            await conn.execute(f"DELETE FROM {fq_table('async_operations')} WHERE operation_id = $1", op_uuid)
        return {
            "success": True,
            "message": f"Operation {operation_id} cancelled",
            "operation_id": operation_id,
            "bank_id": bank_id,
        }

    async def submit_async_retain(
        self,
        bank_id: str,
        contents: list[dict[str, Any]],
        *,
        request_context: "RequestContext",
    ) -> dict[str, Any]:
        """Submit a batch retain operation to run asynchronously."""
        await self._ctx.authenticate_tenant(request_context)
        pool = await self._ctx.get_pool()
        operation_id = uuid.uuid4()
        async with acquire_with_retry(pool) as conn:
            await conn.execute(
                f"""
                INSERT INTO {fq_table("async_operations")} (operation_id, bank_id, operation_type, result_metadata)
                VALUES ($1, $2, $3, $4)
                """,
                operation_id,
                bank_id,
                "retain",
                json.dumps({"items_count": len(contents)}),
            )
        await self._ctx.task_backend.submit_task(
            {
                "type": "batch_retain",
                "operation_id": str(operation_id),
                "bank_id": bank_id,
                "contents": contents,
            }
        )
        logger.info(f"Retain task queued for bank_id={bank_id}, {len(contents)} items, operation_id={operation_id}")
        return {"operation_id": str(operation_id), "items_count": len(contents)}
