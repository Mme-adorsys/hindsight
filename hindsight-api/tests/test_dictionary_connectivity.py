"""
Connectivity test for the engram_dictionary repository (Epic 01, Story 03).

Uses the existing pg0 test database (via conftest.py pg0_db_url fixture).
Requires migration f1b2c3d4e5f6 to have run (engram_dictionary table present).

Test flow: insert → get_by_id → filter → update → update_access →
           update_strength → batch_insert → delete → verify gone.
"""

import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from hindsight_api.engine.engram_dictionary import (
    batch_insert,
    delete_by_id,
    filter_entries,
    get_by_id,
    insert_entry,
    update_access,
    update_entry,
    update_strength,
)

TEST_BANK_ID = "test_dictionary_bank"


@pytest.fixture(scope="module")
async def pool(pg0_db_url):
    """Async connection pool for dictionary tests."""
    db_pool = await asyncpg.create_pool(pg0_db_url, min_size=1, max_size=5)

    # Ensure test bank exists (FK constraint)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banks (bank_id) VALUES ($1) ON CONFLICT DO NOTHING",
            TEST_BANK_ID,
        )

    yield db_pool

    # Cleanup: remove all test engrams and the test bank
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM engram_dictionary WHERE bank_id = $1", TEST_BANK_ID)
        await conn.execute("DELETE FROM banks WHERE bank_id = $1", TEST_BANK_ID)

    await db_pool.close()


def _new_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_insert_and_get(pool: asyncpg.Pool):
    """insert_entry creates a row retrievable by get_by_id."""
    engram_id = _new_id()
    await insert_entry(pool, {
        "engram_id": engram_id,
        "bank_id": TEST_BANK_ID,
        "strength": 0.75,
        "layer": "buffer",
        "tags": ["test", "connectivity"],
        "status": "active",
    })

    row = await get_by_id(pool, engram_id)
    assert row is not None
    assert str(row["engram_id"]) == engram_id
    assert row["bank_id"] == TEST_BANK_ID
    assert row["strength"] == 0.75
    assert row["layer"] == "buffer"
    assert row["status"] == "active"

    await delete_by_id(pool, engram_id)


@pytest.mark.asyncio
async def test_get_by_id_missing(pool: asyncpg.Pool):
    """get_by_id returns None for a non-existent engram_id."""
    result = await get_by_id(pool, _new_id())
    assert result is None


@pytest.mark.asyncio
async def test_filter_entries(pool: asyncpg.Pool):
    """filter_entries returns matching rows by bank + predicates."""
    ids = [_new_id() for _ in range(3)]
    entries = [
        {"engram_id": ids[0], "bank_id": TEST_BANK_ID, "strength": 0.9, "layer": "neocortex", "status": "active"},
        {"engram_id": ids[1], "bank_id": TEST_BANK_ID, "strength": 0.4, "layer": "buffer", "status": "active"},
        {"engram_id": ids[2], "bank_id": TEST_BANK_ID, "strength": 0.8, "layer": "neocortex", "status": "archived"},
    ]
    await batch_insert(pool, entries)

    try:
        # Filter by bank only
        all_rows = await filter_entries(pool, TEST_BANK_ID, status=None)
        found_ids = {str(r["engram_id"]) for r in all_rows}
        assert set(ids).issubset(found_ids)

        # Filter by strength_min
        strong_rows = await filter_entries(pool, TEST_BANK_ID, strength_min=0.8, status=None)
        strong_ids = {str(r["engram_id"]) for r in strong_rows}
        assert ids[0] in strong_ids  # 0.9
        assert ids[2] in strong_ids  # 0.8
        assert ids[1] not in strong_ids  # 0.4

        # Filter by layer
        neocortex_rows = await filter_entries(pool, TEST_BANK_ID, layer="neocortex", status=None)
        neocortex_ids = {str(r["engram_id"]) for r in neocortex_rows}
        assert ids[0] in neocortex_ids
        assert ids[2] in neocortex_ids
        assert ids[1] not in neocortex_ids

        # Filter by status
        active_rows = await filter_entries(pool, TEST_BANK_ID, status="active")
        active_ids = {str(r["engram_id"]) for r in active_rows}
        assert ids[0] in active_ids
        assert ids[1] in active_ids
        assert ids[2] not in active_ids  # archived
    finally:
        for eid in ids:
            await delete_by_id(pool, eid)


@pytest.mark.asyncio
async def test_update_entry(pool: asyncpg.Pool):
    """update_entry modifies specified fields."""
    engram_id = _new_id()
    await insert_entry(pool, {"engram_id": engram_id, "bank_id": TEST_BANK_ID, "strength": 0.5})

    try:
        await update_entry(pool, engram_id, {"strength": 0.9, "layer": "neocortex"})
        row = await get_by_id(pool, engram_id)
        assert row["strength"] == 0.9
        assert row["layer"] == "neocortex"
    finally:
        await delete_by_id(pool, engram_id)


@pytest.mark.asyncio
async def test_update_strength(pool: asyncpg.Pool):
    """update_strength convenience method works."""
    engram_id = _new_id()
    await insert_entry(pool, {"engram_id": engram_id, "bank_id": TEST_BANK_ID, "strength": 0.3})

    try:
        await update_strength(pool, engram_id, 0.95)
        row = await get_by_id(pool, engram_id)
        assert row["strength"] == 0.95
    finally:
        await delete_by_id(pool, engram_id)


@pytest.mark.asyncio
async def test_update_access(pool: asyncpg.Pool):
    """update_access increments access_count and sets last_accessed."""
    engram_id = _new_id()
    await insert_entry(pool, {"engram_id": engram_id, "bank_id": TEST_BANK_ID})

    try:
        before = datetime.now(timezone.utc)
        await update_access(pool, engram_id)
        await update_access(pool, engram_id)

        row = await get_by_id(pool, engram_id)
        assert row["access_count"] == 2
        assert row["last_accessed"] is not None
        assert row["last_accessed"].replace(tzinfo=timezone.utc) >= before
    finally:
        await delete_by_id(pool, engram_id)


@pytest.mark.asyncio
async def test_delete(pool: asyncpg.Pool):
    """delete_by_id removes the entry."""
    engram_id = _new_id()
    await insert_entry(pool, {"engram_id": engram_id, "bank_id": TEST_BANK_ID})
    await delete_by_id(pool, engram_id)

    result = await get_by_id(pool, engram_id)
    assert result is None


@pytest.mark.asyncio
async def test_fk_constraint(pool: asyncpg.Pool):
    """Inserting with a non-existent bank_id raises an error."""
    import asyncpg as apg

    engram_id = _new_id()
    with pytest.raises(apg.ForeignKeyViolationError):
        await insert_entry(pool, {"engram_id": engram_id, "bank_id": "nonexistent_bank_xyz"})
