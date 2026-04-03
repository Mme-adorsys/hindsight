"""
Connectivity test for the Qdrant client (Epic 01, Story 01).

Requires Qdrant running at QDRANT_URL (default: http://localhost:6333).
Start with: docker compose -f docker/docker-compose.yml up -d qdrant

Test flow: connect → ensure_collection → upsert → search → get_by_id → delete → verify gone.
"""

import os
import uuid

import pytest

from hindsight_api.engine.qdrant_client import QdrantEngineClient

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
TEST_COLLECTION = "engrams_test_connectivity"
EMBEDDING_DIM = 384


def _random_embedding() -> list[float]:
    import random

    vec = [random.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
    norm = sum(x**2 for x in vec) ** 0.5
    return [x / norm for x in vec]


@pytest.fixture
async def qdrant_client():
    """Create and connect a QdrantEngineClient pointing at the test collection."""
    client = QdrantEngineClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
        collection=TEST_COLLECTION,
    )
    await client.connect()
    await client.ensure_collection()
    yield client
    # Cleanup: delete the test collection after the test
    try:
        from qdrant_client import AsyncQdrantClient

        raw = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        await raw.delete_collection(TEST_COLLECTION)
        await raw.close()
    except Exception:
        pass
    await client.close()


@pytest.mark.asyncio
async def test_qdrant_upsert_search_delete(qdrant_client: QdrantEngineClient):
    """
    Full connectivity round-trip:
    upsert a point → find it via vector search → retrieve by ID → delete → confirm gone.
    """
    engram_id = str(uuid.uuid4())
    embedding = _random_embedding()
    payload = {"text": "Engram connectivity test", "tags": ["test"], "source": "pytest"}

    # 1. Upsert
    await qdrant_client.upsert_point(engram_id=engram_id, embedding=embedding, payload=payload)

    # 2. Vector search — the upserted point must appear in top results
    results = await qdrant_client.search_similar(embedding=embedding, limit=5)
    found_ids = [r["engram_id"] for r in results]
    assert engram_id in found_ids, f"Upserted engram_id {engram_id} not found in search results: {found_ids}"

    # Score of the exact-match vector must be near 1.0 (cosine similarity)
    match = next(r for r in results if r["engram_id"] == engram_id)
    assert match["score"] > 0.99, f"Expected score ~1.0 for exact vector, got {match['score']}"

    # 3. Get by ID
    point = await qdrant_client.get_by_id(engram_id)
    assert point is not None, f"get_by_id returned None for {engram_id}"
    assert point["engram_id"] == engram_id
    assert point["payload"]["text"] == "Engram connectivity test"

    # 4. Delete
    await qdrant_client.delete_by_id(engram_id)

    # 5. Verify gone
    deleted = await qdrant_client.get_by_id(engram_id)
    assert deleted is None, f"Point {engram_id} still exists after delete"


@pytest.mark.asyncio
async def test_qdrant_batch_upsert(qdrant_client: QdrantEngineClient):
    """batch_upsert inserts multiple points in one request."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    points = [
        {"engram_id": eid, "embedding": _random_embedding(), "payload": {"text": f"batch-{i}"}}
        for i, eid in enumerate(ids)
    ]

    await qdrant_client.batch_upsert(points)

    for eid in ids:
        point = await qdrant_client.get_by_id(eid)
        assert point is not None, f"Batch-upserted point {eid} not found"
        await qdrant_client.delete_by_id(eid)


@pytest.mark.asyncio
async def test_qdrant_ensure_collection_idempotent(qdrant_client: QdrantEngineClient):
    """ensure_collection() can be called multiple times without error."""
    await qdrant_client.ensure_collection()
    await qdrant_client.ensure_collection()


@pytest.mark.asyncio
async def test_qdrant_get_by_id_missing(qdrant_client: QdrantEngineClient):
    """get_by_id returns None for a non-existent engram_id."""
    missing_id = str(uuid.uuid4())
    result = await qdrant_client.get_by_id(missing_id)
    assert result is None
