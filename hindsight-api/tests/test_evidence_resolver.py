"""Unit tests for Epic 25 Story 16 — resolve_schema_evidence."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.consolidation.constants import (
    RECALL_DEFAULT_EVIDENCE_N,
    SCHEMA_TOP_N_EVIDENCE,
)
from hindsight_api.engine.search.evidence_resolver import (
    EvidenceEngram,
    EvidenceResolverError,
    resolve_all_schema_evidence,
    resolve_schema_evidence,
)
from hindsight_api.engine.search.hybrid_retriever import RetrievalHit

# ---------------------------------------------------------------------------
# Drift guard — the recall N is intentionally below the schema-write N
# ---------------------------------------------------------------------------


def test_recall_default_evidence_n_below_schema_top_n():
    assert RECALL_DEFAULT_EVIDENCE_N < SCHEMA_TOP_N_EVIDENCE
    assert RECALL_DEFAULT_EVIDENCE_N == 3
    assert SCHEMA_TOP_N_EVIDENCE == 5


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wired_pool(rows: list[dict[str, Any]]):
    """Return a MagicMock pool whose acquire_with_retry yields a conn with .fetch()."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    return pool, conn, _ctx


def _row(eid: UUID, *, text: str = "morning coffee", strength: float = 0.8, tags=None) -> dict[str, Any]:
    return {
        "id": str(eid),
        "text": text,
        "fact_type": "experience",
        "context": "work",
        "strength": strength,
        "tags": tags or ["coffee"],
    }


def _schema_hit(evidence_ids: list[UUID]) -> RetrievalHit:
    return RetrievalHit(
        kind="schema",
        id=uuid4(),
        score=0.9,
        evidence_engram_ids=evidence_ids,
        description="ritual",
    )


# ---------------------------------------------------------------------------
# Happy path + filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_default_n_when_all_active():
    eids = [uuid4() for _ in range(5)]
    rows = [_row(e) for e in eids]
    pool, _conn, ctx = _wired_pool(rows)
    hit = _schema_hit(eids)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1")

    assert len(out) == RECALL_DEFAULT_EVIDENCE_N
    assert all(isinstance(e, EvidenceEngram) for e in out)
    assert [e.id for e in out] == eids[:RECALL_DEFAULT_EVIDENCE_N]


@pytest.mark.asyncio
async def test_archived_engrams_filtered_via_sql():
    """SQL filters status='active' server-side; the resolver only returns rows it sees."""
    eids = [uuid4() for _ in range(5)]
    # only the 5th id comes back from the DB → simulates 4 archived
    rows = [_row(eids[4])]
    pool, _conn, ctx = _wired_pool(rows)
    hit = _schema_hit(eids)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1")

    assert len(out) == 1
    assert out[0].id == eids[4]


@pytest.mark.asyncio
async def test_empty_evidence_list_returns_empty_without_db_call():
    pool, conn, ctx = _wired_pool([])
    hit = _schema_hit([])

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1")

    assert out == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_max_n_cap_respected():
    eids = [uuid4() for _ in range(5)]
    pool, _conn, ctx = _wired_pool([_row(e) for e in eids])
    hit = _schema_hit(eids)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1", max_n=2)

    assert len(out) == 2
    assert [e.id for e in out] == eids[:2]


@pytest.mark.asyncio
async def test_zero_max_n_returns_empty_without_db_call():
    eids = [uuid4()]
    pool, conn, ctx = _wired_pool([])
    hit = _schema_hit(eids)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1", max_n=0)

    assert out == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_order_preserved_from_evidence_engram_ids():
    """C2 stored evidence by strength descending; recall must keep that order."""
    eids = [uuid4() for _ in range(3)]
    # DB returns rows in random order — resolver re-keys by id
    rows = [_row(eids[2]), _row(eids[0]), _row(eids[1])]
    pool, _conn, ctx = _wired_pool(rows)
    hit = _schema_hit(eids)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_schema_evidence(hit, pool=pool, bank_id="agent-1")

    assert [e.id for e in out] == eids


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engram_kind_hit_raises():
    pool, _conn, ctx = _wired_pool([])
    engram_hit = RetrievalHit(kind="engram", id=uuid4(), score=0.5)

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        with pytest.raises(EvidenceResolverError):
            await resolve_schema_evidence(engram_hit, pool=pool, bank_id="agent-1")


# ---------------------------------------------------------------------------
# Compose helper — Story 16 T3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_all_pairs_schema_with_evidence_engram_with_empty():
    schema_eids = [uuid4() for _ in range(3)]
    schema_hit = _schema_hit(schema_eids)
    engram_hit = RetrievalHit(kind="engram", id=uuid4(), score=0.7, text="solo episode")

    pool, _conn, ctx = _wired_pool([_row(e) for e in schema_eids])

    with patch("hindsight_api.engine.search.evidence_resolver.acquire_with_retry", new=ctx):
        out = await resolve_all_schema_evidence([schema_hit, engram_hit], pool=pool, bank_id="agent-1")

    assert len(out) == 2
    pair_schema, pair_engram = out
    assert pair_schema[0] is schema_hit
    assert [e.id for e in pair_schema[1]] == schema_eids
    assert pair_engram[0] is engram_hit
    assert pair_engram[1] == []
