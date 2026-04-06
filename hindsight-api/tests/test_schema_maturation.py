"""
Unit tests for NCR Phase 3 — R2+R3 Maturation & Abstraction.

Epic 13, Story 02, Task T5.

All tests are pure-unit: Neo4j, Qdrant, asyncpg Pool, and the LLM are mocked.
Covers:
- ncr_cycles_survived incremented each NCR cycle
- No maturation below threshold K
- Maturation at exactly K cycles → Schema created
- Cohesion degradation resets cycles (too many members archived)
- LLM abstraction called once per matured cluster
- Schema links created for all active members
- ClusterCandidate deleted after Schema creation
- Errors (LLM, Neo4j, Qdrant) logged but not raised
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.schema_maturation import (
    MaturationResult,
    MaturedSchema,
    _SchemaAbstraction,
    _active_member_ratio,
    run_maturation,
)


# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------


class TestActiveMemberRatio:
    def _rows(self, *statuses):
        return [{"engram_id": f"e{i}", "status": s, "layer": "neocortex"} for i, s in enumerate(statuses)]

    def test_all_active(self):
        rows = self._rows("active", "active", "active")
        assert _active_member_ratio(rows, ["e0", "e1", "e2"]) == pytest.approx(1.0)

    def test_all_archived(self):
        rows = self._rows("archived", "archived")
        assert _active_member_ratio(rows, ["e0", "e1"]) == pytest.approx(0.0)

    def test_partial(self):
        rows = self._rows("active", "archived", "active")
        assert _active_member_ratio(rows, ["e0", "e1", "e2"]) == pytest.approx(2 / 3)

    def test_empty_member_ids(self):
        assert _active_member_ratio([], []) == pytest.approx(0.0)

    def test_wrong_layer_not_counted(self):
        rows = [{"engram_id": "e0", "status": "active", "layer": "buffer"}]
        assert _active_member_ratio(rows, ["e0"]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Fixtures / mock builders
# ---------------------------------------------------------------------------


def _make_candidate(candidate_id: str, member_ids: list[str], cycles: int):
    return {
        "candidate_id": candidate_id,
        "member_ids": member_ids,
        "cohesion": 3.0,
        "ncr_cycles_survived": cycles,
    }


def _make_member_rows(member_ids: list[str], status: str = "active"):
    return [{"engram_id": mid, "status": status, "layer": "neocortex"} for mid in member_ids]


def _make_neo4j(candidates=None, member_rows=None, increment_result=None):
    """Build a minimal Neo4j mock that routes queries to correct response."""
    client = MagicMock()

    async def run_cypher(query: str, params=None):
        # Increment query has both MATCH (c:ClusterCandidate and SET — must check SET first
        if "SET c.ncr_cycles_survived = c.ncr_cycles_survived + 1" in query:
            return increment_result or [{"ncr_cycles_survived": 1}]
        if "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
            return candidates or []
        if "MATCH (e:Engram" in query and "RETURN" in query:
            return member_rows or []
        if "ncr_cycles_survived = 0" in query:
            return []
        if "CREATE (s:Schema" in query:
            return []
        if "MERGE (e)-[r:SCHEMA]" in query:
            return []
        if "DETACH DELETE" in query:
            return []
        return []

    client.run_cypher = AsyncMock(side_effect=run_cypher)
    return client


def _make_qdrant(payload_text: str = "some engram content"):
    client = MagicMock()
    client.get_by_id = AsyncMock(return_value={"payload": {"text": payload_text}})
    client.upsert_point = AsyncMock(return_value=None)
    return client


def _make_pool():
    return MagicMock()  # asyncpg Pool — dict_repo.insert_entry will be patched


def _make_llm(content: str = "Pattern: agents fix bugs", tags: list[str] | None = None):
    llm = MagicMock()
    llm.call = AsyncMock(
        return_value=_SchemaAbstraction(content=content, tags=tags or ["agents", "bugs"])
    )
    return llm


async def _dummy_embeddings(text: str) -> list[float]:
    return [0.1] * 4  # stub: tiny vector


# ---------------------------------------------------------------------------
# run_maturation integration tests
# ---------------------------------------------------------------------------


class TestRunMaturation:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_empty(self):
        neo4j = _make_neo4j(candidates=[])
        result = await run_maturation(
            "bank-1", neo4j, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings
        )
        assert isinstance(result, MaturationResult)
        assert result.schemas_created == 0
        assert result.candidates_incremented == 0

    @pytest.mark.asyncio
    async def test_cycles_incremented_below_threshold(self):
        """Candidate at cycles=1, threshold=3 → increment but no schema."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=1)
        member_rows = _make_member_rows(["e1", "e2", "e3"])
        neo4j = _make_neo4j(
            candidates=[candidate],
            member_rows=member_rows,
            increment_result=[{"ncr_cycles_survived": 2}],
        )
        result = await run_maturation(
            "bank-1", neo4j, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
            maturation_threshold=3,
        )
        assert result.candidates_incremented == 1
        assert result.schemas_created == 0

    @pytest.mark.asyncio
    async def test_schema_created_at_threshold(self):
        """Candidate reaches threshold=3 → schema is created."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        member_rows = _make_member_rows(["e1", "e2", "e3"])
        neo4j = _make_neo4j(
            candidates=[candidate],
            member_rows=member_rows,
            increment_result=[{"ncr_cycles_survived": 3}],  # just reached threshold
        )

        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ) as mock_insert:
            result = await run_maturation(
                "bank-1", neo4j, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
                maturation_threshold=3,
            )

        assert result.schemas_created == 1
        assert len(result.details) == 1
        schema = result.details[0]
        assert isinstance(schema, MaturedSchema)
        assert schema.bank_id == "bank-1"
        assert schema.candidate_id == "c1"
        assert schema.content == "Pattern: agents fix bugs"
        assert schema.tags == ["agents", "bugs"]
        assert mock_insert.called

    @pytest.mark.asyncio
    async def test_schema_links_created_for_all_members(self):
        """SCHEMA relationship MERGE must be called once per active member."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        member_rows = _make_member_rows(["e1", "e2", "e3"])

        schema_link_calls: list[str] = []

        async def run_cypher(query: str, params=None):
            if "SET c.ncr_cycles_survived = c.ncr_cycles_survived + 1" in query:
                return [{"ncr_cycles_survived": 3}]
            if "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
                return [candidate]
            if "MATCH (e:Engram" in query and "RETURN" in query:
                return member_rows
            if "MERGE (e)-[r:SCHEMA]" in query:
                schema_link_calls.append(params.get("engram_id", ""))
                return []
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)

        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ):
            result = await run_maturation(
                "bank-1", client, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
                maturation_threshold=3,
            )

        assert result.schemas_created == 1
        # All 3 members must get a SCHEMA link
        assert set(schema_link_calls) == {"e1", "e2", "e3"}

    @pytest.mark.asyncio
    async def test_candidate_deleted_after_schema_creation(self):
        """ClusterCandidate must be DETACH DELETE'd after Schema creation."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        member_rows = _make_member_rows(["e1", "e2", "e3"])
        deleted: list[str] = []

        async def run_cypher(query: str, params=None):
            if "SET c.ncr_cycles_survived = c.ncr_cycles_survived + 1" in query:
                return [{"ncr_cycles_survived": 3}]
            if "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
                return [candidate]
            if "MATCH (e:Engram" in query and "RETURN" in query:
                return member_rows
            if "DETACH DELETE" in query:
                deleted.append(params.get("candidate_id", ""))
                return []
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)

        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ):
            await run_maturation(
                "bank-1", client, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
                maturation_threshold=3,
            )

        assert "c1" in deleted

    @pytest.mark.asyncio
    async def test_cohesion_degradation_resets_cycles(self):
        """If >33% of members are archived, cycles must be reset (not incremented)."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        # Only 1 of 3 members still active → ratio=0.33 < 0.67
        degraded_rows = [
            {"engram_id": "e1", "status": "active", "layer": "neocortex"},
            {"engram_id": "e2", "status": "archived", "layer": "neocortex"},
            {"engram_id": "e3", "status": "archived", "layer": "neocortex"},
        ]
        reset_calls: list[str] = []

        async def run_cypher(query: str, params=None):
            if "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
                return [candidate]
            if "MATCH (e:Engram" in query and "RETURN" in query:
                return degraded_rows
            if "ncr_cycles_survived = 0" in query:
                reset_calls.append(params.get("candidate_id", ""))
                return []
            if "ncr_cycles_survived = c.ncr_cycles_survived + 1" in query:
                return [{"ncr_cycles_survived": 3}]
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)

        result = await run_maturation(
            "bank-1", client, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
            maturation_threshold=3, min_active_ratio=0.67,
        )

        assert result.candidates_reset == 1
        assert result.schemas_created == 0
        assert "c1" in reset_calls

    @pytest.mark.asyncio
    async def test_neo4j_fetch_failure_returns_empty(self):
        """If the initial candidate fetch fails, return empty result without raising."""
        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=Exception("connection lost"))
        result = await run_maturation(
            "bank-1", client, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings
        )
        assert result.schemas_created == 0

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback_content(self):
        """LLM failure must not abort schema creation — fallback content is used."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        member_rows = _make_member_rows(["e1", "e2", "e3"])
        neo4j = _make_neo4j(
            candidates=[candidate],
            member_rows=member_rows,
            increment_result=[{"ncr_cycles_survived": 3}],
        )
        broken_llm = MagicMock()
        broken_llm.call = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ):
            result = await run_maturation(
                "bank-1", neo4j, _make_qdrant(), _make_pool(), broken_llm, _dummy_embeddings,
                maturation_threshold=3,
            )

        # Falls back but still creates the schema
        assert result.schemas_created == 1
        assert result.details[0].content.startswith("Pattern:")

    @pytest.mark.asyncio
    async def test_multiple_candidates_independent(self):
        """Two candidates at different cycle counts → only the mature one promotes."""
        c_ready = _make_candidate("ready", ["e1", "e2", "e3"], cycles=2)
        c_young = _make_candidate("young", ["e4", "e5", "e6"], cycles=0)
        member_rows = _make_member_rows(["e1", "e2", "e3", "e4", "e5", "e6"])
        call_counter = [0]

        async def run_cypher(query: str, params=None):
            if "SET c.ncr_cycles_survived = c.ncr_cycles_survived + 1" in query:
                call_counter[0] += 1
                cid = (params or {}).get("candidate_id", "")
                return [{"ncr_cycles_survived": 3 if cid == "ready" else 1}]
            if "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
                return [c_ready, c_young]
            if "MATCH (e:Engram" in query and "RETURN" in query:
                return [r for r in member_rows if r["engram_id"] in (params or {}).get("member_ids", [])]
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)

        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ):
            result = await run_maturation(
                "bank-1", client, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
                maturation_threshold=3,
            )

        assert result.schemas_created == 1
        assert result.candidates_incremented == 2

    @pytest.mark.asyncio
    async def test_schema_id_is_valid_uuid(self):
        """Created Schema must have a valid UUID as schema_id."""
        candidate = _make_candidate("c1", ["e1", "e2", "e3"], cycles=2)
        member_rows = _make_member_rows(["e1", "e2", "e3"])
        neo4j = _make_neo4j(
            candidates=[candidate],
            member_rows=member_rows,
            increment_result=[{"ncr_cycles_survived": 3}],
        )
        with patch(
            "hindsight_api.engine.consolidation.schema_maturation.dict_repo.insert_entry",
            new_callable=AsyncMock,
        ):
            result = await run_maturation(
                "bank-1", neo4j, _make_qdrant(), _make_pool(), _make_llm(), _dummy_embeddings,
                maturation_threshold=3,
            )
        # Validate UUID format
        schema_id = result.details[0].schema_id
        uuid.UUID(schema_id)  # raises if invalid
