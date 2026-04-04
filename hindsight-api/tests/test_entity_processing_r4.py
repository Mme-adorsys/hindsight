"""
Unit tests for Epic 05 Story 04 — Entity Processing Extension (R4).

Covers:
- detect_ambiguous_entities(): no match, single match, multi-match
- disambiguate_entities(): LLM mock, fallback on error, empty input
- Neo4j entity node creation: happy path, None client no-op, error logged
- Neo4j entity relationship creation: happy path, None client no-op, error logged
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.retain.entity_processing import (
    AmbiguousEntity,
    ResolvedEntity,
    create_entity_nodes_batch,
    create_entity_relationships_batch,
    detect_ambiguous_entities,
    disambiguate_entities,
)
from hindsight_api.engine.retain.types import EntityLink
from uuid import UUID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTITY_APPLE_INC = {"entity_id": "aaa", "canonical_name": "Apple Inc.", "text": "Apple", "type": "ORG"}
_ENTITY_APPLE_FRUIT = {"entity_id": "bbb", "canonical_name": "Apple (fruit)", "text": "Apple", "type": "CONCEPT"}
_ENTITY_PARIS_CITY = {"entity_id": "ccc", "canonical_name": "Paris (city)", "text": "Paris", "type": "LOCATION"}
_ENTITY_PARIS_PERSON = {"entity_id": "ddd", "canonical_name": "Paris (person)", "text": "Paris", "type": "PERSON"}
_ENTITY_GOOGLE = {"entity_id": "eee", "canonical_name": "Google", "type": "ORG"}


# ---------------------------------------------------------------------------
# TestDetectAmbiguousEntities
# ---------------------------------------------------------------------------


class TestDetectAmbiguousEntities:
    def test_empty_names_returns_empty(self):
        result = detect_ambiguous_entities([], [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        assert result == []

    def test_empty_known_entities_returns_empty(self):
        result = detect_ambiguous_entities(["Apple"], [])
        assert result == []

    def test_no_match_returns_empty(self):
        result = detect_ambiguous_entities(["Microsoft"], [_ENTITY_APPLE_INC, _ENTITY_GOOGLE])
        assert result == []

    def test_single_match_not_ambiguous(self):
        result = detect_ambiguous_entities(["Google"], [_ENTITY_APPLE_INC, _ENTITY_GOOGLE])
        assert result == []

    def test_multi_match_detected(self):
        result = detect_ambiguous_entities(
            ["apple"], [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT, _ENTITY_GOOGLE]
        )
        assert len(result) == 1
        ae = result[0]
        assert ae.name == "apple"
        assert len(ae.candidates) == 2

    def test_case_insensitive_matching(self):
        result = detect_ambiguous_entities(["APPLE"], [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        assert len(result) == 1
        assert result[0].name == "APPLE"

    def test_multiple_ambiguous_names(self):
        result = detect_ambiguous_entities(
            ["apple", "paris"],
            [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT, _ENTITY_PARIS_CITY, _ENTITY_PARIS_PERSON],
        )
        assert len(result) == 2
        names = {ae.name for ae in result}
        assert names == {"apple", "paris"}

    def test_candidates_correct(self):
        result = detect_ambiguous_entities(["paris"], [_ENTITY_PARIS_CITY, _ENTITY_PARIS_PERSON])
        assert len(result) == 1
        candidate_ids = {c["entity_id"] for c in result[0].candidates}
        assert candidate_ids == {"ccc", "ddd"}

    def test_substring_does_not_create_false_ambiguity(self):
        """'Apple' must NOT match 'Pineapple' — exact match only."""
        known = [
            {"canonical_name": "Apple Inc.", "text": "Apple", "entity_id": "1"},
            {"canonical_name": "Pineapple", "text": "Pineapple", "entity_id": "2"},
        ]
        result = detect_ambiguous_entities(["Apple"], known)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# TestDisambiguateEntities
# ---------------------------------------------------------------------------


class TestDisambiguateEntities:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        llm = MagicMock()
        result = await disambiguate_entities([], "some context", llm)
        assert result == []

    @pytest.mark.asyncio
    async def test_llm_picks_correct_candidate(self):
        ae = AmbiguousEntity("Apple", [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="1")  # picks first candidate (Apple Inc.)
        result = await disambiguate_entities([ae], "The company released a new iPhone.", llm)
        assert len(result) == 1
        assert result[0].name == "Apple"
        assert result[0].chosen_candidate["entity_id"] == "aaa"

    @pytest.mark.asyncio
    async def test_llm_picks_second_candidate(self):
        ae = AmbiguousEntity("Apple", [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="2")
        result = await disambiguate_entities([ae], "I ate a delicious apple.", llm)
        assert result[0].chosen_candidate["entity_id"] == "bbb"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        ae = AmbiguousEntity("Apple", [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        result = await disambiguate_entities([ae], "context", llm)
        # Fallback: first candidate
        assert len(result) == 1
        assert result[0].chosen_candidate["entity_id"] == "aaa"

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self):
        ae = AmbiguousEntity("Apple", [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="not-a-number")
        result = await disambiguate_entities([ae], "context", llm)
        assert len(result) == 1
        assert result[0].chosen_candidate == _ENTITY_APPLE_INC  # first candidate fallback

    @pytest.mark.asyncio
    async def test_batch_multiple_entities(self):
        ae1 = AmbiguousEntity("Apple", [_ENTITY_APPLE_INC, _ENTITY_APPLE_FRUIT])
        ae2 = AmbiguousEntity("Paris", [_ENTITY_PARIS_CITY, _ENTITY_PARIS_PERSON])
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value="1, 2")
        result = await disambiguate_entities([ae1, ae2], "context", llm)
        assert len(result) == 2
        assert result[0].chosen_candidate["entity_id"] == "aaa"  # Apple Inc.
        assert result[1].chosen_candidate["entity_id"] == "ddd"  # Paris (person)


# ---------------------------------------------------------------------------
# TestCreateEntityNodesBatch
# ---------------------------------------------------------------------------


class TestCreateEntityNodesBatch:
    @pytest.mark.asyncio
    async def test_none_client_is_noop(self):
        # Should not raise
        await create_entity_nodes_batch(None, [_ENTITY_APPLE_INC])

    @pytest.mark.asyncio
    async def test_empty_entities_is_noop(self):
        neo4j = AsyncMock()
        await create_entity_nodes_batch(neo4j, [])
        neo4j.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_run_cypher_per_entity(self):
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock()
        entities = [_ENTITY_APPLE_INC, _ENTITY_GOOGLE]
        await create_entity_nodes_batch(neo4j, entities)
        assert neo4j.run_cypher.call_count == 2

    @pytest.mark.asyncio
    async def test_error_logged_not_raised(self):
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock(side_effect=RuntimeError("Neo4j down"))
        # Should not raise — logs warning instead
        await create_entity_nodes_batch(neo4j, [_ENTITY_APPLE_INC])


# ---------------------------------------------------------------------------
# TestCreateEntityRelationshipsBatch
# ---------------------------------------------------------------------------

_LINK = EntityLink(
    from_unit_id=UUID("00000000-0000-0000-0000-000000000001"),
    to_unit_id=UUID("00000000-0000-0000-0000-000000000002"),
    entity_id=UUID("00000000-0000-0000-0000-000000000003"),
    weight=1.0,
)


class TestCreateEntityRelationshipsBatch:
    @pytest.mark.asyncio
    async def test_none_client_is_noop(self):
        await create_entity_relationships_batch(None, [_LINK])

    @pytest.mark.asyncio
    async def test_empty_links_is_noop(self):
        neo4j = AsyncMock()
        await create_entity_relationships_batch(neo4j, [])
        neo4j.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_run_cypher_per_link(self):
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock()
        await create_entity_relationships_batch(neo4j, [_LINK, _LINK])
        assert neo4j.run_cypher.call_count == 2

    @pytest.mark.asyncio
    async def test_error_logged_not_raised(self):
        neo4j = AsyncMock()
        neo4j.run_cypher = AsyncMock(side_effect=ConnectionError("Neo4j unavailable"))
        await create_entity_relationships_batch(neo4j, [_LINK])
