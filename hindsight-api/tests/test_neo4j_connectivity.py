"""
Connectivity test for the Neo4j client (Epic 01, Story 02).

Requires Neo4j running at NEO4J_BOLT_URL (default: bolt://localhost:7687).
Start with: docker compose -f docker/compose.yml up -d neo4j

Test flow: connect → ensure_schema → create node → create relationship
           → get_node → get_relationships → traverse → delete → verify gone.
"""

import os
import uuid

import pytest

from hindsight_api.engine.neo4j_client import Neo4jEngineClient, RELATIONSHIP_TYPES

NEO4J_BOLT_URL = os.getenv("NEO4J_BOLT_URL", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "hindsight")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


@pytest.fixture
async def neo4j_client():
    """Create and connect a Neo4jEngineClient for testing."""
    client = Neo4jEngineClient(
        bolt_url=NEO4J_BOLT_URL,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
    )
    await client.connect()
    await client.ensure_schema()
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_neo4j_create_and_get_node(neo4j_client: Neo4jEngineClient):
    """Create an Engram node and retrieve it by engram_id."""
    engram_id = str(uuid.uuid4())
    props = {
        "engram_id": engram_id,
        "strength": 0.8,
        "layer": "buffer",
        "abstraction_level": 0.0,
        "status": "active",
        "tags": ["test", "connectivity"],
    }

    try:
        created = await neo4j_client.create_node("Engram", props)
        assert created["engram_id"] == engram_id
        assert created["strength"] == 0.8

        retrieved = await neo4j_client.get_node(engram_id)
        assert retrieved is not None
        assert retrieved["engram_id"] == engram_id
        assert retrieved["layer"] == "buffer"
    finally:
        await neo4j_client.delete_node(engram_id)


@pytest.mark.asyncio
async def test_neo4j_create_relationship(neo4j_client: Neo4jEngineClient):
    """Create two Engram nodes and a SEMANTIC relationship between them."""
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    try:
        await neo4j_client.create_node("Engram", {"engram_id": id_a, "strength": 0.7, "status": "active"})
        await neo4j_client.create_node("Engram", {"engram_id": id_b, "strength": 0.6, "status": "active"})

        await neo4j_client.create_relationship(
            from_id=id_a,
            to_id=id_b,
            rel_type="SEMANTIC",
            properties={"weight": 0.85},
        )

        rels = await neo4j_client.get_relationships(id_a, rel_types=["SEMANTIC"])
        assert len(rels) == 1
        assert rels[0]["target_id"] == id_b
        assert rels[0]["rel_type"] == "SEMANTIC"
        assert rels[0]["properties"]["weight"] == 0.85
    finally:
        await neo4j_client.delete_node(id_a)
        await neo4j_client.delete_node(id_b)


@pytest.mark.asyncio
async def test_neo4j_traverse(neo4j_client: Neo4jEngineClient):
    """BFS traversal follows SEMANTIC links to reachable nodes."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    # Chain: ids[0] → ids[1] → ids[2]

    try:
        for eid in ids:
            await neo4j_client.create_node("Engram", {"engram_id": eid, "strength": 0.5, "status": "active"})
        await neo4j_client.create_relationship(ids[0], ids[1], "SEMANTIC", {"weight": 0.9})
        await neo4j_client.create_relationship(ids[1], ids[2], "SEMANTIC", {"weight": 0.8})

        reachable = await neo4j_client.traverse(ids[0], rel_types=["SEMANTIC"], max_depth=3)
        reached_ids = {r["engram_id"] for r in reachable}

        assert ids[1] in reached_ids
        assert ids[2] in reached_ids
    finally:
        for eid in ids:
            await neo4j_client.delete_node(eid)


@pytest.mark.asyncio
async def test_neo4j_delete_node(neo4j_client: Neo4jEngineClient):
    """Deleted node is no longer retrievable."""
    engram_id = str(uuid.uuid4())
    await neo4j_client.create_node("Engram", {"engram_id": engram_id, "strength": 0.5, "status": "active"})
    await neo4j_client.delete_node(engram_id)

    result = await neo4j_client.get_node(engram_id)
    assert result is None


@pytest.mark.asyncio
async def test_neo4j_ensure_schema_idempotent(neo4j_client: Neo4jEngineClient):
    """ensure_schema() can be called multiple times without error."""
    await neo4j_client.ensure_schema()
    await neo4j_client.ensure_schema()


@pytest.mark.asyncio
async def test_neo4j_invalid_relationship_type(neo4j_client: Neo4jEngineClient):
    """Creating a relationship with an unknown type raises ValueError."""
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    with pytest.raises(ValueError, match="Unknown relationship type"):
        await neo4j_client.create_relationship(id_a, id_b, "INVALID_TYPE")


@pytest.mark.asyncio
async def test_neo4j_run_cypher(neo4j_client: Neo4jEngineClient):
    """run_cypher executes arbitrary Cypher and returns results."""
    engram_id = str(uuid.uuid4())

    try:
        await neo4j_client.create_node("Engram", {"engram_id": engram_id, "strength": 0.9, "status": "active"})

        records = await neo4j_client.run_cypher(
            "MATCH (e:Engram {engram_id: $engram_id}) RETURN e.strength AS strength",
            params={"engram_id": engram_id},
        )
        assert len(records) == 1
        assert records[0]["strength"] == 0.9
    finally:
        await neo4j_client.delete_node(engram_id)
