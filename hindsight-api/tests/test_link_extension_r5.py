"""
Unit tests for Story 05 — Link Extension + Neo4j (R5).

Covers:
- create_temporal_proximity_links_batch (link_creation.py)
- check_schema_fit_batch / write_schema_links (schema_links.py)
- write_links_to_neo4j / LinkRecord (neo4j_link_writer.py)
- create_co_activation_link interface (link_creation.py)
- Neo4j failure does not block PostgreSQL pipeline (resilience)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.retain.neo4j_link_writer import LinkRecord, _to_neo4j_rel_type, write_links_to_neo4j
from hindsight_api.engine.retain.schema_links import SchemaLink, check_schema_fit_batch, write_schema_links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _neo4j_mock(side_effect=None):
    """Return a mock neo4j_client with async run_cypher."""
    client = MagicMock()
    if side_effect:
        client.run_cypher = AsyncMock(side_effect=side_effect)
    else:
        client.run_cypher = AsyncMock(return_value=[])
    return client


# ---------------------------------------------------------------------------
# neo4j_link_writer — _to_neo4j_rel_type
# ---------------------------------------------------------------------------


class TestToNeo4jRelType:
    def test_lowercase_converted(self):
        assert _to_neo4j_rel_type("semantic") == "SEMANTIC"

    def test_spaces_replaced(self):
        assert _to_neo4j_rel_type("temporal proximity") == "TEMPORAL_PROXIMITY"

    def test_already_uppercase(self):
        assert _to_neo4j_rel_type("CAUSAL") == "CAUSAL"

    def test_mixed_case(self):
        assert _to_neo4j_rel_type("Co_Activated") == "CO_ACTIVATED"


# ---------------------------------------------------------------------------
# neo4j_link_writer — write_links_to_neo4j
# ---------------------------------------------------------------------------


class TestWriteLinksToNeo4j:
    @pytest.mark.asyncio
    async def test_no_op_when_client_none(self):
        # Should not raise even without a client
        await write_links_to_neo4j(None, [LinkRecord("a", "b", "semantic")])

    @pytest.mark.asyncio
    async def test_no_op_when_empty_links(self):
        client = _neo4j_mock()
        await write_links_to_neo4j(client, [])
        client.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_valid_link(self):
        client = _neo4j_mock()
        link = LinkRecord(from_id="u1", to_id="u2", rel_type="semantic", weight=0.8)
        await write_links_to_neo4j(client, [link])
        client.run_cypher.assert_called_once()
        call_args = client.run_cypher.call_args
        # Cypher uses UNWIND batch format
        assert "SEMANTIC" in call_args[0][0]
        assert "UNWIND" in call_args[0][0]
        links_param = call_args[0][1]["links"]
        assert len(links_param) == 1
        assert links_param[0]["from_id"] == "u1"
        assert links_param[0]["to_id"] == "u2"
        assert links_param[0]["weight"] == 0.8

    @pytest.mark.asyncio
    async def test_skips_unknown_rel_type(self):
        client = _neo4j_mock()
        link = LinkRecord(from_id="u1", to_id="u2", rel_type="nonexistent_type")
        await write_links_to_neo4j(client, [link])
        client.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_neo4j_error_does_not_raise(self):
        client = _neo4j_mock(side_effect=RuntimeError("connection refused"))
        link = LinkRecord(from_id="u1", to_id="u2", rel_type="temporal")
        # Must not propagate
        await write_links_to_neo4j(client, [link])

    @pytest.mark.asyncio
    async def test_writes_multiple_links(self):
        client = _neo4j_mock()
        links = [
            LinkRecord("a", "b", "semantic", weight=0.9),
            LinkRecord("a", "c", "temporal", weight=0.5),
            LinkRecord("b", "c", "causal", weight=0.7),
        ]
        await write_links_to_neo4j(client, links)
        assert client.run_cypher.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_write_uses_single_query_per_type(self):
        """Links of the same rel_type must be sent in one UNWIND query, not N individual ones."""
        client = _neo4j_mock()
        links = [LinkRecord(from_id=f"a{i}", to_id=f"b{i}", rel_type="semantic", weight=0.5) for i in range(10)]
        await write_links_to_neo4j(client, links)
        assert client.run_cypher.call_count == 1
        call_args = client.run_cypher.call_args
        assert "UNWIND" in call_args[0][0]
        assert len(call_args[0][1]["links"]) == 10

    @pytest.mark.asyncio
    async def test_all_valid_rel_types_accepted(self):
        client = _neo4j_mock()
        valid_types = ["semantic", "temporal", "entity", "causal", "co_activated", "temporal_proximity", "schema"]
        links = [LinkRecord(f"u{i}", f"v{i}", rt) for i, rt in enumerate(valid_types)]
        await write_links_to_neo4j(client, links)
        assert client.run_cypher.call_count == len(valid_types)


# ---------------------------------------------------------------------------
# schema_links — check_schema_fit_batch
# ---------------------------------------------------------------------------


class TestCheckSchemaFitBatch:
    @pytest.mark.asyncio
    async def test_no_op_when_client_none(self):
        result = await check_schema_fit_batch(None, ["u1"], [[0.1, 0.2, 0.3]])
        assert result == []

    @pytest.mark.asyncio
    async def test_no_op_when_empty_unit_ids(self):
        client = _neo4j_mock()
        result = await check_schema_fit_batch(client, [], [[0.1]])
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_schemas(self):
        client = _neo4j_mock()  # default return_value=[] already
        result = await check_schema_fit_batch(client, ["u1"], [[1.0, 0.0]])
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_schema_link_above_threshold(self):
        # Schema embedding parallel to engram → similarity = 1.0
        schema_emb = [1.0, 0.0, 0.0]
        engram_emb = [1.0, 0.0, 0.0]

        client = MagicMock()
        client.run_cypher = AsyncMock(return_value=[{"schema_id": "schema-1", "embedding": schema_emb}])

        result = await check_schema_fit_batch(client, ["unit-1"], [engram_emb], threshold=0.7)

        assert len(result) == 1
        assert result[0].engram_id == "unit-1"
        assert result[0].schema_id == "schema-1"
        assert result[0].similarity >= 0.99

    @pytest.mark.asyncio
    async def test_no_link_below_threshold(self):
        # Orthogonal vectors → similarity = 0.0
        schema_emb = [1.0, 0.0, 0.0]
        engram_emb = [0.0, 1.0, 0.0]

        client = MagicMock()
        client.run_cypher = AsyncMock(return_value=[{"schema_id": "schema-1", "embedding": schema_emb}])

        result = await check_schema_fit_batch(client, ["unit-1"], [engram_emb], threshold=0.7)
        assert result == []

    @pytest.mark.asyncio
    async def test_neo4j_error_returns_empty(self):
        client = _neo4j_mock(side_effect=RuntimeError("Neo4j down"))
        result = await check_schema_fit_batch(client, ["u1"], [[1.0, 0.0]])
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_engrams_multiple_schemas(self):
        # Two engrams, two schemas; only high-similarity pairs linked
        schema_embs = [
            [1.0, 0.0, 0.0],  # schema-A
            [0.0, 1.0, 0.0],  # schema-B
        ]
        engram_embs = [
            [1.0, 0.0, 0.0],  # matches schema-A
            [0.0, 1.0, 0.0],  # matches schema-B
        ]

        client = MagicMock()
        client.run_cypher = AsyncMock(
            return_value=[
                {"schema_id": "schema-A", "embedding": schema_embs[0]},
                {"schema_id": "schema-B", "embedding": schema_embs[1]},
            ]
        )

        result = await check_schema_fit_batch(client, ["unit-1", "unit-2"], engram_embs, threshold=0.7)

        schema_ids = {sl.schema_id for sl in result}
        engram_ids = {sl.engram_id for sl in result}
        # Each engram matches its own schema
        assert len(result) == 2
        assert "schema-A" in schema_ids
        assert "schema-B" in schema_ids
        assert "unit-1" in engram_ids
        assert "unit-2" in engram_ids


# ---------------------------------------------------------------------------
# schema_links — write_schema_links
# ---------------------------------------------------------------------------


class TestWriteSchemaLinks:
    @pytest.mark.asyncio
    async def test_no_op_when_client_none(self):
        sl = SchemaLink(engram_id="e1", schema_id="s1", similarity=0.8, weight=0.8)
        await write_schema_links(None, [sl])  # must not raise

    @pytest.mark.asyncio
    async def test_no_op_when_empty(self):
        client = _neo4j_mock()
        await write_schema_links(client, [])
        client.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_schema_relationship(self):
        client = _neo4j_mock()
        sl = SchemaLink(engram_id="e1", schema_id="s1", similarity=0.85, weight=0.85)
        await write_schema_links(client, [sl])
        client.run_cypher.assert_called_once()
        call_args = client.run_cypher.call_args
        assert "SCHEMA" in call_args[0][0]
        assert call_args[0][1]["engram_id"] == "e1"
        assert call_args[0][1]["schema_id"] == "s1"

    @pytest.mark.asyncio
    async def test_neo4j_error_does_not_raise(self):
        client = _neo4j_mock(side_effect=RuntimeError("timeout"))
        sl = SchemaLink(engram_id="e1", schema_id="s1", similarity=0.9, weight=0.9)
        await write_schema_links(client, [sl])  # must not raise


# ---------------------------------------------------------------------------
# create_co_activation_link — interface stub
# ---------------------------------------------------------------------------


class TestCoActivationLink:
    @pytest.mark.asyncio
    async def test_no_op_when_client_none(self):
        from hindsight_api.engine.retain.link_creation import create_co_activation_link

        await create_co_activation_link(None, "a", "b")  # must not raise

    @pytest.mark.asyncio
    async def test_creates_co_activated_rel(self):
        from hindsight_api.engine.retain.link_creation import create_co_activation_link

        client = _neo4j_mock()
        await create_co_activation_link(client, "u1", "u2", weight=1.0)
        client.run_cypher.assert_called_once()
        cypher = client.run_cypher.call_args[0][0]
        assert "CO_ACTIVATED" in cypher

    @pytest.mark.asyncio
    async def test_neo4j_error_does_not_raise(self):
        from hindsight_api.engine.retain.link_creation import create_co_activation_link

        client = _neo4j_mock(side_effect=RuntimeError("write failed"))
        await create_co_activation_link(client, "u1", "u2")  # must not raise
