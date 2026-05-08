"""Unit tests for Epic 25 Story 23 — Multi-Bank schema promotion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from hindsight_api.engine.consolidation.constants import (
    CROSS_AGENT_MATCH_THRESHOLD,
    SHARED_PROMOTION_MAX_DAYS_INACTIVE,
    SHARED_PROMOTION_MIN_CYCLES,
    SHARED_PROMOTION_MIN_EVIDENCE,
)
from hindsight_api.engine.multi_bank.schema_promoter import (
    SchemaPromotionResult,
    _meets_criteria,
    find_schema_promotion_candidates,
    match_existing_shared_schema,
    promote_schema_to_shared,
    promote_schemas_batch,
    reinforce_shared_schema,
)
from hindsight_api.engine.schema.models import SchemaModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strong_schema(*, evidence: int = 12, cycles: int = 4, age_days: int = 1) -> SchemaModel:
    return SchemaModel(
        id=uuid4(),
        description="ritual",
        properties={"format": "1on1"},
        centroid_qdrant_id=uuid4(),
        evidence_engram_ids=[uuid4() for _ in range(5)],
        evidence_count=evidence,
        cycles_survived=cycles,
        last_reinforced_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )


# ---------------------------------------------------------------------------
# Drift-guard + criteria
# ---------------------------------------------------------------------------


class TestConstants:
    def test_pinned(self):
        assert SHARED_PROMOTION_MIN_EVIDENCE == 10
        assert SHARED_PROMOTION_MIN_CYCLES == 3
        assert SHARED_PROMOTION_MAX_DAYS_INACTIVE == 7


class TestMeetsCriteria:
    def test_strong_schema_passes(self):
        s = _strong_schema()
        ok, reason = _meets_criteria(
            s,
            now=datetime.now(timezone.utc),
            min_evidence=SHARED_PROMOTION_MIN_EVIDENCE,
            min_cycles=SHARED_PROMOTION_MIN_CYCLES,
            max_days_inactive=SHARED_PROMOTION_MAX_DAYS_INACTIVE,
        )
        assert ok is True
        assert reason is None

    def test_below_evidence_blocked(self):
        s = _strong_schema(evidence=8)
        ok, reason = _meets_criteria(
            s,
            now=datetime.now(timezone.utc),
            min_evidence=SHARED_PROMOTION_MIN_EVIDENCE,
            min_cycles=SHARED_PROMOTION_MIN_CYCLES,
            max_days_inactive=SHARED_PROMOTION_MAX_DAYS_INACTIVE,
        )
        assert ok is False
        assert reason == "below_evidence"

    def test_below_cycles_blocked(self):
        s = _strong_schema(cycles=1)
        ok, reason = _meets_criteria(
            s,
            now=datetime.now(timezone.utc),
            min_evidence=SHARED_PROMOTION_MIN_EVIDENCE,
            min_cycles=SHARED_PROMOTION_MIN_CYCLES,
            max_days_inactive=SHARED_PROMOTION_MAX_DAYS_INACTIVE,
        )
        assert ok is False
        assert reason == "below_cycles"

    def test_inactive_blocked(self):
        s = _strong_schema(age_days=30)
        ok, reason = _meets_criteria(
            s,
            now=datetime.now(timezone.utc),
            min_evidence=SHARED_PROMOTION_MIN_EVIDENCE,
            min_cycles=SHARED_PROMOTION_MIN_CYCLES,
            max_days_inactive=SHARED_PROMOTION_MAX_DAYS_INACTIVE,
        )
        assert ok is False
        assert reason == "inactive"

    def test_no_last_reinforced_blocked(self):
        s = _strong_schema()
        s = s.model_copy(update={"last_reinforced_at": None})
        ok, reason = _meets_criteria(
            s,
            now=datetime.now(timezone.utc),
            min_evidence=SHARED_PROMOTION_MIN_EVIDENCE,
            min_cycles=SHARED_PROMOTION_MIN_CYCLES,
            max_days_inactive=SHARED_PROMOTION_MAX_DAYS_INACTIVE,
        )
        assert ok is False
        assert reason == "inactive"


# ---------------------------------------------------------------------------
# find_schema_promotion_candidates
# ---------------------------------------------------------------------------


class TestFindCandidates:
    @pytest.mark.asyncio
    async def test_filters_to_eligible(self, monkeypatch):
        eligible = _strong_schema(evidence=15, cycles=5, age_days=1)
        weak = _strong_schema(evidence=5)
        old = _strong_schema(age_days=30)
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(
            mod,
            "list_active_schemas",
            AsyncMock(return_value=[eligible, weak, old]),
        )
        out = await find_schema_promotion_candidates(neo4j=AsyncMock())
        assert [s.id for s in out] == [eligible.id]


# ---------------------------------------------------------------------------
# promote_schema_to_shared
# ---------------------------------------------------------------------------


class TestPromoteSingle:
    @pytest.mark.asyncio
    async def test_copy_assigns_new_id_and_stamps_source(self, monkeypatch):
        original = _strong_schema()
        original_id = original.id
        captured: dict = {}

        async def _fake_create(_neo4j, model, *, label="Schema"):
            captured["model"] = model
            captured["label"] = label
            return model

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "create_schema", _fake_create)

        copy = await promote_schema_to_shared(
            original,
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
        )
        assert copy.id != original_id
        assert copy.evidence_engram_ids == []  # individual evidence stays agent-local
        assert copy.evidence_count == original.evidence_count  # audit-only carry-over
        assert copy.properties["source_bank_id"] == "agent-a"
        assert copy.properties["promoted_from_schema_id"] == str(original_id)
        # original schema's properties survive the copy
        assert copy.properties["format"] == "1on1"
        assert captured["label"] == "Schema"

    @pytest.mark.asyncio
    async def test_centroid_copied_to_qdrant(self, monkeypatch):
        original = _strong_schema()
        qdrant = AsyncMock()
        qdrant.upsert_schema_centroid = AsyncMock()

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "create_schema", AsyncMock(return_value=original))

        copy = await promote_schema_to_shared(
            original,
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
            qdrant=qdrant,
            qdrant_centroid=[1.0, 0.0, 0.0],
        )
        qdrant.upsert_schema_centroid.assert_awaited_once()
        kw = qdrant.upsert_schema_centroid.await_args.kwargs
        assert kw["schema_id"] == str(copy.id)
        assert kw["centroid"] == [1.0, 0.0, 0.0]
        assert kw["schema_meta"]["bank_id"] == "shared"
        assert kw["schema_meta"]["source_bank_id"] == "agent-a"

    @pytest.mark.asyncio
    async def test_qdrant_failure_logged_not_raised(self, monkeypatch):
        original = _strong_schema()
        qdrant = AsyncMock()
        qdrant.upsert_schema_centroid = AsyncMock(side_effect=RuntimeError("qdrant down"))
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "create_schema", AsyncMock(return_value=original))

        copy = await promote_schema_to_shared(
            original,
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
            qdrant=qdrant,
            qdrant_centroid=[0.0, 1.0, 0.0],
        )
        # cortex copy still succeeded
        assert copy.id != original.id


# ---------------------------------------------------------------------------
# promote_schemas_batch
# ---------------------------------------------------------------------------


class TestPromoteBatch:
    @pytest.mark.asyncio
    async def test_only_eligible_promoted(self, monkeypatch):
        eligible = _strong_schema(evidence=15, cycles=5, age_days=1)
        weak = _strong_schema(evidence=5)  # below_evidence
        immature = _strong_schema(cycles=1)  # below_cycles
        old = _strong_schema(age_days=30)  # inactive

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(
            mod,
            "list_active_schemas",
            AsyncMock(return_value=[eligible, weak, immature, old]),
        )
        monkeypatch.setattr(mod, "create_schema", AsyncMock(side_effect=lambda *_a, **_k: _a[1]))

        result = await promote_schemas_batch(
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
        )
        assert isinstance(result, SchemaPromotionResult)
        assert result.scanned == 4
        assert result.promoted == 1
        assert result.skipped_below_evidence == 1
        assert result.skipped_below_cycles == 1
        assert result.skipped_inactive == 1
        assert len(result.promoted_ids) == 1

    @pytest.mark.asyncio
    async def test_per_schema_failure_collected_not_raised(self, monkeypatch):
        eligible = _strong_schema()
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "list_active_schemas", AsyncMock(return_value=[eligible]))
        monkeypatch.setattr(mod, "create_schema", AsyncMock(side_effect=RuntimeError("neo4j down")))

        result = await promote_schemas_batch(
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
        )
        assert result.promoted == 0
        assert result.errors and "neo4j down" not in result.errors[0]  # exception class only
        assert "RuntimeError" in result.errors[0]

    @pytest.mark.asyncio
    async def test_qdrant_centroid_loaded_when_present(self, monkeypatch):
        eligible = _strong_schema()
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [0.5, 0.5, 0.0]})
        qdrant.upsert_schema_centroid = AsyncMock()

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "list_active_schemas", AsyncMock(return_value=[eligible]))
        monkeypatch.setattr(mod, "create_schema", AsyncMock(side_effect=lambda *_a, **_k: _a[1]))

        result = await promote_schemas_batch(
            source_bank_id="agent-a",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
            qdrant=qdrant,
        )
        assert result.promoted == 1
        qdrant.get_by_id.assert_awaited_once()
        qdrant.upsert_schema_centroid.assert_awaited_once()


# ---------------------------------------------------------------------------
# Story 24 — Cross-agent convergence
# ---------------------------------------------------------------------------


def _shared_schema(*, sources: list[str] | None = None, evidence: int = 12) -> SchemaModel:
    sources = sources or ["agent-a"]
    return SchemaModel(
        id=uuid4(),
        description="ritual",
        properties={
            "format": "1on1",
            "source_bank_ids": list(sources),
            "cross_agent_count": len(sources),
            "confidence_tier": "agent_local" if len(sources) < 2 else "cross_agent_validated",
        },
        centroid_qdrant_id=uuid4(),
        evidence_engram_ids=[],
        evidence_count=evidence,
        cycles_survived=4,
        last_reinforced_at=datetime.now(timezone.utc),
    )


class TestCrossAgentConstants:
    def test_threshold_pinned(self):
        assert CROSS_AGENT_MATCH_THRESHOLD == 0.85


class TestMatchExistingSharedSchema:
    @pytest.mark.asyncio
    async def test_match_above_threshold_returns_schema(self, monkeypatch):
        existing = _shared_schema()
        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock(
            return_value=[
                {
                    "engram_id": str(existing.id),
                    "score": 0.92,
                    "payload": {"schema_id": str(existing.id), "kind": "schema"},
                }
            ]
        )
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "get_schema", AsyncMock(return_value=existing))

        out = await match_existing_shared_schema(
            [1.0, 0.0, 0.0],
            shared_bank_id="shared",
            qdrant=qdrant,
            neo4j=AsyncMock(),
        )
        assert out is not None
        schema, score = out
        assert schema.id == existing.id
        assert score == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self, monkeypatch):
        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock(
            return_value=[
                {"engram_id": str(uuid4()), "score": 0.6, "payload": {"schema_id": str(uuid4())}}
            ]
        )
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "get_schema", AsyncMock())

        out = await match_existing_shared_schema(
            [1.0, 0.0, 0.0],
            shared_bank_id="shared",
            qdrant=qdrant,
            neo4j=AsyncMock(),
        )
        assert out is None

    @pytest.mark.asyncio
    async def test_qdrant_failure_returns_none(self):
        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock(side_effect=RuntimeError("qdrant down"))
        out = await match_existing_shared_schema(
            [1.0, 0.0, 0.0],
            shared_bank_id="shared",
            qdrant=qdrant,
            neo4j=AsyncMock(),
        )
        assert out is None

    @pytest.mark.asyncio
    async def test_empty_centroid_short_circuits(self):
        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock()
        out = await match_existing_shared_schema([], shared_bank_id="shared", qdrant=qdrant, neo4j=AsyncMock())
        assert out is None
        qdrant.search_similar.assert_not_called()


class TestReinforceSharedSchema:
    @pytest.mark.asyncio
    async def test_first_external_source_upgrades_tier(self, monkeypatch):
        existing = _shared_schema(sources=["agent-a"], evidence=10)
        incoming = _strong_schema(evidence=4)
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        update_mock = AsyncMock()
        monkeypatch.setattr(mod, "update_schema", update_mock)

        result = await reinforce_shared_schema(
            existing,
            incoming,
            source_bank_id="agent-b",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
        )
        update_mock.assert_awaited_once()
        partial = update_mock.await_args.args[2]
        assert partial["evidence_count"] == 14
        # tier upgraded after the second source
        assert "cross_agent_validated" in partial["properties_json"]
        # local return value mirrors the same upgrade
        assert result.properties["cross_agent_count"] == 2
        assert result.properties["confidence_tier"] == "cross_agent_validated"
        assert result.properties["source_bank_ids"] == ["agent-a", "agent-b"]

    @pytest.mark.asyncio
    async def test_same_source_does_not_double_count(self, monkeypatch):
        existing = _shared_schema(sources=["agent-a"], evidence=10)
        incoming = _strong_schema(evidence=4)
        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "update_schema", AsyncMock())

        result = await reinforce_shared_schema(
            existing,
            incoming,
            source_bank_id="agent-a",  # same source as before
            shared_bank_id="shared",
            neo4j=AsyncMock(),
        )
        # cross_agent_count stays at 1 — same agent re-promoting doesn't count
        assert result.properties["cross_agent_count"] == 1
        assert result.properties["confidence_tier"] == "agent_local"
        # evidence still accumulates (audit-only sum)
        assert result.evidence_count == 14

    @pytest.mark.asyncio
    async def test_centroid_running_mean_persisted(self, monkeypatch):
        existing = _shared_schema(sources=["agent-a"])
        incoming = _strong_schema()
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0, 0.0], "payload": {}})
        qdrant.upsert_schema_centroid = AsyncMock()

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "update_schema", AsyncMock())

        await reinforce_shared_schema(
            existing,
            incoming,
            source_bank_id="agent-b",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
            qdrant=qdrant,
            incoming_centroid=[0.0, 1.0, 0.0],
        )
        qdrant.upsert_schema_centroid.assert_awaited_once()
        kw = qdrant.upsert_schema_centroid.await_args.kwargs
        assert kw["schema_id"] == str(existing.id)
        # 1×(1,0,0) + 1×(0,1,0) → normalised → (~0.71, ~0.71, 0)
        c = kw["centroid"]
        assert c[0] == pytest.approx(0.7071, abs=0.01)
        assert c[1] == pytest.approx(0.7071, abs=0.01)


class TestBatchMatchVsCreate:
    @pytest.mark.asyncio
    async def test_match_promotes_via_reinforce_path(self, monkeypatch):
        agent_schema = _strong_schema()
        existing_shared = _shared_schema(sources=["agent-a"])
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [1.0, 0.0, 0.0]})
        qdrant.search_similar = AsyncMock(
            return_value=[
                {
                    "engram_id": str(existing_shared.id),
                    "score": 0.95,
                    "payload": {"schema_id": str(existing_shared.id)},
                }
            ]
        )
        qdrant.upsert_schema_centroid = AsyncMock()

        import hindsight_api.engine.multi_bank.schema_promoter as mod

        monkeypatch.setattr(mod, "list_active_schemas", AsyncMock(return_value=[agent_schema]))
        monkeypatch.setattr(
            mod,
            "get_schema",
            AsyncMock(return_value=existing_shared),
        )
        monkeypatch.setattr(mod, "update_schema", AsyncMock())
        monkeypatch.setattr(mod, "create_schema", AsyncMock())  # must NOT be called

        result = await promote_schemas_batch(
            source_bank_id="agent-b",
            shared_bank_id="shared",
            neo4j=AsyncMock(),
            qdrant=qdrant,
        )
        assert result.promoted == 0
        assert result.reinforced == 1
        assert result.reinforced_ids == [existing_shared.id]
        mod.create_schema.assert_not_called()
