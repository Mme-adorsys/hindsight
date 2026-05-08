"""Unit tests for Epic 25 Story 27 — Control Plane Schema-Explorer endpoints.

Calls the route handlers directly via the FastAPI router with a mocked
``request.app.state``; live Neo4j / Qdrant / PG are not required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from hindsight_api.api.cp_schemas import (
    SchemaDetailDTO,
    SchemaListItemDTO,
    get_schema_detail,
    get_schema_evidence,
    list_bank_hyper_schemas,
    list_bank_schemas,
)
from hindsight_api.engine.schema.models import HyperSchemaModel, SchemaModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema(*, bank_id: str | None = "agent-a", description: str = "ritual", evidence: int = 8) -> SchemaModel:
    props: dict = {"format": "1on1"}
    if bank_id is not None:
        props["bank_id"] = bank_id
    return SchemaModel(
        id=uuid4(),
        description=description,
        properties=props,
        evidence_engram_ids=[uuid4(), uuid4(), uuid4()],
        evidence_count=evidence,
        cycles_survived=3,
        last_reinforced_at=datetime.now(timezone.utc),
    )


def _request(neo4j=None, qdrant=None, pool=None):
    state = SimpleNamespace(neo4j=neo4j, qdrant=qdrant, memory=SimpleNamespace(_pool=pool))
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


# When the route handler is called directly (not via FastAPI's DI), Query()
# defaults stay un-resolved. Pass explicit query-arg defaults via this kwargs
# bundle so the handler logic runs against ints/strings, not Query objects.
_LIST_DEFAULTS: dict = {
    "status": "active",
    "sort_by": "last_reinforced_at",
    "limit": 100,
    "offset": 0,
}


# ---------------------------------------------------------------------------
# list_bank_schemas
# ---------------------------------------------------------------------------


class TestListBankSchemas:
    @pytest.mark.asyncio
    async def test_filters_by_bank_id(self, monkeypatch):
        in_bank = _schema(bank_id="agent-a")
        out_of_bank = _schema(bank_id="agent-b")
        no_stamp = _schema(bank_id=None)

        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(
            mod,
            "list_active_schemas",
            AsyncMock(return_value=[in_bank, out_of_bank, no_stamp]),
        )

        result = await list_bank_schemas("agent-a", _request(neo4j=AsyncMock()), **_LIST_DEFAULTS)
        ids = {r.id for r in result}
        # in-bank + no-stamp pass through; out-of-bank filtered.
        assert in_bank.id in ids
        assert no_stamp.id in ids
        assert out_of_bank.id not in ids

    @pytest.mark.asyncio
    async def test_sort_by_evidence_count(self, monkeypatch):
        a = _schema(evidence=3, description="a")
        b = _schema(evidence=10, description="b")
        c = _schema(evidence=1, description="c")
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "list_active_schemas", AsyncMock(return_value=[a, b, c]))

        kwargs = {**_LIST_DEFAULTS, "sort_by": "evidence_count"}
        result = await list_bank_schemas("agent-a", _request(neo4j=AsyncMock()), **kwargs)
        assert [r.description for r in result] == ["b", "a", "c"]

    @pytest.mark.asyncio
    async def test_archived_returns_empty(self, monkeypatch):
        a = _schema()
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "list_active_schemas", AsyncMock(return_value=[a]))
        kwargs = {**_LIST_DEFAULTS, "status": "archived"}
        result = await list_bank_schemas("agent-a", _request(neo4j=AsyncMock()), **kwargs)
        assert result == []

    @pytest.mark.asyncio
    async def test_neo4j_missing_returns_503(self):
        with pytest.raises(HTTPException) as exc:
            await list_bank_schemas("agent-a", _request(neo4j=None), **_LIST_DEFAULTS)
        assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# get_schema_detail
# ---------------------------------------------------------------------------


class TestSchemaDetail:
    @pytest.mark.asyncio
    async def test_returns_full_detail(self, monkeypatch):
        s = _schema()
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=s))

        out = await get_schema_detail(s.id, _request(neo4j=AsyncMock()), include_centroid=False)
        assert isinstance(out, SchemaDetailDTO)
        assert out.id == s.id
        assert out.evidence_count == s.evidence_count
        assert out.properties == s.properties
        assert out.centroid is None  # not requested

    @pytest.mark.asyncio
    async def test_includes_centroid_on_request(self, monkeypatch):
        s = _schema()
        s = s.model_copy(update={"centroid_qdrant_id": uuid4()})
        qdrant = AsyncMock()
        qdrant.get_by_id = AsyncMock(return_value={"vector": [0.1, 0.2, 0.3]})
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=s))

        out = await get_schema_detail(s.id, _request(neo4j=AsyncMock(), qdrant=qdrant), include_centroid=True)
        assert out.centroid == [0.1, 0.2, 0.3]
        qdrant.get_by_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_404_when_missing(self, monkeypatch):
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=None))

        with pytest.raises(HTTPException) as exc:
            await get_schema_detail(uuid4(), _request(neo4j=AsyncMock()), include_centroid=False)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_falls_back_to_hyperschema_label(self, monkeypatch):
        h = HyperSchemaModel(
            id=uuid4(),
            description="hyper",
            properties={},
            evidence_count=10,
        )
        # Schema-label miss, HyperSchema-label hit.
        import hindsight_api.api.cp_schemas as mod

        get_mock = AsyncMock(side_effect=[None, h])
        monkeypatch.setattr(mod, "get_schema_node", get_mock)

        out = await get_schema_detail(h.id, _request(neo4j=AsyncMock()), include_centroid=False)
        assert out.id == h.id
        assert out.description == "hyper"


# ---------------------------------------------------------------------------
# get_schema_evidence
# ---------------------------------------------------------------------------


class TestSchemaEvidence:
    @pytest.mark.asyncio
    async def test_resolves_top_n_in_order(self, monkeypatch):
        eids = [uuid4() for _ in range(4)]
        s = SchemaModel(
            id=uuid4(),
            description="d",
            evidence_engram_ids=eids,
            evidence_count=4,
        )
        rows = {
            eid: {
                "text": f"text-{i}",
                "fact_type": "world",
                "context": None,
                "strength": 0.5,
                "tags": [f"tag-{i}"],
            }
            for i, eid in enumerate(eids)
        }
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=s))
        monkeypatch.setattr(mod, "fetch_active_engrams_by_ids", AsyncMock(return_value=rows))

        pool = MagicMock()
        out = await get_schema_evidence(s.id, _request(neo4j=AsyncMock(), pool=pool), bank_id="agent-a", max_n=3)
        # Order preserved; capped at max_n=3.
        assert [e.id for e in out] == eids[:3]
        assert [e.text for e in out] == ["text-0", "text-1", "text-2"]

    @pytest.mark.asyncio
    async def test_empty_evidence_returns_empty(self, monkeypatch):
        s = SchemaModel(id=uuid4(), description="d", evidence_engram_ids=[], evidence_count=0)
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=s))
        out = await get_schema_evidence(
            s.id, _request(neo4j=AsyncMock(), pool=MagicMock()), bank_id="agent-a", max_n=5
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_404_when_schema_missing(self, monkeypatch):
        import hindsight_api.api.cp_schemas as mod

        monkeypatch.setattr(mod, "get_schema_node", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as exc:
            await get_schema_evidence(
                uuid4(), _request(neo4j=AsyncMock(), pool=MagicMock()), bank_id="agent-a", max_n=5
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# list_bank_hyper_schemas
# ---------------------------------------------------------------------------


class TestHyperSchemas:
    @pytest.mark.asyncio
    async def test_returns_with_children_ids(self):
        hyper_id = uuid4()
        child_a = uuid4()
        child_b = uuid4()
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock(
            return_value=[
                {
                    "hp": {
                        "id": str(hyper_id),
                        "description": "hyper-a",
                        "properties_json": '{"bank_id": "agent-a"}',
                        "evidence_count": 9,
                        "cycles_survived": 4,
                        "status": "active",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "child_ids": [str(child_a), str(child_b)],
                }
            ]
        )
        out = await list_bank_hyper_schemas("agent-a", _request(neo4j=neo4j), limit=50)
        assert len(out) == 1
        h = out[0]
        assert h.id == hyper_id
        assert set(h.children_ids) == {child_a, child_b}
        assert h.evidence_count == 9

    @pytest.mark.asyncio
    async def test_filters_by_bank(self):
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock(
            return_value=[
                {
                    "hp": {
                        "id": str(uuid4()),
                        "description": "wrong-bank",
                        "properties_json": '{"bank_id": "agent-b"}',
                        "evidence_count": 5,
                        "cycles_survived": 2,
                        "status": "active",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "child_ids": [],
                }
            ]
        )
        out = await list_bank_hyper_schemas("agent-a", _request(neo4j=neo4j), limit=50)
        assert out == []
