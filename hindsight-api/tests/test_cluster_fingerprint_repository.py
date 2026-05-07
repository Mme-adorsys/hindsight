"""Unit tests for Epic 25 Story 05 — cluster_fingerprint_repository (R2).

Pure unit: asyncpg pool/connection mocked. The repository's responsibility
is pgvector-style cosine match-or-create + stale prune; we assert SQL
shapes and decision branches without a live database.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.cluster_fingerprint_repository import (
    DEFAULT_STALE_MAX_AGE_DAYS,
    MATCH_COSINE_THRESHOLD,
    FingerprintMatch,
    _format_vector_literal,
    match_or_create,
    prune_stale,
)


def _wired_pool(conn: AsyncMock) -> MagicMock:
    """Return a MagicMock pool whose ``acquire_with_retry`` yields ``conn``."""
    pool = MagicMock()

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    return pool, _ctx


def _patch_acquire(ctx_manager):
    return patch(
        "hindsight_api.engine.consolidation.cluster_fingerprint_repository.acquire_with_retry",
        new=ctx_manager,
    )


# ---------------------------------------------------------------------------
# Pure helpers + constants
# ---------------------------------------------------------------------------


class TestVectorLiteral:
    def test_format_round_trip(self):
        # The string form must be parseable as a pgvector literal — square
        # brackets, comma-separated floats, no spaces required.
        s = _format_vector_literal([1.0, 2.5, -0.1])
        assert s.startswith("[") and s.endswith("]")
        assert s == "[1.0,2.5,-0.1]"

    def test_threshold_locked_to_concept(self):
        # concept §13 R2/R4 — same cosine 0.85 in both places. Drift guard.
        assert MATCH_COSINE_THRESHOLD == 0.85
        assert DEFAULT_STALE_MAX_AGE_DAYS == 7


# ---------------------------------------------------------------------------
# match_or_create
# ---------------------------------------------------------------------------


class TestMatchOrCreate:
    async def test_match_increments_existing_row(self):
        existing_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": existing_id, "cycles_survived": 1, "cosine": 0.92},  # SELECT
                {"id": existing_id, "cycles_survived": 2},  # UPDATE
            ]
        )
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            outcome = await match_or_create(pool, "bank-A", [0.1] * 384, ["tag1", "tag2"])

        assert isinstance(outcome, FingerprintMatch)
        assert outcome.fingerprint_id == existing_id
        assert outcome.cycles_survived == 2
        assert outcome.matched_existing is True
        assert outcome.cosine == pytest.approx(0.92)

        # SELECT first, then UPDATE — exactly two fetchrow calls.
        assert conn.fetchrow.await_count == 2
        select_query = conn.fetchrow.await_args_list[0].args[0]
        update_query = conn.fetchrow.await_args_list[1].args[0]
        assert "SELECT id, cycles_survived" in select_query
        assert "centroid <=> $2::vector" in select_query
        assert "UPDATE c2_cluster_fingerprints" in update_query
        assert "cycles_survived + 1" in update_query

    async def test_below_threshold_creates_new_row(self):
        new_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": uuid.uuid4(), "cycles_survived": 5, "cosine": 0.50},  # SELECT — too far
                {"id": new_id, "cycles_survived": 1},  # INSERT
            ]
        )
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            outcome = await match_or_create(pool, "bank-A", [0.0] * 384, ["fresh"])

        assert outcome.fingerprint_id == new_id
        assert outcome.cycles_survived == 1
        assert outcome.matched_existing is False
        # Cosine reported reflects the rejected nearest neighbour for diagnostics.
        assert outcome.cosine == pytest.approx(0.50)
        assert "INSERT INTO c2_cluster_fingerprints" in conn.fetchrow.await_args_list[1].args[0]

    async def test_empty_table_creates_first_fingerprint(self):
        new_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,  # SELECT — no rows
                {"id": new_id, "cycles_survived": 1},  # INSERT
            ]
        )
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            outcome = await match_or_create(pool, "bank-A", [0.0] * 384, [])

        assert outcome.matched_existing is False
        assert outcome.cycles_survived == 1
        assert outcome.cosine is None  # No nearest neighbour to report.

    async def test_explicit_threshold_overrides_default(self):
        # A threshold of 0.99 should reject a row that 0.85 would accept.
        existing_id = uuid.uuid4()
        new_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": existing_id, "cycles_survived": 3, "cosine": 0.90},  # SELECT
                {"id": new_id, "cycles_survived": 1},  # INSERT
            ]
        )
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            outcome = await match_or_create(pool, "bank-A", [0.0] * 384, [], threshold=0.99)
        assert outcome.matched_existing is False
        assert outcome.fingerprint_id == new_id


# ---------------------------------------------------------------------------
# prune_stale
# ---------------------------------------------------------------------------


class TestPruneStale:
    async def test_returns_deleted_count(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="DELETE 3")
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            deleted = await prune_stale(pool, "bank-A", max_age_days=14)
        assert deleted == 3
        sql = conn.execute.await_args.args[0]
        assert "DELETE FROM c2_cluster_fingerprints" in sql
        assert "last_seen_at < now() - ($2::int * interval '1 day')" in sql
        assert conn.execute.await_args.args[1] == "bank-A"
        assert conn.execute.await_args.args[2] == 14

    async def test_zero_deletes_returns_zero(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="DELETE 0")
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            deleted = await prune_stale(pool, "bank-A")
        assert deleted == 0

    async def test_unexpected_status_string_returns_zero(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 2")  # not a DELETE result
        pool, ctx = _wired_pool(conn)
        with _patch_acquire(ctx):
            deleted = await prune_stale(pool, "bank-A")
        assert deleted == 0
