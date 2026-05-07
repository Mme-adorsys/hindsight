"""Unit tests for Epic 25 Story 12 — incremental R4 schema-fit at retain time.

Pure unit: schema match + reinforce + storage clients are mocked. Tests
cover the four behaviours from the story: hit → reinforce, miss → no-op,
two-schema scenario routes to the strongest, and the feature-flag short-
circuit.

Includes a focused test for ``reinforce_schema_single_engram`` to pin
the single-engram weighting math.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.c2_schema_writer import reinforce_schema_single_engram
from hindsight_api.engine.consolidation.constants import (
    R4_INCREMENTAL_ENABLED,
    R4_INCREMENTAL_PROPERTY_REFRESH,
)
from hindsight_api.engine.retain.schema_fit_check import incremental_schema_fit
from hindsight_api.engine.schema.models import SchemaModel


def _schema(*, evidence_count: int = 4) -> SchemaModel:
    return SchemaModel(
        id=uuid.uuid4(),
        description="existing",
        properties={
            "evidence_count": evidence_count,
            "activity": {"type": "categorical", "value": "coffee", "count": evidence_count},
        },
        centroid_qdrant_id=None,
        evidence_engram_ids=[uuid.uuid4() for _ in range(3)],
        evidence_count=evidence_count,
        cycles_survived=2,
        status="active",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_reinforced_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def test_r4_constants_have_sensible_defaults():
    # Feature on, property refresh off — keep the cheap retain hot-path.
    assert R4_INCREMENTAL_ENABLED is True
    assert R4_INCREMENTAL_PROPERTY_REFRESH is False


# ---------------------------------------------------------------------------
# reinforce_schema_single_engram
# ---------------------------------------------------------------------------


class TestReinforceSchemaSingleEngram:
    async def test_happy_path_increments_evidence_and_reshuffles_top_n(self):
        schema = _schema(evidence_count=4)
        new_top_n = [uuid.uuid4() for _ in range(3)]
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0], "payload": {}})
        qdrant.upsert_schema_centroid = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=new_top_n),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ) as update_mock,
        ):
            updated = await reinforce_schema_single_engram(
                schema,
                "bank-A",
                engram_id=str(uuid.uuid4()),
                embedding=[0.0, 1.0],
                neo4j=MagicMock(),
                qdrant=qdrant,
                pool=MagicMock(),
            )
        assert updated.id == schema.id
        assert updated.evidence_count == 5
        assert updated.cycles_survived == schema.cycles_survived + 1
        assert updated.evidence_engram_ids == new_top_n
        update_mock.assert_awaited_once()
        # Centroid drifted toward [0,1] but old (weight 4) dominates → first comp > second.
        sent = qdrant.upsert_schema_centroid.await_args.kwargs["centroid"]
        assert sent[0] > sent[1]

    async def test_missing_centroid_bootstraps_with_engram_embedding(self):
        schema = _schema(evidence_count=2)
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value=None)
        qdrant.upsert_schema_centroid = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ),
        ):
            await reinforce_schema_single_engram(
                schema,
                "bank-A",
                engram_id=str(uuid.uuid4()),
                embedding=[0.5, 0.5],
                neo4j=MagicMock(),
                qdrant=qdrant,
                pool=MagicMock(),
            )
        sent = qdrant.upsert_schema_centroid.await_args.kwargs["centroid"]
        # Bootstrap path uses the embedding as-is.
        assert sent == [0.5, 0.5]

    async def test_property_refresh_off_keeps_old_properties(self):
        schema = _schema(evidence_count=4)
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0]})
        qdrant.upsert_schema_centroid = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer._fetch_member_tags",
                new=AsyncMock(side_effect=AssertionError("must not be called")),
            ),
        ):
            updated = await reinforce_schema_single_engram(
                schema,
                "bank-A",
                engram_id=str(uuid.uuid4()),
                embedding=[0.0, 1.0],
                neo4j=MagicMock(),
                qdrant=qdrant,
                pool=MagicMock(),
                refresh_properties=False,
            )
        # Original properties carried through unchanged.
        assert updated.properties == schema.properties

    async def test_property_refresh_on_reaggregates(self):
        schema = _schema(evidence_count=4)
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0]})
        qdrant.upsert_schema_centroid = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.select_top_n_evidence",
                new=AsyncMock(return_value=[uuid.uuid4()]),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer.update_schema",
                new=AsyncMock(),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_schema_writer._fetch_member_tags",
                new=AsyncMock(return_value=[["activity:tea"]]),
            ),
        ):
            updated = await reinforce_schema_single_engram(
                schema,
                "bank-A",
                engram_id=str(uuid.uuid4()),
                embedding=[0.0, 1.0],
                neo4j=MagicMock(),
                qdrant=qdrant,
                pool=MagicMock(),
                refresh_properties=True,
            )
        assert updated.properties["activity"]["value"] == "tea"


# ---------------------------------------------------------------------------
# incremental_schema_fit
# ---------------------------------------------------------------------------


class TestIncrementalSchemaFit:
    async def test_match_reinforces(self):
        schema = _schema()
        reinforced = MagicMock(spec_set=["id"], id=schema.id)

        async def _match(**kwargs):
            return schema, 0.92

        async def _reinforce(*args, **kwargs):
            return reinforced

        with (
            patch(
                "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
                new=_match,
            ),
            patch(
                "hindsight_api.engine.retain.schema_fit_check.reinforce_schema_single_engram",
                new=_reinforce,
            ),
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
            )
        assert result is reinforced

    async def test_no_match_returns_none(self):
        async def _match(**kwargs):
            return None, 0.55

        with patch(
            "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
            new=_match,
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
            )
        assert result is None

    async def test_match_failure_is_swallowed(self):
        async def _match(**kwargs):
            raise RuntimeError("qdrant timeout")

        with patch(
            "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
            new=_match,
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
            )
        assert result is None

    async def test_reinforce_failure_is_swallowed(self):
        schema = _schema()

        async def _match(**kwargs):
            return schema, 0.95

        async def _reinforce(*args, **kwargs):
            raise RuntimeError("transient")

        with (
            patch(
                "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
                new=_match,
            ),
            patch(
                "hindsight_api.engine.retain.schema_fit_check.reinforce_schema_single_engram",
                new=_reinforce,
            ),
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
            )
        assert result is None

    async def test_feature_flag_off_short_circuits(self):
        # Even if match would have hit, enabled=False bypasses everything.
        async def _match(**kwargs):  # pragma: no cover — must not be called
            raise AssertionError("match must not be called when disabled")

        with patch(
            "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
            new=_match,
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
                enabled=False,
            )
        assert result is None

    async def test_strongest_of_multiple_candidates_wins_via_match_top_one(self):
        # match_existing_schema already does limit=1 — by contract it returns
        # the top hit. We simulate two similar schemas and verify the higher
        # cosine schema is the one routed to reinforcement.
        weak_schema = _schema()
        strong_schema = _schema()

        async def _match(**kwargs):
            # Qdrant naturally returns its highest-cosine hit; we mimic that.
            return strong_schema, 0.95

        captured: dict = {}

        async def _reinforce(schema, bank_id, **kwargs):
            captured["schema"] = schema
            return schema

        with (
            patch(
                "hindsight_api.engine.retain.schema_fit_check.match_existing_schema",
                new=_match,
            ),
            patch(
                "hindsight_api.engine.retain.schema_fit_check.reinforce_schema_single_engram",
                new=_reinforce,
            ),
        ):
            result = await incremental_schema_fit(
                engram_id=str(uuid.uuid4()),
                embedding=[0.1] * 4,
                bank_id="bank-A",
                neo4j=MagicMock(),
                qdrant=MagicMock(),
                pool=MagicMock(),
                schema_lookup=AsyncMock(),
            )
        assert result is strong_schema
        assert captured["schema"] is strong_schema
        assert weak_schema is not captured["schema"]


@pytest.mark.parametrize("flag", [R4_INCREMENTAL_ENABLED])
def test_default_flag_state(flag):
    # If someone toggles this off in constants.py the retain hot-path goes
    # silent — make the flip visible in the test suite.
    assert flag is True
