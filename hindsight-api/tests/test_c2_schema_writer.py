"""Unit tests for Epic 25 Story 09 — c2_schema_writer.

Pure unit: PostgreSQL pool, Neo4j client and Qdrant client are mocked.
Tests pin the happy path, the Qdrant-failure-→-archive saga, the Top-N
strength selection, and the per-batch best-effort semantics.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.c2_pattern_recognition import CreationPayload, MaturedClusterCandidate
from hindsight_api.engine.consolidation.c2_schema_writer import (
    persist_creation_payloads,
    persist_new_schema,
    select_top_n_evidence,
)
from hindsight_api.engine.consolidation.constants import SCHEMA_TOP_N_EVIDENCE


def _payload(*, ids: list[str], description: str = "test schema", properties: dict | None = None) -> CreationPayload:
    cluster = MaturedClusterCandidate(
        engram_ids=tuple(ids),
        centroid=(1.0, 0.0, 0.0),
        dominant_tags=("activity",),
        cycles_survived=2,
        fingerprint_id=uuid.uuid4(),
        matched_existing=False,
        cohesion=0.9,
        member_tags=tuple(("activity:coffee",) for _ in ids),
    )
    return CreationPayload(
        cluster=cluster,
        properties=properties or {"evidence_count": len(ids), "activity": {"type": "categorical", "value": "coffee"}},
        description=description,
    )


def _pool_returning(rows: list[dict]) -> MagicMock:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def _ctx(_pool):
        yield conn

    pool = MagicMock()
    return pool, _ctx, conn


def _patch_acquire(ctx_manager):
    return patch(
        "hindsight_api.engine.consolidation.c2_schema_writer.acquire_with_retry",
        new=ctx_manager,
    )


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def test_top_n_constant_locked_to_concept():
    # concept §4.2 fixes Top-N=5 evidence engrams per schema.
    assert SCHEMA_TOP_N_EVIDENCE == 5


# ---------------------------------------------------------------------------
# select_top_n_evidence
# ---------------------------------------------------------------------------


class TestSelectTopNEvidence:
    async def test_returns_uuids_in_strength_order(self):
        ids = [uuid.uuid4() for _ in range(3)]
        # PG already sorts; the helper just relays.
        pool, ctx, conn = _pool_returning(
            [
                {"engram_id": ids[2], "strength": 0.9},
                {"engram_id": ids[0], "strength": 0.7},
                {"engram_id": ids[1], "strength": 0.4},
            ]
        )
        with _patch_acquire(ctx):
            result = await select_top_n_evidence(pool, [str(i) for i in ids])
        assert result == [ids[2], ids[0], ids[1]]
        sql = conn.fetch.await_args.args[0]
        assert "ORDER BY strength DESC" in sql

    async def test_empty_input_short_circuits(self):
        pool, ctx, conn = _pool_returning([])
        with _patch_acquire(ctx):
            result = await select_top_n_evidence(pool, [])
        assert result == []
        conn.fetch.assert_not_called()

    async def test_n_zero_returns_empty(self):
        pool, ctx, conn = _pool_returning([])
        with _patch_acquire(ctx):
            result = await select_top_n_evidence(pool, [str(uuid.uuid4())], n=0)
        assert result == []
        conn.fetch.assert_not_called()

    async def test_passes_limit_param(self):
        ids = [str(uuid.uuid4()) for _ in range(2)]
        pool, ctx, conn = _pool_returning([])
        with _patch_acquire(ctx):
            await select_top_n_evidence(pool, ids, n=2)
        assert conn.fetch.await_args.args[2] == 2


# ---------------------------------------------------------------------------
# persist_new_schema
# ---------------------------------------------------------------------------


def _wired_writer():
    """Build mocks for neo4j, qdrant, pool, and select_top_n_evidence patcher."""
    neo4j = MagicMock()
    qdrant = AsyncMock()
    pool = MagicMock()
    return neo4j, qdrant, pool


class TestPersistNewSchema:
    async def test_happy_path_writes_neo4j_then_qdrant(self):
        neo4j, qdrant, pool = _wired_writer()
        evidence = [uuid.uuid4() for _ in range(3)]
        payload = _payload(ids=[str(uuid.uuid4()) for _ in range(5)], description="Coffee meetings")

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=evidence),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.create_schema",
                new=AsyncMock(),
            ) as create_mock,
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.archive_schema",
                new=AsyncMock(),
            ) as archive_mock,
        ):
            schema = await persist_new_schema(payload, "bank-A", neo4j=neo4j, qdrant=qdrant, pool=pool)

        # Neo4j called once with label="Schema"
        create_mock.assert_awaited_once()
        call_kwargs = create_mock.await_args.kwargs
        assert call_kwargs["label"] == "Schema"
        # Qdrant called with kind="schema" via upsert_schema_centroid + bank_id meta
        qdrant.upsert_schema_centroid.assert_awaited_once()
        qkwargs = qdrant.upsert_schema_centroid.await_args.kwargs
        assert qkwargs["schema_id"] == str(schema.id)
        assert qkwargs["centroid"] == [1.0, 0.0, 0.0]
        assert qkwargs["schema_meta"]["bank_id"] == "bank-A"
        # No archive (happy path)
        archive_mock.assert_not_called()
        # Returned model carries evidence + cycles_survived=1 + status active
        assert schema.evidence_engram_ids == evidence
        assert schema.cycles_survived == 1
        assert schema.status == "active"
        assert schema.description == "Coffee meetings"
        assert isinstance(schema.created_at, datetime)
        # last_reinforced_at equals created_at on birth
        assert schema.last_reinforced_at == schema.created_at

    async def test_qdrant_failure_archives_neo4j_node(self):
        neo4j, qdrant, pool = _wired_writer()
        qdrant.upsert_schema_centroid.side_effect = RuntimeError("qdrant down")
        payload = _payload(ids=[str(uuid.uuid4()) for _ in range(3)])

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.create_schema",
                new=AsyncMock(),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.archive_schema",
                new=AsyncMock(),
            ) as archive_mock,
        ):
            with pytest.raises(RuntimeError, match="qdrant down"):
                await persist_new_schema(payload, "bank-A", neo4j=neo4j, qdrant=qdrant, pool=pool)
        archive_mock.assert_awaited_once()
        # Archive called with the same schema_id that create_schema saw.
        archived_id = archive_mock.await_args.args[1]
        assert isinstance(archived_id, uuid.UUID)

    async def test_neo4j_failure_propagates_without_qdrant_call(self):
        neo4j, qdrant, pool = _wired_writer()
        payload = _payload(ids=[str(uuid.uuid4()) for _ in range(3)])

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.create_schema",
                new=AsyncMock(side_effect=RuntimeError("neo4j down")),
            ),
        ):
            with pytest.raises(RuntimeError, match="neo4j down"):
                await persist_new_schema(payload, "bank-A", neo4j=neo4j, qdrant=qdrant, pool=pool)
        qdrant.upsert_schema_centroid.assert_not_called()

    async def test_evidence_count_inherited_from_properties(self):
        neo4j, qdrant, pool = _wired_writer()
        payload = _payload(
            ids=[str(uuid.uuid4()) for _ in range(2)],
            properties={"evidence_count": 8, "x": {"type": "categorical", "value": "y"}},
        )
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.create_schema",
                new=AsyncMock(),
            ),
        ):
            schema = await persist_new_schema(payload, "bank-A", neo4j=neo4j, qdrant=qdrant, pool=pool)
        assert schema.evidence_count == 8


# ---------------------------------------------------------------------------
# persist_creation_payloads — best-effort batch
# ---------------------------------------------------------------------------


class TestPersistCreationPayloads:
    async def test_batch_continues_after_individual_failure(self):
        good = _payload(ids=[str(uuid.uuid4()) for _ in range(3)], description="ok")
        bad = _payload(ids=[str(uuid.uuid4()) for _ in range(3)], description="bad")
        good_again = _payload(ids=[str(uuid.uuid4()) for _ in range(3)], description="ok2")

        async def _persist(payload, bank_id, **kwargs):
            if payload.description == "bad":
                raise RuntimeError("transient")
            return MagicMock(spec_set=["id", "description"], id=uuid.uuid4(), description=payload.description)

        with patch(
            "hindsight_api.engine.consolidation.c2_schema_writer.persist_new_schema",
            new=_persist,
        ):
            persisted = await persist_creation_payloads(
                (good, bad, good_again),
                "bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
            )
        assert len(persisted) == 2
        assert {p.description for p in persisted} == {"ok", "ok2"}

    async def test_empty_batch_is_safe(self):
        persisted = await persist_creation_payloads(
            (),
            "bank-A",
            neo4j=MagicMock(),
            qdrant=MagicMock(),
            pool=MagicMock(),
        )
        assert persisted == []


# ---------------------------------------------------------------------------
# Story 10 — R4 batch reinforcement
# ---------------------------------------------------------------------------


import math  # noqa: E402

from hindsight_api.engine.consolidation.c2_pattern_recognition import MatchedForReinforcement  # noqa: E402
from hindsight_api.engine.consolidation.c2_schema_writer import (  # noqa: E402
    reinforce_matched,
    reinforce_schema,
    weighted_centroid,
)
from hindsight_api.engine.schema.models import SchemaModel  # noqa: E402


class TestWeightedCentroid:
    def test_unit_blend_stays_unit(self):
        # Two orthogonal unit vectors with equal weight → 45° blend, normalised.
        out = weighted_centroid([1.0, 0.0], 1, [0.0, 1.0], 1)
        assert out == pytest.approx([math.sqrt(2) / 2, math.sqrt(2) / 2], rel=1e-9)
        assert math.isclose(math.sqrt(out[0] ** 2 + out[1] ** 2), 1.0, rel_tol=1e-9)

    def test_heavier_old_dominates(self):
        # 9× old weight should pull the result close to old.
        out = weighted_centroid([1.0, 0.0], 9, [0.0, 1.0], 1)
        # Pre-norm the blend is [0.9, 0.1]; norm ≈ 0.906; close to old direction.
        assert out[0] > out[1]

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            weighted_centroid([1.0, 0.0], -1, [0.0, 1.0], 1)

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimensions disagree"):
            weighted_centroid([1.0, 0.0], 1, [0.0, 1.0, 0.0], 1)

    def test_zero_total_weight_raises(self):
        with pytest.raises(ValueError, match="zero-weighted"):
            weighted_centroid([1.0, 0.0], 0, [0.0, 1.0], 0)


def _existing_schema(*, evidence_count: int = 8, evidence_ids: list | None = None) -> SchemaModel:
    return SchemaModel(
        id=uuid.uuid4(),
        description="existing",
        properties={
            "evidence_count": evidence_count,
            "activity": {"type": "categorical", "value": "coffee", "count": evidence_count, "confidence": 1.0},
        },
        centroid_qdrant_id=None,  # Will fall back to schema.id when looked up.
        evidence_engram_ids=evidence_ids or [uuid.uuid4() for _ in range(5)],
        evidence_count=evidence_count,
        cycles_survived=3,
        status="active",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_reinforced_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )


def _matched(
    schema: SchemaModel, *, cluster_size: int = 3, centroid: tuple[float, ...] = (0.0, 1.0)
) -> MatchedForReinforcement:
    cluster = MaturedClusterCandidate(
        engram_ids=tuple(str(uuid.uuid4()) for _ in range(cluster_size)),
        centroid=centroid,
        dominant_tags=("activity",),
        cycles_survived=2,
        fingerprint_id=uuid.uuid4(),
        matched_existing=True,
        cohesion=0.9,
        member_tags=tuple(("activity:tea",) for _ in range(cluster_size)),
    )
    return MatchedForReinforcement(cluster=cluster, schema=schema, cosine=0.91)


class TestReinforceSchema:
    async def test_happy_path_increments_evidence_and_cycles(self):
        schema = _existing_schema(evidence_count=8)
        matched = _matched(schema, cluster_size=3, centroid=(0.0, 1.0))
        new_top_n = [uuid.uuid4() for _ in range(5)]
        new_tags = [["activity:tea"]] * 5

        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0], "payload": {}})
        qdrant.upsert_schema_centroid = AsyncMock()
        neo4j = MagicMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=new_top_n),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer._fetch_member_tags",
                new=AsyncMock(return_value=new_tags),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ) as update_mock,
        ):
            updated = await reinforce_schema(matched, "bank-A", neo4j=neo4j, qdrant=qdrant, pool=MagicMock())

        assert updated.id == schema.id
        assert updated.evidence_count == 8 + 3
        assert updated.cycles_survived == schema.cycles_survived + 1
        assert updated.evidence_engram_ids == new_top_n
        # Centroid moved from old (1,0) toward cluster (0,1) — old weight 8, new 3.
        # Dominated by old → first component still > 0.5.
        upserted = qdrant.upsert_schema_centroid.await_args.kwargs["centroid"]
        assert upserted[0] > 0.5
        assert upserted[1] > 0.0
        # Properties refreshed via aggregator over the new tags (activity=tea now).
        assert updated.properties["activity"]["value"] == "tea"
        update_mock.assert_awaited_once()
        assert update_mock.await_args.kwargs["label"] == "Schema"

    async def test_qdrant_lookup_failure_bootstraps_with_cluster_centroid(self):
        schema = _existing_schema(evidence_count=4)
        matched = _matched(schema, cluster_size=3, centroid=(0.0, 1.0))
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(side_effect=RuntimeError("qdrant unreachable"))
        qdrant.upsert_schema_centroid = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer._fetch_member_tags",
                new=AsyncMock(return_value=[["activity:tea"]]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ),
        ):
            updated = await reinforce_schema(matched, "bank-A", neo4j=MagicMock(), qdrant=qdrant, pool=MagicMock())

        # Bootstrap: new centroid == cluster centroid.
        sent_centroid = qdrant.upsert_schema_centroid.await_args.kwargs["centroid"]
        assert sent_centroid == [0.0, 1.0]
        assert updated.evidence_count == 7

    async def test_qdrant_centroid_upsert_failure_logs_but_no_rollback(self):
        schema = _existing_schema()
        matched = _matched(schema)
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0], "payload": {}})
        qdrant.upsert_schema_centroid = AsyncMock(side_effect=RuntimeError("qdrant down"))

        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer._fetch_member_tags",
                new=AsyncMock(return_value=[["activity:tea"]]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ) as update_mock,
        ):
            updated = await reinforce_schema(matched, "bank-A", neo4j=MagicMock(), qdrant=qdrant, pool=MagicMock())

        # Update happened, swallowed Qdrant failure, returned the new model.
        assert updated.evidence_count > schema.evidence_count
        update_mock.assert_awaited_once()

    async def test_empty_cluster_is_noop(self):
        schema = _existing_schema()
        cluster = MaturedClusterCandidate(
            engram_ids=(),
            centroid=(0.0, 1.0),
            dominant_tags=(),
            cycles_survived=2,
            fingerprint_id=uuid.uuid4(),
            matched_existing=True,
            cohesion=0.9,
            member_tags=(),
        )
        matched = MatchedForReinforcement(cluster=cluster, schema=schema, cosine=0.9)
        result = await reinforce_schema(matched, "bank-A", neo4j=MagicMock(), qdrant=AsyncMock(), pool=MagicMock())
        assert result is schema


class TestReinforceMatched:
    async def test_batch_continues_after_individual_failure(self):
        schema_ok = _existing_schema()
        schema_bad = _existing_schema()
        matched_ok = _matched(schema_ok)
        matched_bad = _matched(schema_bad)

        async def _reinforce(matched, bank_id, **kwargs):
            if matched.schema is schema_bad:
                raise RuntimeError("transient")
            return matched.schema

        with patch(
            "hindsight_api.engine.consolidation.c2_schema_writer.reinforce_schema",
            new=_reinforce,
        ):
            out = await reinforce_matched(
                (matched_ok, matched_bad),
                "bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
            )
        assert len(out) == 1
        assert out[0] is schema_ok

    async def test_empty_batch_safe(self):
        out = await reinforce_matched(
            (),
            "bank-A",
            neo4j=MagicMock(),
            qdrant=MagicMock(),
            pool=MagicMock(),
        )
        assert out == []
