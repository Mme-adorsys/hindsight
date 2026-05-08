"""Unit tests for Epic 25 Stories 21+22 — schema reconsolidation + drift throttle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from hindsight_api.engine.consolidation.constants import (
    MAX_SCHEMA_DRIFTS_PER_DAY,
    SCHEMA_CENTROID_DRIFT_ALPHA,
)
from hindsight_api.engine.reflect.schema_reconsolidation import (
    _throttle_check,
    drift_centroid,
    reconsolidate_schema_hit,
)
from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.schema.models import SchemaModel
from hindsight_api.engine.search.evidence_resolver import EvidenceEngram
from hindsight_api.engine.search.hybrid_retriever import RetrievalHit

# ---------------------------------------------------------------------------
# drift_centroid
# ---------------------------------------------------------------------------


class TestDriftCentroid:
    def test_alpha_zero_returns_unit_old(self):
        old = [1.0, 0.0, 0.0]
        out = drift_centroid(old, [0.0, 1.0, 0.0], alpha=0.0)
        assert out == pytest.approx([1.0, 0.0, 0.0])

    def test_alpha_one_returns_unit_query(self):
        out = drift_centroid([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], alpha=1.0)
        assert out == pytest.approx([0.0, 1.0, 0.0])

    def test_default_alpha_pulls_slightly(self):
        out = drift_centroid([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        # angle off the original axis is small with α=0.05
        assert out[0] > 0.9
        assert 0.0 < out[1] < 0.1

    def test_output_is_unit_norm(self):
        out = drift_centroid([3.0, 0.0, 0.0], [0.0, 4.0, 0.0], alpha=0.5)
        norm = sum(v * v for v in out) ** 0.5
        assert norm == pytest.approx(1.0)

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimension mismatch"):
            drift_centroid([1.0, 0.0], [0.0, 1.0, 0.0])

    def test_alpha_out_of_range_raises(self):
        with pytest.raises(ValueError, match="alpha"):
            drift_centroid([1.0, 0.0], [0.0, 1.0], alpha=2.0)

    def test_drift_alpha_constant_value_pinned(self):
        assert SCHEMA_CENTROID_DRIFT_ALPHA == 0.05


# ---------------------------------------------------------------------------
# reconsolidate_schema_hit — mode dispatch
# ---------------------------------------------------------------------------


def _schema(*, access_count: int = 5) -> SchemaModel:
    return SchemaModel(
        id=uuid4(),
        description="ritual",
        properties={"format": "1on1"},
        evidence_engram_ids=[],
        evidence_count=8,
        access_count=access_count,
    )


def _schema_hit(schema: SchemaModel) -> RetrievalHit:
    return RetrievalHit(
        kind="schema",
        id=schema.id,
        score=0.9,
        schema_label="Schema",
    )


def _wired_neo4j(schema: SchemaModel):
    """Patch the get_schema/update_schema calls used by reconsolidate_schema_hit."""
    neo4j = AsyncMock()
    return neo4j


@pytest.mark.asyncio
async def test_engram_hit_returns_none_no_op():
    out = await reconsolidate_schema_hit(
        RetrievalHit(kind="engram", id=uuid4(), score=0.5),
        neo4j=AsyncMock(),
        mode=RetrievalMode.PRECISION,
    )
    assert out is None


@pytest.mark.asyncio
async def test_precision_only_touches_access_count(monkeypatch):
    schema = _schema(access_count=3)
    after = schema.model_copy(update={"access_count": 4})

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)
    update_mock = AsyncMock(return_value=after)
    monkeypatch.setattr(mod, "update_schema", update_mock)

    hit = _schema_hit(schema)
    out = await reconsolidate_schema_hit(hit, neo4j=AsyncMock(), mode=RetrievalMode.PRECISION)

    assert out is after
    update_mock.assert_awaited_once()
    # update_schema(client, schema_id, partial, *, label=...) — partial is positional arg #3
    partial = update_mock.await_args.args[2]
    # Only access_count + last_accessed should be in the partial — no
    # property/centroid touches in Precision.
    assert "access_count" in partial
    assert "last_accessed" in partial
    assert "properties_json" not in partial


@pytest.mark.asyncio
async def test_exploration_refreshes_properties_from_evidence(monkeypatch):
    schema = _schema()
    monkeypatch.setattr(
        "hindsight_api.engine.reflect.schema_reconsolidation.update_schema",
        AsyncMock(return_value=schema),
    )
    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    upd = AsyncMock(return_value=schema)
    monkeypatch.setattr(mod, "update_schema", upd)
    monkeypatch.setattr(
        mod,
        "_get",
        AsyncMock(return_value=schema),
        raising=False,
    )

    evidence = [
        EvidenceEngram(id=uuid4(), text="t1", tags=["mood:productive", "format:1on1"]),
        EvidenceEngram(id=uuid4(), text="t2", tags=["mood:productive", "drink:coffee"]),
    ]
    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.EXPLORATION,
        evidence=evidence,
    )
    partial = upd.await_args.args[2]
    assert "properties_json" in partial
    # Properties should at least carry the recurring categorical keys.
    assert "mood" in partial["properties_json"] or "format" in partial["properties_json"]


@pytest.mark.asyncio
async def test_validation_with_pe_drifts_centroid(monkeypatch):
    schema = _schema()
    qdrant = AsyncMock()
    qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0, 0.0], "payload": {"bank_id": "b"}})
    qdrant.upsert_schema_centroid = AsyncMock()

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "update_schema", AsyncMock(return_value=schema))
    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)

    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.VALIDATION,
        query_embedding=[0.0, 1.0, 0.0],
        prediction_error=True,
        qdrant=qdrant,
    )
    qdrant.upsert_schema_centroid.assert_awaited_once()
    args, kwargs = qdrant.upsert_schema_centroid.await_args
    drifted = kwargs.get("centroid") or args[1]
    # centroid moved off the (1,0,0) axis but stays close
    assert drifted[0] > 0.9
    assert 0.0 < drifted[1] < 0.1


@pytest.mark.asyncio
async def test_validation_without_pe_skips_drift(monkeypatch):
    schema = _schema()
    qdrant = AsyncMock()

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "update_schema", AsyncMock(return_value=schema))
    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)

    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.VALIDATION,
        query_embedding=[0.0, 1.0, 0.0],
        prediction_error=False,
        qdrant=qdrant,
    )
    qdrant.upsert_schema_centroid.assert_not_called()


@pytest.mark.asyncio
async def test_missing_schema_returns_none(monkeypatch):
    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(mod, "update_schema", AsyncMock())

    out = await reconsolidate_schema_hit(
        _schema_hit(_schema()),
        neo4j=AsyncMock(),
        mode=RetrievalMode.PRECISION,
    )
    assert out is None


@pytest.mark.asyncio
async def test_update_failure_logged_returns_none(monkeypatch, caplog):
    schema = _schema()
    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)
    monkeypatch.setattr(mod, "update_schema", AsyncMock(side_effect=RuntimeError("neo4j down")))

    out = await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.PRECISION,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Story 22 — Drift throttle + audit
# ---------------------------------------------------------------------------


def _wired_pg_pool():
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = MagicMock()

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    return pool, conn, _ctx


def _drift_schema(*, drift_count: int = 0, last_drifted_at=None) -> SchemaModel:
    return SchemaModel(
        id=uuid4(),
        description="ritual",
        properties={"format": "1on1"},
        evidence_count=8,
        access_count=2,
        drift_count=drift_count,
        last_drifted_at=last_drifted_at,
    )


class TestThrottleCheck:
    def test_constant_pinned_at_5(self):
        assert MAX_SCHEMA_DRIFTS_PER_DAY == 5

    def test_fresh_schema_allows_drift(self):
        s = _drift_schema()
        new_count, new_last, allowed = _throttle_check(s, now=datetime.now(timezone.utc))
        assert allowed is True
        assert new_count == 0
        assert new_last is None

    def test_below_cap_allows_drift(self):
        now = datetime.now(timezone.utc)
        s = _drift_schema(drift_count=4, last_drifted_at=now - timedelta(hours=2))
        _, _, allowed = _throttle_check(s, now=now)
        assert allowed is True

    def test_at_cap_blocks_drift(self):
        now = datetime.now(timezone.utc)
        s = _drift_schema(drift_count=MAX_SCHEMA_DRIFTS_PER_DAY, last_drifted_at=now - timedelta(hours=2))
        _, _, allowed = _throttle_check(s, now=now)
        assert allowed is False

    def test_window_rolls_after_24h(self):
        now = datetime.now(timezone.utc)
        s = _drift_schema(drift_count=MAX_SCHEMA_DRIFTS_PER_DAY, last_drifted_at=now - timedelta(days=2))
        new_count, new_last, allowed = _throttle_check(s, now=now)
        assert new_count == 0
        assert new_last is None
        assert allowed is True


@pytest.mark.asyncio
async def test_validation_drift_persists_audit_row(monkeypatch):
    schema = _drift_schema()
    qdrant = AsyncMock()
    qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0, 0.0], "payload": {}})
    qdrant.upsert_schema_centroid = AsyncMock()

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)
    monkeypatch.setattr(mod, "update_schema", AsyncMock(return_value=schema))

    pool, conn, ctx = _wired_pg_pool()
    monkeypatch.setattr(mod, "acquire_with_retry", ctx)

    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.VALIDATION,
        query_embedding=[0.0, 1.0, 0.0],
        prediction_error=True,
        qdrant=qdrant,
        pool=pool,
        bank_id="bank-a",
    )
    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    sql, bank_arg, schema_arg, alpha_arg, hash_arg, mode_arg = args
    assert "INSERT INTO" in sql
    assert "schema_drift_events" in sql
    assert bank_arg == "bank-a"
    assert schema_arg == str(schema.id)
    assert alpha_arg == pytest.approx(SCHEMA_CENTROID_DRIFT_ALPHA)
    assert mode_arg == "validation"
    assert hash_arg


@pytest.mark.asyncio
async def test_throttled_drift_skips_qdrant_and_audit(monkeypatch):
    now = datetime.now(timezone.utc)
    schema = _drift_schema(
        drift_count=MAX_SCHEMA_DRIFTS_PER_DAY,
        last_drifted_at=now - timedelta(hours=1),
    )
    qdrant = AsyncMock()
    qdrant.get_by_id = AsyncMock()
    qdrant.upsert_schema_centroid = AsyncMock()

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)
    upd = AsyncMock(return_value=schema)
    monkeypatch.setattr(mod, "update_schema", upd)

    pool, conn, ctx = _wired_pg_pool()
    monkeypatch.setattr(mod, "acquire_with_retry", ctx)

    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.VALIDATION,
        query_embedding=[0.0, 1.0, 0.0],
        prediction_error=True,
        qdrant=qdrant,
        pool=pool,
        bank_id="bank-a",
    )

    qdrant.upsert_schema_centroid.assert_not_called()
    conn.execute.assert_not_called()
    partial = upd.await_args.args[2]
    assert "access_count" in partial
    assert "drift_count" not in partial


@pytest.mark.asyncio
async def test_drift_after_24h_window_rolls_counter(monkeypatch):
    now = datetime.now(timezone.utc)
    schema = _drift_schema(
        drift_count=MAX_SCHEMA_DRIFTS_PER_DAY,
        last_drifted_at=now - timedelta(days=2),
    )
    qdrant = AsyncMock()
    qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0, 0.0], "payload": {}})
    qdrant.upsert_schema_centroid = AsyncMock()

    import hindsight_api.engine.reflect.schema_reconsolidation as mod

    monkeypatch.setattr(mod, "_get", AsyncMock(return_value=schema), raising=False)
    upd = AsyncMock(return_value=schema)
    monkeypatch.setattr(mod, "update_schema", upd)

    pool, _conn, ctx = _wired_pg_pool()
    monkeypatch.setattr(mod, "acquire_with_retry", ctx)

    await reconsolidate_schema_hit(
        _schema_hit(schema),
        neo4j=AsyncMock(),
        mode=RetrievalMode.VALIDATION,
        query_embedding=[0.0, 1.0, 0.0],
        prediction_error=True,
        qdrant=qdrant,
        pool=pool,
        bank_id="bank-a",
    )

    qdrant.upsert_schema_centroid.assert_awaited_once()
    partial = upd.await_args.args[2]
    assert partial["drift_count"] == 1
    assert partial["last_drifted_at"] is not None
