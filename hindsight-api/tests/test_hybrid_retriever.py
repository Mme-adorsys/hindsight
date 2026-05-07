"""Unit tests for Epic 25 Story 15 — HybridRetriever.

Tests run without live Qdrant / Neo4j / PostgreSQL: callers and lookups are
injected as AsyncMock / closures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.response_models import RetrievalMode
from hindsight_api.engine.schema.models import HyperSchemaModel, SchemaModel
from hindsight_api.engine.search.hybrid_retriever import HybridRetriever, RetrievalHit
from hindsight_api.engine.session.mode_config import MODE_PROFILES


def _engram_payload(engram_id: UUID, **extra: Any) -> dict[str, Any]:
    return {
        "engram_id": str(engram_id),
        "kind": "engram",
        "bank_id": "agent-1",
        **extra,
    }


def _schema_payload(schema_id: UUID, label: str = "Schema", **extra: Any) -> dict[str, Any]:
    return {
        "schema_id": str(schema_id),
        "kind": "schema",
        "schema_label": label,
        "bank_id": "agent-1",
        **extra,
    }


def _make_qdrant(hits: list[dict[str, Any]]) -> AsyncMock:
    qdrant = AsyncMock()
    qdrant.search_similar = AsyncMock(return_value=hits)
    return qdrant


# ---------------------------------------------------------------------------
# RetrievalHit — model basics
# ---------------------------------------------------------------------------


class TestRetrievalHit:
    def test_engram_hit_defaults(self):
        eid = uuid4()
        hit = RetrievalHit(kind="engram", id=eid, score=0.91)
        assert hit.kind == "engram"
        assert hit.id == eid
        assert hit.score == pytest.approx(0.91)
        assert hit.text is None
        assert hit.tags == []
        assert hit.evidence_engram_ids == []

    def test_invalid_kind_rejected(self):
        with pytest.raises(Exception):
            RetrievalHit(kind="other", id=uuid4(), score=0.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HybridRetriever.retrieve — search wiring
# ---------------------------------------------------------------------------


class TestHybridRetrieverSearch:
    @pytest.mark.asyncio
    async def test_calls_qdrant_without_kind_filter(self):
        qdrant = _make_qdrant([])
        retriever = HybridRetriever(qdrant=qdrant)
        await retriever.retrieve([0.1] * 384, "agent-1", k=7)

        qdrant.search_similar.assert_awaited_once()
        kwargs = qdrant.search_similar.await_args.kwargs
        assert kwargs["limit"] == 7
        assert kwargs["kind"] is None  # mixed search — concept §3
        # bank_id filter must be in the must list
        must = kwargs["filters"]["must"]
        assert any(c == {"key": "bank_id", "match": {"value": "agent-1"}} for c in must)

    @pytest.mark.asyncio
    async def test_tag_filter_passed_through(self):
        qdrant = _make_qdrant([])
        retriever = HybridRetriever(qdrant=qdrant)
        await retriever.retrieve([0.1] * 384, "agent-1", tags=["coffee", "morning"])

        must = qdrant.search_similar.await_args.kwargs["filters"]["must"]
        keys_values = [(c["key"], c["match"]["value"]) for c in must]
        assert ("tags", "coffee") in keys_values
        assert ("tags", "morning") in keys_values

    @pytest.mark.asyncio
    async def test_no_hits_returns_empty(self):
        retriever = HybridRetriever(qdrant=_make_qdrant([]))
        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert hits == []

    @pytest.mark.asyncio
    async def test_unparseable_id_skipped_not_raised(self):
        bad = {"score": 0.5, "payload": {"kind": "engram", "engram_id": "not-a-uuid"}}
        retriever = HybridRetriever(qdrant=_make_qdrant([bad]))
        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert hits == []


# ---------------------------------------------------------------------------
# Engram-only enrichment
# ---------------------------------------------------------------------------


class TestEngramEnrichment:
    @pytest.mark.asyncio
    async def test_engram_hit_enriched_via_lookup(self):
        eid = uuid4()
        raw = [
            {
                "engram_id": str(eid),
                "score": 0.88,
                "payload": _engram_payload(eid, tags=["a"]),
            }
        ]
        engram_lookup = AsyncMock(
            return_value={
                eid: {
                    "text": "morning coffee meeting with anna",
                    "fact_type": "experience",
                    "context": "work",
                    "tags": ["coffee", "morning"],
                }
            }
        )
        retriever = HybridRetriever(qdrant=_make_qdrant(raw), engram_lookup=engram_lookup)

        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert len(hits) == 1
        hit = hits[0]
        assert hit.kind == "engram"
        assert hit.id == eid
        assert hit.text == "morning coffee meeting with anna"
        assert hit.fact_type == "experience"
        assert hit.context == "work"
        assert hit.tags == ["coffee", "morning"]
        engram_lookup.assert_awaited_once()
        called_ids, called_bank = engram_lookup.await_args.args
        assert called_ids == [eid]
        assert called_bank == "agent-1"

    @pytest.mark.asyncio
    async def test_engram_lookup_missing_row_leaves_defaults(self):
        eid = uuid4()
        raw = [{"engram_id": str(eid), "score": 0.5, "payload": _engram_payload(eid)}]
        retriever = HybridRetriever(
            qdrant=_make_qdrant(raw),
            engram_lookup=AsyncMock(return_value={}),
        )
        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert hits[0].text is None
        assert hits[0].tags == []

    @pytest.mark.asyncio
    async def test_no_pg_no_lookup_skips_enrichment_silently(self):
        eid = uuid4()
        raw = [{"engram_id": str(eid), "score": 0.5, "payload": _engram_payload(eid)}]
        retriever = HybridRetriever(qdrant=_make_qdrant(raw))
        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        # base hit still emitted; just no enrichment fields populated
        assert len(hits) == 1
        assert hits[0].text is None


# ---------------------------------------------------------------------------
# Schema-only enrichment
# ---------------------------------------------------------------------------


def _make_schema(*, label: str = "Schema") -> SchemaModel | HyperSchemaModel:
    cls = HyperSchemaModel if label == "HyperSchema" else SchemaModel
    return cls(
        id=uuid4(),
        description="morning coffee 1:1 ritual",
        properties={"participant_count": {"min": 2, "max": 2}},
        evidence_engram_ids=[uuid4(), uuid4(), uuid4()],
        evidence_count=12,
        created_at=datetime.now(timezone.utc),
    )


class TestSchemaEnrichment:
    @pytest.mark.asyncio
    async def test_schema_hit_enriched_via_lookup(self):
        schema = _make_schema()
        raw = [{"engram_id": str(schema.id), "score": 0.92, "payload": _schema_payload(schema.id)}]
        schema_lookup = AsyncMock(return_value=schema)
        retriever = HybridRetriever(qdrant=_make_qdrant(raw), schema_lookup=schema_lookup)

        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert len(hits) == 1
        hit = hits[0]
        assert hit.kind == "schema"
        assert hit.id == schema.id
        assert hit.description == "morning coffee 1:1 ritual"
        assert hit.properties == {"participant_count": {"min": 2, "max": 2}}
        assert hit.evidence_engram_ids == schema.evidence_engram_ids
        assert hit.evidence_count == 12
        assert hit.schema_label == "Schema"
        schema_lookup.assert_awaited_once_with(schema.id, "Schema")

    @pytest.mark.asyncio
    async def test_hyper_schema_label_routed(self):
        schema = _make_schema(label="HyperSchema")
        raw = [
            {
                "engram_id": str(schema.id),
                "score": 0.9,
                "payload": _schema_payload(schema.id, label="HyperSchema"),
            }
        ]
        schema_lookup = AsyncMock(return_value=schema)
        retriever = HybridRetriever(qdrant=_make_qdrant(raw), schema_lookup=schema_lookup)

        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert hits[0].schema_label == "HyperSchema"
        schema_lookup.assert_awaited_once_with(schema.id, "HyperSchema")

    @pytest.mark.asyncio
    async def test_schema_lookup_failure_logged_not_raised(self, caplog):
        sid = uuid4()
        raw = [{"engram_id": str(sid), "score": 0.7, "payload": _schema_payload(sid)}]
        schema_lookup = AsyncMock(side_effect=RuntimeError("neo4j down"))
        retriever = HybridRetriever(qdrant=_make_qdrant(raw), schema_lookup=schema_lookup)

        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        # base hit still emitted; enrichment skipped
        assert len(hits) == 1
        assert hits[0].description is None

    @pytest.mark.asyncio
    async def test_schema_lookup_missing_node_leaves_defaults(self):
        sid = uuid4()
        raw = [{"engram_id": str(sid), "score": 0.7, "payload": _schema_payload(sid)}]
        retriever = HybridRetriever(
            qdrant=_make_qdrant(raw),
            schema_lookup=AsyncMock(return_value=None),
        )
        hits = await retriever.retrieve([0.0] * 384, "agent-1")
        assert hits[0].description is None
        assert hits[0].properties == {}


# ---------------------------------------------------------------------------
# Mixed hits — Story 15 acceptance criterion 6c
# ---------------------------------------------------------------------------


class TestModeWeighting:
    """Story 17 — Mode-abhängige Schema/Engram-Gewichtung."""

    def test_mode_profile_drift_guard(self):
        """Spec values pinned: regression alarm if anyone tweaks blindly."""
        p = MODE_PROFILES[RetrievalMode.PRECISION]
        e = MODE_PROFILES[RetrievalMode.EXPLORATION]
        a = MODE_PROFILES[RetrievalMode.ANALOGY]
        v = MODE_PROFILES[RetrievalMode.VALIDATION]
        assert (p.w_schema, p.w_engram) == (1.2, 0.9)
        assert (e.w_schema, e.w_engram) == (0.8, 1.2)
        assert (a.w_schema, a.w_engram) == (1.1, 1.0)
        assert (v.w_schema, v.w_engram) == (1.0, 1.0)

    @pytest.mark.asyncio
    async def test_precision_promotes_schema_above_tied_engram(self):
        engram_id = uuid4()
        schema_id = uuid4()
        # Equal raw scores — Precision (1.2 vs 0.9) must put schema first.
        raw = [
            {"engram_id": str(engram_id), "score": 0.80, "payload": _engram_payload(engram_id)},
            {"engram_id": str(schema_id), "score": 0.80, "payload": _schema_payload(schema_id)},
        ]
        retriever = HybridRetriever(qdrant=_make_qdrant(raw))
        hits = await retriever.retrieve([0.0] * 384, "agent-1", mode=RetrievalMode.PRECISION)
        assert [h.kind for h in hits] == ["schema", "engram"]
        assert hits[0].score == pytest.approx(0.80 * 1.2)
        assert hits[1].score == pytest.approx(0.80 * 0.9)

    @pytest.mark.asyncio
    async def test_exploration_promotes_engram_above_tied_schema(self):
        engram_id = uuid4()
        schema_id = uuid4()
        raw = [
            {"engram_id": str(schema_id), "score": 0.80, "payload": _schema_payload(schema_id)},
            {"engram_id": str(engram_id), "score": 0.80, "payload": _engram_payload(engram_id)},
        ]
        retriever = HybridRetriever(qdrant=_make_qdrant(raw))
        hits = await retriever.retrieve([0.0] * 384, "agent-1", mode=RetrievalMode.EXPLORATION)
        assert [h.kind for h in hits] == ["engram", "schema"]
        assert hits[0].score == pytest.approx(0.80 * 1.2)
        assert hits[1].score == pytest.approx(0.80 * 0.8)

    @pytest.mark.asyncio
    async def test_validation_neutral_keeps_qdrant_order(self):
        engram_id = uuid4()
        schema_id = uuid4()
        raw = [
            {"engram_id": str(engram_id), "score": 0.91, "payload": _engram_payload(engram_id)},
            {"engram_id": str(schema_id), "score": 0.85, "payload": _schema_payload(schema_id)},
        ]
        retriever = HybridRetriever(qdrant=_make_qdrant(raw))
        hits = await retriever.retrieve([0.0] * 384, "agent-1", mode=RetrievalMode.VALIDATION)
        # Validation has 1.0/1.0 — top-ranked engram stays first.
        assert [h.kind for h in hits] == ["engram", "schema"]
        assert hits[0].score == pytest.approx(0.91)

    @pytest.mark.asyncio
    async def test_re_sort_picks_winner_after_weighting(self):
        """Schema with lower raw score can overtake engram under Precision."""
        engram_id = uuid4()
        schema_id = uuid4()
        # raw engram 0.95, raw schema 0.85.
        # Precision: engram=0.855, schema=1.02 → schema wins.
        raw = [
            {"engram_id": str(engram_id), "score": 0.95, "payload": _engram_payload(engram_id)},
            {"engram_id": str(schema_id), "score": 0.85, "payload": _schema_payload(schema_id)},
        ]
        retriever = HybridRetriever(qdrant=_make_qdrant(raw))
        hits = await retriever.retrieve([0.0] * 384, "agent-1", mode=RetrievalMode.PRECISION)
        assert hits[0].kind == "schema"
        assert hits[0].score > hits[1].score


class TestMixedHits:
    @pytest.mark.asyncio
    async def test_mixed_hits_preserve_qdrant_order(self):
        engram_id = uuid4()
        schema_id = uuid4()
        raw = [
            {"engram_id": str(schema_id), "score": 0.95, "payload": _schema_payload(schema_id)},
            {"engram_id": str(engram_id), "score": 0.83, "payload": _engram_payload(engram_id)},
        ]
        schema = SchemaModel(
            id=schema_id,
            description="ritual",
            properties={},
            evidence_engram_ids=[],
            evidence_count=4,
        )
        engram_lookup = AsyncMock(
            return_value={
                engram_id: {
                    "text": "specific instance",
                    "fact_type": "world",
                    "context": None,
                    "tags": [],
                }
            }
        )
        retriever = HybridRetriever(
            qdrant=_make_qdrant(raw),
            schema_lookup=AsyncMock(return_value=schema),
            engram_lookup=engram_lookup,
        )
        hits = await retriever.retrieve([0.0] * 384, "agent-1", k=5)
        assert [h.kind for h in hits] == ["schema", "engram"]
        # both enrichment paths fired
        assert hits[0].description == "ritual"
        assert hits[1].text == "specific instance"
        # Qdrant scores carried over verbatim — re-weighting belongs to Story 17.
        assert hits[0].score == pytest.approx(0.95)
        assert hits[1].score == pytest.approx(0.83)
