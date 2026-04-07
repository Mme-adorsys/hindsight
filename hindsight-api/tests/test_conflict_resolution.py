"""
Unit tests for Epic 14 Story 02 — Write Conflict Resolution (B2).

All external dependencies (Qdrant, Neo4j, LLM) are mocked.
Covers:
- detect_conflicts: threshold filtering, payload mapping, error handling
- _is_contradiction: yes/no parsing, exception fallback
- merge_engrams: content base, strength max, tag union, provenance
- resolve_conflict: MERGE, REPLACE, KEEP_EXISTING, CONTRADICTION_LINK
- create_contradiction_link: Neo4j call, error tolerance
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from hindsight_api.engine.consolidation.conflict_resolution import (
    ConflictCandidate,
    ConflictResult,
    Resolution,
    _is_contradiction,
    create_contradiction_link,
    detect_conflicts,
    merge_engrams,
    resolve_conflict,
)
from hindsight_api.engine.response_models import EngramContent, EngramMetadata, FullEngram


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
_OLDER = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(
    strength: float = 0.5,
    thalamus_overall: float = 0.5,
    text: str = "The sky is blue.",
    created_at: datetime | None = None,
    tags: list[str] | None = None,
) -> FullEngram:
    eid = uuid4()
    return FullEngram(
        engram_id=eid,
        metadata=EngramMetadata(
            engram_id=eid,
            bank_id="agent-1:dictionary",
            strength=strength,
            thalamus_overall=thalamus_overall,
            tags=tags or [],
            created_at=created_at or _NOW,
        ),
        content=EngramContent(text=text, extra={}),
    )


def _existing(
    eid: UUID | None = None,
    similarity: float = 0.90,
    strength: float = 0.5,
    thalamus_overall: float = 0.5,
    text: str = "The sky is blue.",
    created_at: datetime | None = None,
) -> ConflictCandidate:
    return ConflictCandidate(
        engram_id=eid or uuid4(),
        similarity=similarity,
        text=text,
        strength=strength,
        thalamus_overall=thalamus_overall,
        created_at=created_at or _OLDER,
    )


def _llm(answer: str = "no") -> AsyncMock:
    llm = MagicMock()
    llm.call = AsyncMock(return_value=answer)
    return llm


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    @pytest.mark.asyncio
    async def test_returns_empty_when_below_threshold(self):
        qdrant = MagicMock()
        eid = uuid4()
        qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": str(eid), "score": 0.70, "payload": {"text": "hi", "strength": 0.5}}]
        )
        result = await detect_conflicts(qdrant, [0.1] * 4, "shared", threshold=0.85)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_candidate_above_threshold(self):
        qdrant = MagicMock()
        eid = uuid4()
        qdrant.search_similar = AsyncMock(
            return_value=[
                {
                    "engram_id": str(eid),
                    "score": 0.91,
                    "payload": {
                        "text": "The sky is blue.",
                        "strength": 0.6,
                        "thalamus_overall": 0.7,
                        "created_at": "2026-04-06T12:00:00+00:00",
                    },
                }
            ]
        )
        result = await detect_conflicts(qdrant, [0.1] * 4, "shared", threshold=0.85)
        assert len(result) == 1
        assert result[0].engram_id == eid
        assert result[0].similarity == pytest.approx(0.91)
        assert result[0].text == "The sky is blue."
        assert result[0].strength == pytest.approx(0.6)
        assert result[0].thalamus_overall == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_skips_hits_without_engram_id(self):
        qdrant = MagicMock()
        qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": None, "score": 0.95, "payload": {"text": "x"}}]
        )
        result = await detect_conflicts(qdrant, [0.1] * 4, "shared")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_qdrant_exception(self):
        qdrant = MagicMock()
        qdrant.search_similar = AsyncMock(side_effect=RuntimeError("Qdrant down"))
        result = await detect_conflicts(qdrant, [0.1] * 4, "shared")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_threshold_exactly(self):
        """Score == threshold should be included."""
        qdrant = MagicMock()
        eid = uuid4()
        qdrant.search_similar = AsyncMock(
            return_value=[{"engram_id": str(eid), "score": 0.85, "payload": {"text": "x"}}]
        )
        result = await detect_conflicts(qdrant, [0.1] * 4, "shared", threshold=0.85)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _is_contradiction
# ---------------------------------------------------------------------------


class TestIsContradiction:
    @pytest.mark.asyncio
    async def test_yes_returns_true(self):
        llm = _llm("yes")
        assert await _is_contradiction(llm, "A", "B") is True

    @pytest.mark.asyncio
    async def test_yes_case_insensitive(self):
        llm = _llm("Yes, they contradict.")
        assert await _is_contradiction(llm, "A", "B") is True

    @pytest.mark.asyncio
    async def test_no_returns_false(self):
        llm = _llm("no")
        assert await _is_contradiction(llm, "A", "B") is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        llm = MagicMock()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM unreachable"))
        assert await _is_contradiction(llm, "A", "B") is False


# ---------------------------------------------------------------------------
# merge_engrams
# ---------------------------------------------------------------------------


class TestMergeEngrams:
    def test_stronger_content_is_base(self):
        strong = _candidate(strength=0.8, text="Strong content.")
        weak = _candidate(strength=0.3, text="Weak content.")
        merged = merge_engrams(strong, weak)
        assert merged.content.text == "Strong content."

    def test_strength_is_max(self):
        strong = _candidate(strength=0.6)
        weak = _candidate(strength=0.9)  # weaker by argument, higher numeric
        merged = merge_engrams(strong, weak)
        assert merged.metadata.strength == pytest.approx(0.9)

    def test_merged_from_recorded(self):
        strong = _candidate()
        weak = _candidate()
        merged = merge_engrams(strong, weak)
        assert merged.content.extra["merged_from"] == str(weak.engram_id)

    def test_tags_are_union(self):
        strong = _candidate(tags=["science", "physics"])
        weak = _candidate(tags=["physics", "chemistry"])
        merged = merge_engrams(strong, weak)
        assert set(merged.metadata.tags) == {"science", "physics", "chemistry"}

    def test_original_not_mutated(self):
        strong = _candidate(strength=0.5)
        weak = _candidate(strength=0.9)
        _ = merge_engrams(strong, weak)
        assert strong.metadata.strength == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# resolve_conflict
# ---------------------------------------------------------------------------


class TestResolveConflict:
    @pytest.mark.asyncio
    async def test_no_contradiction_candidate_stronger_gives_replace(self):
        cand = _candidate(strength=0.8)
        ex = _existing(strength=0.5)
        result = await resolve_conflict(cand, ex, _llm("no"))
        assert result.resolution == Resolution.REPLACE
        assert result.winner_id == cand.engram_id

    @pytest.mark.asyncio
    async def test_no_contradiction_existing_stronger_gives_merge(self):
        cand = _candidate(strength=0.3)
        ex = _existing(strength=0.8)
        result = await resolve_conflict(cand, ex, _llm("no"))
        assert result.resolution == Resolution.MERGE
        assert result.winner_id == ex.engram_id

    @pytest.mark.asyncio
    async def test_contradiction_candidate_higher_thalamus_gives_contradiction_link(self):
        cand = _candidate(thalamus_overall=0.9)
        ex = _existing(thalamus_overall=0.4)
        result = await resolve_conflict(cand, ex, _llm("yes"))
        assert result.resolution == Resolution.CONTRADICTION_LINK
        assert result.winner_id == cand.engram_id
        assert result.loser_id == ex.engram_id

    @pytest.mark.asyncio
    async def test_contradiction_existing_higher_thalamus_gives_keep_existing(self):
        cand = _candidate(thalamus_overall=0.3)
        ex = _existing(thalamus_overall=0.8)
        result = await resolve_conflict(cand, ex, _llm("yes"))
        assert result.resolution == Resolution.KEEP_EXISTING
        assert result.winner_id == ex.engram_id

    @pytest.mark.asyncio
    async def test_contradiction_equal_thalamus_newer_candidate_wins(self):
        cand = _candidate(thalamus_overall=0.5, created_at=_NOW)
        ex = _existing(thalamus_overall=0.5, created_at=_OLDER)
        result = await resolve_conflict(cand, ex, _llm("yes"))
        assert result.resolution == Resolution.CONTRADICTION_LINK
        assert result.winner_id == cand.engram_id

    @pytest.mark.asyncio
    async def test_contradiction_equal_thalamus_newer_existing_wins(self):
        cand = _candidate(thalamus_overall=0.5, created_at=_OLDER)
        ex = _existing(thalamus_overall=0.5, created_at=_NOW)
        result = await resolve_conflict(cand, ex, _llm("yes"))
        assert result.resolution == Resolution.KEEP_EXISTING
        assert result.winner_id == ex.engram_id

    @pytest.mark.asyncio
    async def test_result_contains_description(self):
        cand = _candidate(strength=0.9)
        ex = _existing(strength=0.4)
        result = await resolve_conflict(cand, ex, _llm("no"))
        assert result.description != ""


# ---------------------------------------------------------------------------
# create_contradiction_link
# ---------------------------------------------------------------------------


class TestCreateContradictionLink:
    @pytest.mark.asyncio
    async def test_calls_neo4j_with_correct_args(self):
        neo4j = MagicMock()
        neo4j.create_relationship = AsyncMock()
        winner = uuid4()
        loser = uuid4()
        await create_contradiction_link(neo4j, winner, loser, "test contradiction")
        neo4j.create_relationship.assert_called_once_with(
            from_id=str(winner),
            to_id=str(loser),
            rel_type="CONTRADICTION",
            properties={"description": "test contradiction", "resolved": True},
        )

    @pytest.mark.asyncio
    async def test_neo4j_failure_does_not_propagate(self):
        neo4j = MagicMock()
        neo4j.create_relationship = AsyncMock(side_effect=RuntimeError("Neo4j down"))
        # Must not raise
        await create_contradiction_link(neo4j, uuid4(), uuid4())
