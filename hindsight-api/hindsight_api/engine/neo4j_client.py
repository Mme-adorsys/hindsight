"""
Neo4j client for Engram graph storage and traversal.

Provides async access to Neo4j with session management and retry logic
analogous to db_utils.py. Manages Engram nodes and all 8 relationship types.

Relationship types:
    SEMANTIC          — semantic similarity between Engrams (weight)
    TEMPORAL          — temporal sequence (weight)
    ENTITY            — shared entity reference (weight, entity_id)
    CAUSAL            — causal link (weight, subtype: causes/caused_by/enables/prevents)
    CO_ACTIVATED      — co-recalled in same session (weight, activation_count)
    TEMPORAL_PROXIMITY — stored close in time (weight, time_delta)
    SCHEMA            — member of schema/abstraction (weight)
    CONTRADICTION     — conflicting content (weight, resolution)
"""

import asyncio
import logging
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

logger = logging.getLogger(__name__)

# Retry configuration (mirrors db_utils.py defaults)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 0.5  # seconds
DEFAULT_MAX_DELAY = 5.0  # seconds

RETRYABLE_EXCEPTIONS = (
    ServiceUnavailable,
    SessionExpired,
    TransientError,
    ConnectionError,
    asyncio.TimeoutError,
    OSError,
)

# All 8 relationship types in the Engram graph (concept.md ch. 3 + ch. 14)
RELATIONSHIP_TYPES = (
    "SEMANTIC",
    "TEMPORAL",
    "ENTITY",
    "CAUSAL",
    "CO_ACTIVATED",
    "TEMPORAL_PROXIMITY",
    "SCHEMA",
    "CONTRADICTION",
)


async def _retry_with_backoff(
    func,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """Execute an async function with exponential backoff retry."""
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except RETRYABLE_EXCEPTIONS as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    f"Neo4j operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Neo4j operation failed after {max_retries + 1} attempts: {e}")
    raise last_exception


