"""Unit tests for Epic 25 Story 13 — C3 R3 hyper-schema restructure.

Pure unit: Neo4j and Qdrant clients are mocked. The R3 candidate
detection runs over real-shaped SchemaModel data so the cosine math,
property-diff comparison and union logic are exercised end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.c3_schema_restructure import (
    HyperSchemaCandidate,
    R3Report,
    R5Report,
    _cosine,
    _differing_property_keys,
    _merge_property,
    _union_properties,
    archive_dead_schemas,
    create_hyper_schema,
    cycles_since_reinforced,
    find_hyper_schema_candidates,
    run_r3_hyper_schema,
)
from hindsight_api.engine.consolidation.constants import (
    C3_CYCLE_PERIOD_DAYS,
    HYPER_SCHEMA_COHESION_THRESHOLD,
    HYPER_SCHEMA_MIN_PROPERTY_DIFF,
    R5_EVIDENCE_THRESHOLD,
    R5_K_CYCLES,
)
from hindsight_api.engine.schema.models import HyperSchemaModel, SchemaModel


def _schema(*, properties: dict | None = None, evidence_count: int = 4) -> SchemaModel:
    return SchemaModel(
        id=uuid.uuid4(),
        description="schema",
        properties=properties or {"evidence_count": evidence_count},
        centroid_qdrant_id=None,
        evidence_engram_ids=[],
        evidence_count=evidence_count,
        cycles_survived=2,
        status="active",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_reinforced_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Drift guards
# ---------------------------------------------------------------------------


def test_constants_match_concept():
    # concept §13: cosine 0.7, at least one differing property key.
    assert HYPER_SCHEMA_COHESION_THRESHOLD == 0.7
    assert HYPER_SCHEMA_MIN_PROPERTY_DIFF == 1


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestCosine:
    def test_unit_orthogonal(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_unit_aligned(self):
        assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_dimension_mismatch_returns_zero(self):
        assert _cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestDifferingPropertyKeys:
    def test_same_value_no_diff(self):
        a = {"activity": {"type": "categorical", "value": "coffee", "count": 5}}
        b = {"activity": {"type": "categorical", "value": "coffee", "count": 9}}
        assert _differing_property_keys(a, b) == ()

    def test_different_value_listed(self):
        a = {"activity": {"type": "categorical", "value": "coffee"}}
        b = {"activity": {"type": "categorical", "value": "tea"}}
        assert _differing_property_keys(a, b) == ("activity",)

    def test_evidence_count_excluded(self):
        a = {"evidence_count": 5}
        b = {"evidence_count": 99}
        assert _differing_property_keys(a, b) == ()

    def test_missing_key_one_side_counts_as_diff(self):
        a = {"mood": {"type": "categorical", "value": "productive"}}
        b = {}
        assert _differing_property_keys(a, b) == ("mood",)


class TestMergeProperty:
    def test_categorical_same_value_keeps_value(self):
        merged = _merge_property(
            {"type": "categorical", "value": "coffee", "count": 5},
            {"type": "categorical", "value": "coffee", "count": 3},
        )
        assert merged["value"] == "coffee"
        assert merged["count"] == 8

    def test_categorical_different_value_becomes_set(self):
        merged = _merge_property(
            {"type": "categorical", "value": "coffee"},
            {"type": "categorical", "value": "tea"},
        )
        assert merged["type"] == "categorical_set"
        assert merged["values"] == ["coffee", "tea"]

    def test_numeric_unions_range_and_means_means(self):
        merged = _merge_property(
            {"type": "numeric", "min": 40, "max": 50, "mean": 45, "count": 4},
            {"type": "numeric", "min": 60, "max": 70, "mean": 65, "count": 6},
        )
        assert merged["min"] == 40
        assert merged["max"] == 70
        assert merged["mean"] == pytest.approx(55.0)
        assert merged["count"] == 10

    def test_temporal_unions_interval(self):
        merged = _merge_property(
            {"type": "temporal", "min_iso": "2026-05-01T00:00:00", "max_iso": "2026-05-03T00:00:00", "count": 4},
            {"type": "temporal", "min_iso": "2026-05-02T00:00:00", "max_iso": "2026-05-05T00:00:00", "count": 6},
        )
        assert merged["min_iso"] == "2026-05-01T00:00:00"
        assert merged["max_iso"] == "2026-05-05T00:00:00"


class TestUnionProperties:
    def test_evidence_counts_summed(self):
        out = _union_properties({"evidence_count": 4}, {"evidence_count": 6})
        assert out["evidence_count"] == 10

    def test_overlapping_keys_merged(self):
        a = {"activity": {"type": "categorical", "value": "coffee"}, "evidence_count": 4}
        b = {"activity": {"type": "categorical", "value": "coffee"}, "evidence_count": 6}
        out = _union_properties(a, b)
        assert out["activity"]["value"] == "coffee"

    def test_unique_keys_carried_over(self):
        a = {"x": {"type": "categorical", "value": "a"}, "evidence_count": 1}
        b = {"y": {"type": "categorical", "value": "b"}, "evidence_count": 1}
        out = _union_properties(a, b)
        assert out["x"]["value"] == "a"
        assert out["y"]["value"] == "b"


# ---------------------------------------------------------------------------
# find_hyper_schema_candidates
# ---------------------------------------------------------------------------


class TestFindHyperSchemaCandidates:
    async def test_pair_above_threshold_with_diff_yields_candidate(self):
        a = _schema(properties={"evidence_count": 4, "time": {"type": "categorical", "value": "morning"}})
        b = _schema(properties={"evidence_count": 4, "time": {"type": "categorical", "value": "afternoon"}})
        # Centroids near each other → cosine ≈ 0.99.
        centroids = {a.id: [1.0, 0.05], b.id: [1.0, 0.0]}

        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
        ):
            candidates = await find_hyper_schema_candidates("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert len(candidates) == 1
        assert "time" in candidates[0].differing_keys
        assert candidates[0].cosine > HYPER_SCHEMA_COHESION_THRESHOLD

    async def test_below_cosine_no_candidate(self):
        a = _schema(properties={"evidence_count": 4, "x": {"type": "categorical", "value": "a"}})
        b = _schema(properties={"evidence_count": 4, "x": {"type": "categorical", "value": "b"}})
        centroids = {a.id: [1.0, 0.0], b.id: [0.0, 1.0]}  # orthogonal → cosine 0.0
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
        ):
            candidates = await find_hyper_schema_candidates("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert candidates == []

    async def test_no_property_diff_no_candidate(self):
        # Cosine high, but properties identical → fails the differing-keys gate.
        a = _schema(properties={"evidence_count": 4, "activity": {"type": "categorical", "value": "coffee"}})
        b = _schema(properties={"evidence_count": 4, "activity": {"type": "categorical", "value": "coffee"}})
        centroids = {a.id: [1.0, 0.0], b.id: [1.0, 0.05]}
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
        ):
            candidates = await find_hyper_schema_candidates("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert candidates == []

    async def test_fewer_than_two_schemas_short_circuits(self):
        with patch(
            "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
            new=AsyncMock(return_value=[_schema()]),
        ):
            assert await find_hyper_schema_candidates("bank-A", neo4j=MagicMock(), qdrant=MagicMock()) == []


# ---------------------------------------------------------------------------
# create_hyper_schema
# ---------------------------------------------------------------------------


class TestCreateHyperSchema:
    async def test_persists_neo4j_qdrant_and_links(self):
        a = _schema(properties={"evidence_count": 4, "time": {"type": "categorical", "value": "morning"}})
        b = _schema(
            properties={"evidence_count": 6, "time": {"type": "categorical", "value": "afternoon"}},
            evidence_count=6,
        )
        cand = HyperSchemaCandidate(left=a, right=b, cosine=0.92, differing_keys=("time",))
        centroids = {a.id: [1.0, 0.0], b.id: [0.0, 1.0]}

        qdrant = AsyncMock()
        qdrant.upsert_schema_centroid = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.create_schema",
                new=AsyncMock(),
            ) as create_mock,
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.link_specialization",
                new=AsyncMock(),
            ) as link_mock,
        ):
            hyper = await create_hyper_schema(
                cand, "bank-A", neo4j=MagicMock(), qdrant=qdrant, schema_centroids=centroids
            )

        assert isinstance(hyper, HyperSchemaModel)
        assert hyper.evidence_count == 10  # 4 + 6
        # Property union carries activity/time keys.
        assert "time" in hyper.properties
        # create_schema called with HyperSchema label.
        assert create_mock.await_args.kwargs["label"] == "HyperSchema"
        # upsert_schema_centroid received normalized centroid (length 2, unit norm).
        sent = qdrant.upsert_schema_centroid.await_args.kwargs["centroid"]
        norm = sum(x * x for x in sent) ** 0.5
        assert abs(norm - 1.0) < 1e-9
        # Two SPECIALIZES links created (one per subschema).
        assert link_mock.await_count == 2

    async def test_missing_centroid_raises(self):
        a = _schema()
        b = _schema()
        cand = HyperSchemaCandidate(left=a, right=b, cosine=0.9, differing_keys=("x",))
        with pytest.raises(ValueError, match="centroids for both subschemas"):
            await create_hyper_schema(
                cand,
                "bank-A",
                neo4j=MagicMock(),
                qdrant=AsyncMock(),
                schema_centroids={a.id: [1.0, 0.0]},  # right missing
            )

    async def test_link_specialization_failure_logged_not_raised(self):
        a = _schema()
        b = _schema()
        cand = HyperSchemaCandidate(left=a, right=b, cosine=0.9, differing_keys=("x",))
        centroids = {a.id: [1.0, 0.0], b.id: [0.0, 1.0]}
        qdrant = AsyncMock()
        qdrant.upsert_schema_centroid = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.create_schema",
                new=AsyncMock(),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.link_specialization",
                new=AsyncMock(side_effect=RuntimeError("link failed")),
            ),
        ):
            hyper = await create_hyper_schema(
                cand,
                "bank-A",
                neo4j=MagicMock(),
                qdrant=qdrant,
                schema_centroids=centroids,
            )
        assert isinstance(hyper, HyperSchemaModel)


# ---------------------------------------------------------------------------
# run_r3_hyper_schema — end-to-end with reporting
# ---------------------------------------------------------------------------


class TestRunR3HyperSchema:
    async def test_no_pairs_returns_empty_report(self):
        with patch(
            "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
            new=AsyncMock(return_value=[_schema()]),
        ):
            report = await run_r3_hyper_schema("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert isinstance(report, R3Report)
        assert report.schemas_scanned == 1
        assert report.hyper_schemas_created == 0

    async def test_single_pair_creates_one_hyper(self):
        a = _schema(properties={"evidence_count": 4, "time": {"type": "categorical", "value": "morning"}})
        b = _schema(properties={"evidence_count": 6, "time": {"type": "categorical", "value": "afternoon"}})
        centroids = {a.id: [1.0, 0.0], b.id: [1.0, 0.05]}

        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.create_hyper_schema",
                new=AsyncMock(return_value=MagicMock()),
            ) as create_mock,
        ):
            report = await run_r3_hyper_schema("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert report.hyper_schemas_created == 1
        assert report.pairs_above_cosine == 1
        assert report.pairs_with_property_diff == 1
        create_mock.assert_awaited_once()

    async def test_subschema_used_in_at_most_one_hyper_per_run(self):
        # a, b, c all near each other with property diffs. Greedy first-match
        # locks `a` and `b` into a hyper; `c` is left alone this run.
        a = _schema(properties={"evidence_count": 2, "time": {"type": "categorical", "value": "morning"}})
        b = _schema(properties={"evidence_count": 2, "time": {"type": "categorical", "value": "noon"}})
        c = _schema(properties={"evidence_count": 2, "time": {"type": "categorical", "value": "evening"}})
        centroids = {a.id: [1.0, 0.0, 0.0], b.id: [1.0, 0.05, 0.0], c.id: [1.0, 0.0, 0.05]}

        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b, c]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.create_hyper_schema",
                new=AsyncMock(return_value=MagicMock()),
            ) as create_mock,
        ):
            report = await run_r3_hyper_schema("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert report.hyper_schemas_created == 1
        create_mock.assert_awaited_once()

    async def test_create_failure_continues_run(self):
        a = _schema(properties={"evidence_count": 4, "time": {"type": "categorical", "value": "morning"}})
        b = _schema(properties={"evidence_count": 6, "time": {"type": "categorical", "value": "afternoon"}})
        centroids = {a.id: [1.0, 0.0], b.id: [1.0, 0.05]}
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[a, b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure._fetch_schema_centroids",
                new=AsyncMock(return_value=centroids),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.create_hyper_schema",
                new=AsyncMock(side_effect=RuntimeError("transient")),
            ),
        ):
            report = await run_r3_hyper_schema("bank-A", neo4j=MagicMock(), qdrant=MagicMock())
        assert report.hyper_schemas_created == 0
        assert len(report.candidates) == 1  # detected, just not minted


# ---------------------------------------------------------------------------
# Story 14 — R5 Schema Death
# ---------------------------------------------------------------------------


def _aged_schema(*, days_since_reinforced: int, evidence_count: int) -> SchemaModel:
    """Synthesise a schema with a controllable last_reinforced_at age."""
    now = datetime(2026, 5, 7, tzinfo=timezone.utc)
    return SchemaModel(
        id=uuid.uuid4(),
        description="aged",
        properties={"evidence_count": evidence_count},
        centroid_qdrant_id=None,
        evidence_engram_ids=[],
        evidence_count=evidence_count,
        cycles_survived=2,
        status="active",
        created_at=now - timedelta(days=days_since_reinforced + 30),
        last_reinforced_at=now - timedelta(days=days_since_reinforced),
    )


_NOW = datetime(2026, 5, 7, tzinfo=timezone.utc)


class TestR5Constants:
    def test_bootstrap_defaults_more_lenient_than_concept(self):
        # Concept-default per §13: K=4, threshold=5. Bootstrap is K=8, threshold=3 —
        # both axes more permissive (longer life, harder to die from thin support).
        assert R5_K_CYCLES > 4
        assert R5_EVIDENCE_THRESHOLD < 5
        assert C3_CYCLE_PERIOD_DAYS == 7


class TestCyclesSinceReinforced:
    def test_zero_when_just_reinforced(self):
        schema = _aged_schema(days_since_reinforced=0, evidence_count=1)
        assert cycles_since_reinforced(schema, now=_NOW) == 0

    def test_one_cycle_after_seven_days(self):
        schema = _aged_schema(days_since_reinforced=7, evidence_count=1)
        assert cycles_since_reinforced(schema, now=_NOW) == 1

    def test_eight_cycles_after_fifty_six_days(self):
        # 8 cycles × 7 days = 56 days
        schema = _aged_schema(days_since_reinforced=56, evidence_count=1)
        assert cycles_since_reinforced(schema, now=_NOW) == 8

    def test_no_last_reinforced_at_returns_zero(self):
        schema = _aged_schema(days_since_reinforced=100, evidence_count=1)
        # Override directly so we hit the None branch.
        schema_no_reinforced = SchemaModel(
            id=schema.id,
            description=schema.description,
            properties=schema.properties,
            centroid_qdrant_id=None,
            evidence_engram_ids=[],
            evidence_count=1,
            cycles_survived=1,
            status="active",
            created_at=schema.created_at,
            last_reinforced_at=None,
        )
        assert cycles_since_reinforced(schema_no_reinforced, now=_NOW) == 0


class TestArchiveDeadSchemas:
    async def test_dead_schema_archived(self):
        # 9 cycles old (63 days), evidence_count=1 → both gates trigger.
        dead = _aged_schema(days_since_reinforced=63, evidence_count=1)
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[dead]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.archive_schema",
                new=AsyncMock(),
            ) as archive_mock,
        ):
            report = await archive_dead_schemas("bank-A", neo4j=MagicMock(), now=_NOW)
        assert isinstance(report, R5Report)
        assert report.archived == 1
        assert dead.id in report.archived_ids
        archive_mock.assert_awaited_once()
        assert archive_mock.await_args.kwargs["label"] == "Schema"

    async def test_evidence_above_threshold_protects(self):
        # Old schema but evidence_count >= R5_EVIDENCE_THRESHOLD → stays alive.
        protected = _aged_schema(days_since_reinforced=200, evidence_count=R5_EVIDENCE_THRESHOLD)
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[protected]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.archive_schema",
                new=AsyncMock(),
            ) as archive_mock,
        ):
            report = await archive_dead_schemas("bank-A", neo4j=MagicMock(), now=_NOW)
        assert report.archived == 0
        archive_mock.assert_not_called()

    async def test_fresh_schema_retained_even_with_thin_evidence(self):
        # 1 cycle old + evidence_count=0 → cycles gate fails → not archived.
        fresh = _aged_schema(days_since_reinforced=7, evidence_count=0)
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[fresh]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.archive_schema",
                new=AsyncMock(),
            ) as archive_mock,
        ):
            report = await archive_dead_schemas("bank-A", neo4j=MagicMock(), now=_NOW)
        assert report.archived == 0
        archive_mock.assert_not_called()

    async def test_archive_failure_skips_individual_does_not_abort(self):
        dead_a = _aged_schema(days_since_reinforced=200, evidence_count=1)
        dead_b = _aged_schema(days_since_reinforced=200, evidence_count=1)

        async def _archive(neo4j, schema_id, *, label):
            if schema_id == dead_a.id:
                raise RuntimeError("transient")

        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[dead_a, dead_b]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.archive_schema",
                new=_archive,
            ),
        ):
            report = await archive_dead_schemas("bank-A", neo4j=MagicMock(), now=_NOW)
        # Only dead_b counted as archived; dead_a's failure was logged.
        assert report.archived == 1
        assert dead_b.id in report.archived_ids
        assert dead_a.id not in report.archived_ids

    async def test_explicit_overrides_match_concept_default(self):
        # Override to concept defaults (K=4, threshold=5) — same schema,
        # different gate. 35 days = 5 cycles > 4, evidence=4 < 5 → archived.
        schema = _aged_schema(days_since_reinforced=35, evidence_count=4)
        with (
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
                new=AsyncMock(return_value=[schema]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c3_schema_restructure.archive_schema",
                new=AsyncMock(),
            ),
        ):
            report = await archive_dead_schemas(
                "bank-A",
                neo4j=MagicMock(),
                k_cycles=4,
                evidence_threshold=5,
                now=_NOW,
            )
        assert report.archived == 1

    async def test_empty_bank_returns_zero_report(self):
        with patch(
            "hindsight_api.engine.consolidation.c3_schema_restructure.list_active_schemas",
            new=AsyncMock(return_value=[]),
        ):
            report = await archive_dead_schemas("bank-A", neo4j=MagicMock(), now=_NOW)
        assert report == R5Report(bank_id="bank-A", schemas_scanned=0, archived=0, archived_ids=[])
