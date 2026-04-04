"""
Engram Storage Service — single point of write/read for all three storage systems.

This is the ONLY layer that writes directly to PostgreSQL Dictionary, Qdrant, and Neo4j.
All higher layers (Retain Pipeline, Consolidation, Reflect) go through this service.

Design:
- Create: sequential write (Dict → Qdrant → Neo4j) with compensation on failure
- Read:   parallel fetch from all three systems, assembled into FullEngram
- Update: routed to correct store(s) based on what changed
- Delete: cascading (Neo4j rels → Neo4j node → Qdrant → Dictionary)

Epic 01, Story 04 — concept.md ch. 3 (Storage Architecture)
"""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo
from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.qdrant_client import QdrantEngineClient
from hindsight_api.engine.response_models import (
    EngramContent,
    EngramMetadata,
    EngramRelationship,
    FullEngram,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class EngramStorageInterface(ABC):
    """Abstract interface for the Engram Storage Service."""

    @abstractmethod
    async def create_engram(self, data: dict[str, Any]) -> str:
        """
        Create a new Engram in all three storage systems atomically.

        Args:
            data: Dict containing at minimum bank_id, text, embedding.
                  Optional: strength, layer, tags, thalamus scores, metadata, etc.

        Returns:
            engram_id (UUID string) of the created Engram.
        """

    @abstractmethod
    async def read_engram(
        self,
        engram_id: str,
        fields: set[str] | None = None,
    ) -> FullEngram | None:
        """
        Read a full Engram assembled from all three systems.

        Args:
            engram_id: UUID string.
            fields: Optional set of {'metadata', 'content', 'relationships'}.
                    None = fetch all three. Partial fetch is faster.

        Returns:
            FullEngram or None if not found in Dictionary.
        """

    @abstractmethod
    async def read_metadata(self, engram_id: str) -> EngramMetadata | None:
        """Fast metadata-only read (Dictionary only — no Qdrant/Neo4j)."""

    @abstractmethod
    async def update_metadata(self, engram_id: str, updates: dict[str, Any]) -> None:
        """Update metadata fields in Dictionary + Neo4j node properties."""

    @abstractmethod
    async def update_content(self, engram_id: str, text: str, embedding: list[float]) -> None:
        """Replace text + embedding in Qdrant."""

    @abstractmethod
    async def add_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Add a relationship between two Engrams in Neo4j."""

    @abstractmethod
    async def remove_relationship(self, source_id: str, target_id: str, rel_type: str) -> None:
        """Remove a specific relationship between two Engrams in Neo4j."""

    @abstractmethod
    async def delete_engram(self, engram_id: str) -> None:
        """Delete Engram from all three systems (cascading)."""

    @abstractmethod
    async def exists(self, engram_id: str) -> bool:
        """Check if an Engram exists (Dictionary lookup only)."""

    @abstractmethod
    async def batch_create(self, engrams: list[dict[str, Any]]) -> list[str]:
        """Create multiple Engrams. Returns list of engram_ids."""


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class EngramStorageService(EngramStorageInterface):
    """
    Concrete implementation of EngramStorageInterface.

    Receives all three DB clients at construction time.
    The MemoryEngine holds the single instance (Epic 01, Story 04, T8).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        qdrant: QdrantEngineClient,
        neo4j: Neo4jEngineClient,
    ) -> None:
        self._pool = pool
        self._qdrant = qdrant
        self._neo4j = neo4j

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------

    async def create_engram(self, data: dict[str, Any]) -> str:
        """
        Write Engram to all three systems sequentially with compensation.

        Order: Dictionary INSERT → Qdrant upsert → Neo4j CREATE
        On failure at any step: reverse-delete already-written systems.
        """
        engram_id = str(uuid.uuid4())
        logger.debug(f"[create_engram] {engram_id} bank={data.get('bank_id')}")

        # --- Step 1: Dictionary ---
        try:
            await dict_repo.insert_entry(self._pool, {**data, "engram_id": engram_id})
        except Exception as exc:
            logger.error(f"[create_engram] Dictionary INSERT failed for {engram_id}: {exc}")
            raise

        # --- Step 2: Qdrant ---
        embedding = data.get("embedding", [])
        payload = {k: v for k, v in data.items() if k not in {"embedding", "bank_id"}}
        try:
            await self._qdrant.upsert_point(
                engram_id=engram_id,
                embedding=embedding,
                payload=payload,
            )
        except Exception as exc:
            logger.error(f"[create_engram] Qdrant upsert failed for {engram_id}: {exc}. Compensating...")
            await self._compensate_delete_dict(engram_id)
            raise

        # --- Step 3: Neo4j ---
        neo4j_props = {
            "engram_id": engram_id,
            "strength": data.get("strength", 0.0),
            "layer": data.get("layer"),
            "abstraction_level": data.get("abstraction_level", 0.0),
            "status": data.get("status", "active"),
            "thalamus_overall": data.get("thalamus_overall"),
        }
        # Filter out None values — Neo4j handles missing properties gracefully
        neo4j_props = {k: v for k, v in neo4j_props.items() if v is not None}
        try:
            await self._neo4j.create_node("Engram", neo4j_props)
        except Exception as exc:
            logger.error(f"[create_engram] Neo4j CREATE failed for {engram_id}: {exc}. Compensating...")
            await self._compensate_delete_qdrant(engram_id)
            await self._compensate_delete_dict(engram_id)
            raise

        logger.debug(f"[create_engram] Created {engram_id} in all 3 systems.")
        return engram_id

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    async def read_engram(
        self,
        engram_id: str,
        fields: set[str] | None = None,
    ) -> FullEngram | None:
        """
        Fetch Engram from requested systems in parallel.

        Uses asyncio.gather for concurrent reads across Dictionary/Qdrant/Neo4j.
        """
        fetch_all = fields is None
        fetch_meta = fetch_all or "metadata" in (fields or set())
        fetch_content = fetch_all or "content" in (fields or set())
        fetch_rels = fetch_all or "relationships" in (fields or set())

        async def _noop() -> None:
            return None

        tasks: list[Any] = [
            dict_repo.get_by_id(self._pool, engram_id) if fetch_meta else _noop(),
            self._qdrant.get_by_id(engram_id) if fetch_content else _noop(),
            self._neo4j.get_relationships(engram_id) if fetch_rels else _noop(),
        ]
        dict_row, qdrant_point, neo4j_rels = await asyncio.gather(*tasks, return_exceptions=False)

        # Not found in Dictionary = Engram does not exist
        if fetch_meta and dict_row is None:
            return None

        metadata: EngramMetadata | None = None
        if dict_row:
            metadata = _dict_row_to_metadata(dict_row)

        content: EngramContent | None = None
        if qdrant_point:
            content = EngramContent(
                text=qdrant_point["payload"].get("text", ""),
                embedding=qdrant_point.get("vector"),
                source=qdrant_point["payload"].get("source"),
                extra={k: v for k, v in qdrant_point["payload"].items() if k not in {"text", "source", "engram_id"}},
            )

        relationships: list[EngramRelationship] = []
        if neo4j_rels:
            relationships = [
                EngramRelationship(
                    target_id=UUID(r["target_id"]),
                    rel_type=r["rel_type"],
                    weight=r["properties"].get("weight", 1.0),
                    properties={k: v for k, v in r["properties"].items() if k != "weight"},
                )
                for r in neo4j_rels
            ]

        return FullEngram(
            engram_id=UUID(engram_id),
            metadata=metadata,
            content=content,
            relationships=relationships,
        )

    async def read_metadata(self, engram_id: str) -> EngramMetadata | None:
        """Dictionary-only read — fast, no Qdrant/Neo4j."""
        row = await dict_repo.get_by_id(self._pool, engram_id)
        return _dict_row_to_metadata(row) if row else None

    # -----------------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------------

    async def update_metadata(self, engram_id: str, updates: dict[str, Any]) -> None:
        """
        Update metadata in Dictionary + mirrored fields in Neo4j node.

        Fields mirrored to Neo4j: strength, layer, abstraction_level, status, thalamus_overall.
        """
        # Update Dictionary
        await dict_repo.update_entry(self._pool, engram_id, updates)

        # Mirror scalar fields to Neo4j node properties
        neo4j_mirror = {"strength", "layer", "abstraction_level", "status", "thalamus_overall"}
        neo4j_updates = {k: v for k, v in updates.items() if k in neo4j_mirror}
        if neo4j_updates:
            set_clause = ", ".join(f"e.{k} = $p_{k}" for k in neo4j_updates)
            await self._neo4j.run_cypher(
                f"MATCH (e:Engram {{engram_id: $engram_id}}) SET {set_clause}",
                params={"engram_id": engram_id, **{f"p_{k}": v for k, v in neo4j_updates.items()}},
            )

    async def update_content(self, engram_id: str, text: str, embedding: list[float]) -> None:
        """Replace text + embedding in Qdrant (metadata unchanged)."""
        point = await self._qdrant.get_by_id(engram_id)
        existing_payload = point["payload"] if point else {}
        await self._qdrant.upsert_point(
            engram_id=engram_id,
            embedding=embedding,
            payload={**existing_payload, "text": text},
        )

    # -----------------------------------------------------------------------
    # Relationships
    # -----------------------------------------------------------------------

    async def add_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        await self._neo4j.create_relationship(source_id, target_id, rel_type, properties)

    async def remove_relationship(self, source_id: str, target_id: str, rel_type: str) -> None:
        await self._neo4j.run_cypher(
            f"MATCH (a:Engram {{engram_id: $src}})-[r:{rel_type}]->(b:Engram {{engram_id: $tgt}}) DELETE r",
            params={"src": source_id, "tgt": target_id},
        )

    # -----------------------------------------------------------------------
    # Delete
    # -----------------------------------------------------------------------

    async def delete_engram(self, engram_id: str) -> None:
        """
        Delete Engram from all three systems in cascade order.

        Order chosen to avoid orphaned relationships:
        1. Neo4j relationships → 2. Neo4j node → 3. Qdrant → 4. Dictionary
        """
        logger.debug(f"[delete_engram] Deleting {engram_id}")

        # Neo4j (relationships + node) — DETACH DELETE handles both
        try:
            await self._neo4j.delete_node(engram_id, cascade_relationships=True)
        except Exception as exc:
            logger.warning(f"[delete_engram] Neo4j delete failed for {engram_id}: {exc}")

        # Qdrant
        try:
            await self._qdrant.delete_by_id(engram_id)
        except Exception as exc:
            logger.warning(f"[delete_engram] Qdrant delete failed for {engram_id}: {exc}")

        # Dictionary (last — authoritative existence check)
        try:
            await dict_repo.delete_by_id(self._pool, engram_id)
        except Exception as exc:
            logger.warning(f"[delete_engram] Dictionary delete failed for {engram_id}: {exc}")

    # -----------------------------------------------------------------------
    # Exists / Batch
    # -----------------------------------------------------------------------

    async def exists(self, engram_id: str) -> bool:
        return await dict_repo.get_by_id(self._pool, engram_id) is not None

    async def batch_create(self, engrams: list[dict[str, Any]]) -> list[str]:
        """Create multiple Engrams sequentially. Returns list of engram_ids."""
        ids: list[str] = []
        for data in engrams:
            eid = await self.create_engram(data)
            ids.append(eid)
        return ids

    # -----------------------------------------------------------------------
    # Compensation helpers
    # -----------------------------------------------------------------------

    async def _compensate_delete_dict(self, engram_id: str) -> None:
        try:
            await dict_repo.delete_by_id(self._pool, engram_id)
        except Exception as exc:
            logger.error(f"[compensate] Failed to clean up Dictionary for {engram_id}: {exc}")

    async def _compensate_delete_qdrant(self, engram_id: str) -> None:
        try:
            await self._qdrant.delete_by_id(engram_id)
        except Exception as exc:
            logger.error(f"[compensate] Failed to clean up Qdrant for {engram_id}: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dict_row_to_metadata(row: dict[str, Any]) -> EngramMetadata:
    """Convert a raw asyncpg row dict to an EngramMetadata model."""
    return EngramMetadata(
        engram_id=row["engram_id"],
        bank_id=row["bank_id"],
        strength=row.get("strength") or 0.0,
        layer=row.get("layer"),
        abstraction_level=row.get("abstraction_level") or 0.0,
        tags=list(row.get("tags") or []),
        novelty=row.get("novelty"),
        surprise=row.get("surprise"),
        task_relevance=row.get("task_relevance"),
        emotional_valence=row.get("emotional_valence"),
        thalamus_overall=row.get("thalamus_overall"),
        created_at=row.get("created_at"),
        last_accessed=row.get("last_accessed"),
        access_count=row.get("access_count") or 0,
        status=row.get("status") or "active",
        confidence_score=row.get("confidence_score"),
        session_ref=row.get("session_ref"),
    )
