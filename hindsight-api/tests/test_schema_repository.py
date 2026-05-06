"""Unit + integration tests for engine/schema/schema_repository.py (Epic 25 Story 01).

Unit tests mock Neo4jEngineClient.run_cypher; integration tests run against a
real Neo4j when NEO4J_BOLT_URL is reachable (graceful skip otherwise) and
exercise real constraint/index creation through ensure_schema().
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest

from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.schema import (
    HyperSchemaModel,
    SchemaModel,
    archive_schema,
    create_schema,
    get_schema,
    link_specialization,
    list_active_schemas,
    materialize_schema_node,
    update_schema,
)
from hindsight_api.engine.schema.schema_repository import SchemaRepositoryError

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestSchemaModelSerialisation:
    def test_defaults(self):
        m = SchemaModel()
        assert m.status == "active"
        assert m.cycles_survived == 0
        assert m.evidence_count == 0
        assert m.evidence_engram_ids == []
        assert m.last_reinforced_at is None
        assert m.created_at is not None

    def test_to_neo4j_props_serialises_uuids_and_dicts(self):
        eid1, eid2 = uuid.uuid4(), uuid.uuid4()
        centroid = uuid.uuid4()
        m = SchemaModel(
            description="coffee meeting",
            properties={"duration_avg": 45, "mood": "productive"},
            centroid_qdrant_id=centroid,
            evidence_engram_ids=[eid1, eid2],
            evidence_count=7,
        )
        props = m.to_neo4j_props()
        assert props["id"] == str(m.id)
        assert props["centroid_qdrant_id"] == str(centroid)
        assert props["evidence_engram_ids"] == [str(eid1), str(eid2)]
        # Nested dict is JSON-encoded so Neo4j (which forbids nested maps) accepts it.
        assert isinstance(props["properties_json"], str)
        assert "duration_avg" in props["properties_json"]
        assert isinstance(props["created_at"], str)  # ISO string

    def test_round_trip(self):
        original = SchemaModel(
            description="afternoon walk",
            properties={"location": "park", "duration": 30},
            evidence_engram_ids=[uuid.uuid4() for _ in range(3)],
            evidence_count=12,
            cycles_survived=4,
        )
        rebuilt = SchemaModel.from_neo4j_props(original.to_neo4j_props())
        assert rebuilt.id == original.id
        assert rebuilt.description == original.description
        assert rebuilt.properties == original.properties
        assert rebuilt.evidence_engram_ids == original.evidence_engram_ids
        assert rebuilt.evidence_count == original.evidence_count
        assert rebuilt.cycles_survived == original.cycles_survived
        assert rebuilt.status == original.status

    def test_hyperschema_round_trip(self):
        original = HyperSchemaModel(description="generic meeting")
        rebuilt = HyperSchemaModel.from_neo4j_props(original.to_neo4j_props())
        assert rebuilt.id == original.id
        assert rebuilt.description == original.description


# ---------------------------------------------------------------------------
# Repository unit tests (mocked client)
# ---------------------------------------------------------------------------


def _mock_client(rows):
    client = AsyncMock()
    client.run_cypher = AsyncMock(return_value=rows)
    return client


class TestRepositoryUnit:
    async def test_create_schema_uses_merge(self):
        m = SchemaModel(description="x")
        client = _mock_client([{"p": m.to_neo4j_props()}])
        result = await create_schema(client, m)
        client.run_cypher.assert_awaited_once()
        cypher, _ = client.run_cypher.call_args.args[0], client.run_cypher.call_args.kwargs
        assert "MERGE (s:Schema {id: $id})" in cypher
        assert "ON CREATE SET s.created_at = $created_at" in cypher
        assert result.id == m.id

    async def test_create_hyperschema_uses_correct_label(self):
        m = HyperSchemaModel(description="generic")
        client = _mock_client([{"p": m.to_neo4j_props()}])
        await create_schema(client, m, label="HyperSchema")
        cypher = client.run_cypher.call_args.args[0]
        assert "MERGE (s:HyperSchema" in cypher

    async def test_create_schema_invalid_label_raises(self):
        m = SchemaModel()
        client = _mock_client([])
        with pytest.raises(SchemaRepositoryError):
            await create_schema(client, m, label="Engram")

    async def test_create_schema_no_rows_raises(self):
        m = SchemaModel()
        client = _mock_client([])
        with pytest.raises(SchemaRepositoryError):
            await create_schema(client, m)

    async def test_get_schema_returns_none_when_missing(self):
        client = _mock_client([])
        result = await get_schema(client, uuid.uuid4())
        assert result is None

    async def test_get_schema_returns_model(self):
        m = SchemaModel(description="hit")
        client = _mock_client([{"p": m.to_neo4j_props()}])
        result = await get_schema(client, m.id)
        assert result is not None
        assert result.id == m.id
        assert result.description == "hit"

    async def test_update_schema_rejects_immutable_fields(self):
        client = _mock_client([])
        with pytest.raises(SchemaRepositoryError):
            await update_schema(client, uuid.uuid4(), {"id": "new", "description": "x"})
        with pytest.raises(SchemaRepositoryError):
            await update_schema(client, uuid.uuid4(), {"created_at": "now"})

    async def test_update_schema_partial_set_clauses(self):
        m = SchemaModel(description="updated")
        client = _mock_client([{"p": m.to_neo4j_props()}])
        await update_schema(client, m.id, {"description": "updated", "evidence_count": 9})
        cypher = client.run_cypher.call_args.args[0]
        # Both fields must appear in SET clause.
        assert "s.description = $description" in cypher
        assert "s.evidence_count = $evidence_count" in cypher

    async def test_update_schema_empty_partial_returns_current(self):
        m = SchemaModel()
        client = _mock_client([{"p": m.to_neo4j_props()}])
        result = await update_schema(client, m.id, {})
        # No SET query — falls through to get_schema; still a single run_cypher call.
        client.run_cypher.assert_awaited_once()
        assert result is not None and result.id == m.id

    async def test_archive_schema_sets_status(self):
        m = SchemaModel(status="archived")
        client = _mock_client([{"p": m.to_neo4j_props()}])
        result = await archive_schema(client, m.id)
        cypher = client.run_cypher.call_args.args[0]
        assert "s.status = $status" in cypher
        assert client.run_cypher.call_args.kwargs["params"]["status"] == "archived"
        assert result is not None and result.status == "archived"

    async def test_list_active_schemas_orders_by_last_reinforced(self):
        m = SchemaModel()
        client = _mock_client([{"p": m.to_neo4j_props()}])
        await list_active_schemas(client, limit=10)
        cypher = client.run_cypher.call_args.args[0]
        assert "status: 'active'" in cypher
        assert "ORDER BY s.last_reinforced_at DESC" in cypher
        assert client.run_cypher.call_args.kwargs["params"]["limit"] == 10

    async def test_link_specialization_directional(self):
        client = _mock_client([])
        sid, hid = uuid.uuid4(), uuid.uuid4()
        await link_specialization(client, sid, hid)
        cypher = client.run_cypher.call_args.args[0]
        assert "MATCH (s:Schema {id: $schema_id})" in cypher
        assert "(h:HyperSchema {id: $hyper_id})" in cypher
        assert "MERGE (s)-[:SPECIALIZES]->(h)" in cypher

    async def test_materialize_dispatches_by_class(self):
        # SchemaModel → :Schema label
        s = SchemaModel()
        client_s = _mock_client([{"p": s.to_neo4j_props()}])
        await materialize_schema_node(client_s, s)
        assert "MERGE (s:Schema {id: $id})" in client_s.run_cypher.call_args.args[0]

        # HyperSchemaModel → :HyperSchema label
        h = HyperSchemaModel()
        client_h = _mock_client([{"p": h.to_neo4j_props()}])
        await materialize_schema_node(client_h, h)
        assert "MERGE (s:HyperSchema {id: $id})" in client_h.run_cypher.call_args.args[0]


# ---------------------------------------------------------------------------
# Integration tests (real Neo4j; gated by env)
# ---------------------------------------------------------------------------


NEO4J_BOLT_URL = os.getenv("NEO4J_BOLT_URL", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "hindsight")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


@pytest.fixture
async def neo4j_client():
    client = Neo4jEngineClient(
        bolt_url=NEO4J_BOLT_URL,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
    )
    try:
        await client.connect()
        await client.ensure_schema()
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Neo4j unavailable at {NEO4J_BOLT_URL}: {exc}")
    yield client
    # Best-effort cleanup of any nodes this test class created.
    try:
        await client.run_cypher("MATCH (n) WHERE n:Schema OR n:HyperSchema DETACH DELETE n")
    finally:
        await client.close()


@pytest.mark.integration
class TestRepositoryIntegration:
    async def test_create_then_get(self, neo4j_client):
        m = SchemaModel(description="integration", evidence_count=2)
        await create_schema(neo4j_client, m)
        fetched = await get_schema(neo4j_client, m.id)
        assert fetched is not None
        assert fetched.id == m.id
        assert fetched.description == "integration"
        assert fetched.evidence_count == 2

    async def test_update_then_archive(self, neo4j_client):
        m = SchemaModel(description="initial")
        await create_schema(neo4j_client, m)
        updated = await update_schema(neo4j_client, m.id, {"description": "renamed"})
        assert updated is not None and updated.description == "renamed"
        archived = await archive_schema(neo4j_client, m.id)
        assert archived is not None and archived.status == "archived"

    async def test_duplicate_id_merges_not_duplicates(self, neo4j_client):
        m = SchemaModel(description="first")
        await create_schema(neo4j_client, m)
        # Re-creating with same id should MERGE (idempotent), not raise.
        m2 = SchemaModel(id=m.id, description="second")
        await create_schema(neo4j_client, m2)
        rows = await neo4j_client.run_cypher(
            "MATCH (s:Schema {id: $id}) RETURN count(s) AS c",
            params={"id": str(m.id)},
        )
        assert rows[0]["c"] == 1
        # And the description was overwritten.
        fetched = await get_schema(neo4j_client, m.id)
        assert fetched is not None and fetched.description == "second"

    async def test_specialization_edge(self, neo4j_client):
        s = SchemaModel(description="specific")
        h = HyperSchemaModel(description="general")
        await create_schema(neo4j_client, s, label="Schema")
        await create_schema(neo4j_client, h, label="HyperSchema")
        await link_specialization(neo4j_client, s.id, h.id)
        rows = await neo4j_client.run_cypher(
            "MATCH (s:Schema {id: $sid})-[r:SPECIALIZES]->(h:HyperSchema {id: $hid}) RETURN count(r) AS c",
            params={"sid": str(s.id), "hid": str(h.id)},
        )
        assert rows[0]["c"] == 1
        # Idempotent: re-linking does not duplicate.
        await link_specialization(neo4j_client, s.id, h.id)
        rows = await neo4j_client.run_cypher(
            "MATCH (s:Schema {id: $sid})-[r:SPECIALIZES]->(h:HyperSchema {id: $hid}) RETURN count(r) AS c",
            params={"sid": str(s.id), "hid": str(h.id)},
        )
        assert rows[0]["c"] == 1

    async def test_list_active_schemas_only_active(self, neo4j_client):
        active = SchemaModel(description="alive")
        archived = SchemaModel(description="ghost", status="archived")
        await create_schema(neo4j_client, active)
        await create_schema(neo4j_client, archived)
        listed = await list_active_schemas(neo4j_client, limit=50)
        ids = {s.id for s in listed}
        assert active.id in ids
        assert archived.id not in ids