class Neo4jEngineClient:
    """
    Async Neo4j client for Engram graph operations.

    Wraps AsyncGraphDatabase driver with session management and retry logic.
    One instance per application lifetime — created at startup.

    All Engram nodes carry the label :Engram with an engram_id property.
    Flat architecture: no node hierarchy, differences expressed via properties
    (abstraction_level, connection_density) per concept.md ch. 3.
    """

    def __init__(self, bolt_url: str, username: str, password: str | None, database: str) -> None:
        self._bolt_url = bolt_url
        self._username = username
        self._password = password or ""
        self._database = database
        self._driver: AsyncDriver | None = None

    async def connect(self) -> None:
        """Initialize the async Neo4j driver."""
        self._driver = AsyncGraphDatabase.driver(
            self._bolt_url,
            auth=(self._username, self._password),
        )
        logger.info(f"Neo4j driver connected: url={self._bolt_url}, database={self._database}")

    async def close(self) -> None:
        """Close the Neo4j driver and all connections."""
        if self._driver:
            await self._driver.close()
            self._driver = None

    def _require_driver(self) -> AsyncDriver:
        if self._driver is None:
            raise RuntimeError("Neo4jEngineClient not connected. Call connect() first.")
        return self._driver

    async def ensure_schema(self) -> None:
        """
        Create constraints and indexes for the Engram graph (idempotent).

        Schema:
        - Unique constraint on Engram.engram_id
        - Composite index on (layer, status) for pre-filter queries
        - Indexes on strength, thalamus_overall, tags

        Relationship types are created implicitly by Neo4j on first use.
        See module docstring for all 8 relationship types and their properties.
        """
        driver = self._require_driver()

        schema_statements = [
            # Unique constraint — prevents duplicate Engram nodes
            "CREATE CONSTRAINT engram_id_unique IF NOT EXISTS FOR (e:Engram) REQUIRE e.engram_id IS UNIQUE",
            # Composite index for pre-filter: layer + status
            "CREATE INDEX engram_layer_status IF NOT EXISTS FOR (e:Engram) ON (e.layer, e.status)",
            # Index for strength-based filtering (recency decay, consolidation)
            "CREATE INDEX engram_strength IF NOT EXISTS FOR (e:Engram) ON (e.strength)",
            # Index for thalamus_overall score (relevance gating)
            "CREATE INDEX engram_thalamus IF NOT EXISTS FOR (e:Engram) ON (e.thalamus_overall)",
        ]

        async def _apply_schema():
            async with driver.session(database=self._database) as session:
                for stmt in schema_statements:
                    await session.run(stmt)
            logger.info("Neo4j schema applied: constraint + indexes on Engram nodes.")

        await _retry_with_backoff(_apply_schema)

    async def create_node(self, label: str, properties: dict[str, Any]) -> dict[str, Any]:
        """
        Create a node with the given label and properties.

        Args:
            label: Node label (e.g. "Engram").
            properties: Node properties dict. Must include engram_id for Engram nodes.

        Returns:
            The created node's properties.
        """
        driver = self._require_driver()

        async def _create():
            async with driver.session(database=self._database) as session:
                result = await session.run(
                    f"CREATE (n:{label} $props) RETURN properties(n) AS props",
                    props=properties,
                )
                record = await result.single()
                return dict(record["props"]) if record else {}

        return await _retry_with_backoff(_create)

    async def create_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """
        Create a relationship between two Engram nodes.

        Args:
            from_id: engram_id of the source node.
            to_id: engram_id of the target node.
            rel_type: Relationship type (must be one of RELATIONSHIP_TYPES).
            properties: Optional relationship properties (weight, subtype, etc.).
        """
        if rel_type not in RELATIONSHIP_TYPES:
            raise ValueError(f"Unknown relationship type '{rel_type}'. Must be one of {RELATIONSHIP_TYPES}.")

        driver = self._require_driver()
        props = properties or {}

        async def _create_rel():
            async with driver.session(database=self._database) as session:
                await session.run(
                    f"MATCH (a:Engram {{engram_id: $from_id}}), (b:Engram {{engram_id: $to_id}}) "
                    f"CREATE (a)-[r:{rel_type} $props]->(b)",
                    from_id=from_id,
                    to_id=to_id,
                    props=props,
                )

        await _retry_with_backoff(_create_rel)

    async def get_node(self, engram_id: str) -> dict[str, Any] | None:
        """
        Retrieve an Engram node by engram_id.

        Returns:
            Node properties dict, or None if not found.
        """
        driver = self._require_driver()

        async def _get():
            async with driver.session(database=self._database) as session:
                result = await session.run(
                    "MATCH (e:Engram {engram_id: $engram_id}) RETURN properties(e) AS props",
                    engram_id=engram_id,
                )
                record = await result.single()
                return dict(record["props"]) if record else None

        return await _retry_with_backoff(_get)

    async def get_relationships(
        self,
        engram_id: str,
        rel_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get all relationships for an Engram node.

        Args:
            engram_id: Source engram_id.
            rel_types: Optional filter list of relationship types. None = all types.

        Returns:
            List of dicts with keys: target_id, rel_type, properties.
        """
        driver = self._require_driver()
        type_filter = "|".join(rel_types) if rel_types else "|".join(RELATIONSHIP_TYPES)

        async def _get_rels():
            async with driver.session(database=self._database) as session:
                result = await session.run(
                    f"MATCH (e:Engram {{engram_id: $engram_id}})-[r:{type_filter}]->(t:Engram) "
                    "RETURN t.engram_id AS target_id, type(r) AS rel_type, properties(r) AS props",
                    engram_id=engram_id,
                )
                records = await result.data()
                return [
                    {
                        "target_id": rec["target_id"],
                        "rel_type": rec["rel_type"],
                        "properties": dict(rec["props"]),
                    }
                    for rec in records
                ]

        return await _retry_with_backoff(_get_rels)

    async def delete_node(self, engram_id: str, cascade_relationships: bool = True) -> None:
        """
        Delete an Engram node, optionally cascading to all its relationships.

        Args:
            engram_id: The engram_id of the node to delete.
            cascade_relationships: If True (default), delete all relationships first.
        """
        driver = self._require_driver()
        cypher = (
            "MATCH (e:Engram {engram_id: $engram_id}) DETACH DELETE e"
            if cascade_relationships
            else "MATCH (e:Engram {engram_id: $engram_id}) DELETE e"
        )

        async def _delete():
            async with driver.session(database=self._database) as session:
                await session.run(cypher, engram_id=engram_id)

        await _retry_with_backoff(_delete)

    async def traverse(
        self,
        start_id: str,
        rel_types: list[str] | None = None,
        max_depth: int = 3,
        min_weight: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        BFS traversal from a starting Engram node.

        Args:
            start_id: engram_id of the starting node.
            rel_types: Relationship types to follow. None = all types.
            max_depth: Maximum traversal depth (hops).
            min_weight: Minimum relationship weight to follow (0.0 = no filter).

        Returns:
            List of reachable nodes as dicts with keys: engram_id, depth, properties.
        """
        driver = self._require_driver()
        type_filter = "|".join(rel_types) if rel_types else "|".join(RELATIONSHIP_TYPES)
        weight_filter = "WHERE r.weight >= $min_weight" if min_weight > 0.0 else ""

        async def _traverse():
            async with driver.session(database=self._database) as session:
                result = await session.run(
                    f"MATCH path = (start:Engram {{engram_id: $start_id}})"
                    f"-[r:{type_filter}*1..{max_depth}]->(target:Engram) "
                    f"{weight_filter} "
                    "RETURN DISTINCT target.engram_id AS engram_id, "
                    "length(path) AS depth, properties(target) AS props",
                    start_id=start_id,
                    min_weight=min_weight,
                )
                records = await result.data()
                return [
                    {
                        "engram_id": rec["engram_id"],
                        "depth": rec["depth"],
                        "properties": dict(rec["props"]),
                    }
                    for rec in records
                ]

        return await _retry_with_backoff(_traverse)

    async def run_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute an arbitrary Cypher query and return all records as dicts.

        Args:
            query: Cypher query string.
            params: Optional query parameters.

        Returns:
            List of record dicts.
        """
        driver = self._require_driver()

        async def _run():
            async with driver.session(database=self._database) as session:
                result = await session.run(query, **(params or {}))
                return await result.data()

        return await _retry_with_backoff(_run)
