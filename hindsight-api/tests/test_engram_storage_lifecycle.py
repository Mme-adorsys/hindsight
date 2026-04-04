"""
CRUD lifecycle test for EngramStorageService (Epic 01, Story 04).

Requires all three services running:
  - PostgreSQL: via pg0 (handled by conftest)
  - Qdrant: docker compose -f docker/compose.yml up -d qdrant
  - Neo4j: docker compose -f docker/compose.yml up -d neo4j

Test flow:
  create → read (all 3 DBs) → update metadata → update content →
  add relationship → read (verify updates) → delete → read (all 3 DBs empty)
"""

import os
import uuid

import asyncpg
import pytest
import pytest_asyncio

from hindsight_api.engine.engram_storage import EngramStorageService
from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.qdrant_client import QdrantEngineClient

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None

NEO4J_BOLT_URL = os.getenv("NEO4J_BOLT_URL", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "hindsight")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

EMBEDDING_DIM = 384


def _random_embedding() -> list[float]:
    import random

    vec = [random.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
    norm = sum(x**2 for x in vec) ** 0.5
    return [x / norm for x in vec]


@pytest_asyncio.fixture
async def storage(pg0_db_url):
    """Fully wired EngramStorageService for lifecycle tests."""
    # Unique collection + bank per test to avoid parallel-worker conflicts
    collection = f"engrams_test_lc_{uuid.uuid4().hex[:8]}"
    bank_id = f"test_lifecycle_{uuid.uuid4().hex[:8]}"

    # PostgreSQL pool
    pool = await asyncpg.create_pool(pg0_db_url, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banks (bank_id) VALUES ($1) ON CONFLICT DO NOTHING",
            bank_id,
        )

    # Qdrant
    qdrant = QdrantEngineClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, collection=collection)
    await qdrant.connect()
    await qdrant.ensure_collection()

    # Neo4j
    neo4j = Neo4jEngineClient(
        bolt_url=NEO4J_BOLT_URL,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
    )
    await neo4j.connect()
    await neo4j.ensure_schema()

    service = EngramStorageService(pool=pool, qdrant=qdrant, neo4j=neo4j)
    service._test_bank_id = bank_id  # type: ignore[attr-defined]

    yield service

    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM engram_dictionary WHERE bank_id = $1", bank_id)
        await conn.execute("DELETE FROM banks WHERE bank_id = $1", bank_id)

    from qdrant_client import AsyncQdrantClient

    raw = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    await raw.delete_collection(collection)
    await raw.close()

    await qdrant.close()
    await neo4j.close()
    await pool.close()


@pytest.mark.asyncio
async def test_full_crud_lifecycle(storage: EngramStorageService):
    """
    Full CRUD lifecycle across all 3 storage systems.

    create → read all → update metadata → update content →
    add relationship → read verify → delete → read confirm gone
    """
    bank_id = storage._test_bank_id  # type: ignore[attr-defined]
    embedding = _random_embedding()

    # --- Create ---
    engram_id = await storage.create_engram({
        "bank_id": bank_id,
        "text": "The Engram lifecycle test stores a fact.",
        "embedding": embedding,
        "strength": 0.6,
        "layer": "buffer",
        "tags": ["lifecycle", "test"],
        "status": "active",
    })
    assert engram_id
    assert isinstance(engram_id, str)

    # --- Read (all 3 systems) ---
    full = await storage.read_engram(engram_id)
    assert full is not None
    assert str(full.engram_id) == engram_id

    # Dictionary (metadata)
    assert full.metadata is not None
    assert full.metadata.strength == 0.6
    assert full.metadata.layer == "buffer"
    assert "lifecycle" in full.metadata.tags

    # Qdrant (content)
    assert full.content is not None
    assert full.content.text == "The Engram lifecycle test stores a fact."

    # Neo4j (no relationships yet)
    assert full.relationships == []

    # --- Update metadata ---
    await storage.update_metadata(engram_id, {"strength": 0.9, "layer": "neocortex"})
    meta = await storage.read_metadata(engram_id)
    assert meta is not None
    assert meta.strength == 0.9
    assert meta.layer == "neocortex"

    # --- Update content ---
    new_embedding = _random_embedding()
    await storage.update_content(engram_id, "Updated Engram content.", new_embedding)
    full2 = await storage.read_engram(engram_id, fields={"content"})
    assert full2 is not None
    assert full2.content is not None
    assert full2.content.text == "Updated Engram content."

    # --- Add relationship (needs a second Engram) ---
    engram_id_b = await storage.create_engram({
        "bank_id": bank_id,
        "text": "Related Engram for relationship test.",
        "embedding": _random_embedding(),
        "strength": 0.5,
    })

    await storage.add_relationship(engram_id, engram_id_b, "SEMANTIC", {"weight": 0.75})

    # Read and verify relationship
    full3 = await storage.read_engram(engram_id, fields={"relationships"})
    assert full3 is not None
    assert len(full3.relationships) == 1
    assert str(full3.relationships[0].target_id) == engram_id_b
    assert full3.relationships[0].rel_type == "SEMANTIC"
    assert full3.relationships[0].weight == 0.75

    # --- Delete both Engrams ---
    await storage.delete_engram(engram_id_b)
    await storage.delete_engram(engram_id)

    # --- Verify gone from all 3 systems ---
    gone = await storage.read_engram(engram_id)
    assert gone is None

    # Qdrant
    qdrant_point = await storage._qdrant.get_by_id(engram_id)
    assert qdrant_point is None

    # Neo4j
    neo4j_node = await storage._neo4j.get_node(engram_id)
    assert neo4j_node is None


@pytest.mark.asyncio
async def test_exists(storage: EngramStorageService):
    """exists() returns True for present and False for absent engram_ids."""
    bank_id = storage._test_bank_id  # type: ignore[attr-defined]
    engram_id = await storage.create_engram({
        "bank_id": bank_id,
        "text": "exists test",
        "embedding": _random_embedding(),
    })

    assert await storage.exists(engram_id) is True
    assert await storage.exists(str(uuid.uuid4())) is False

    await storage.delete_engram(engram_id)
    assert await storage.exists(engram_id) is False


@pytest.mark.asyncio
async def test_batch_create(storage: EngramStorageService):
    """batch_create returns all engram_ids and creates entries in all systems."""
    bank_id = storage._test_bank_id  # type: ignore[attr-defined]
    engrams = [
        {"bank_id": bank_id, "text": f"Batch engram {i}", "embedding": _random_embedding()}
        for i in range(3)
    ]
    ids = await storage.batch_create(engrams)
    assert len(ids) == 3

    for eid in ids:
        assert await storage.exists(eid)
        await storage.delete_engram(eid)


@pytest.mark.asyncio
async def test_metadata_only_read(storage: EngramStorageService):
    """read_metadata returns metadata without querying Qdrant or Neo4j."""
    bank_id = storage._test_bank_id  # type: ignore[attr-defined]
    engram_id = await storage.create_engram({
        "bank_id": bank_id,
        "text": "metadata only",
        "embedding": _random_embedding(),
        "strength": 0.42,
    })

    meta = await storage.read_metadata(engram_id)
    assert meta is not None
    assert meta.strength == 0.42

    await storage.delete_engram(engram_id)
