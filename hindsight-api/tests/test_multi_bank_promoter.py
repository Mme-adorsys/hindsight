"""
Unit tests for Epic 14 Story 03 — Multi-Bank Promoter (B3 + B5).

All external dependencies (asyncpg pool, Qdrant, Neo4j, dict_repo) are mocked.
Covers:
- check_novelty: NOVEL vs REDUNDANT, threshold boundary, Qdrant error fallback
- count_agents_with_similar: zero agents, partial match, full match, error tolerance
- find_promotion_candidates: delegates to dict_repo with correct params
- find_schema_triggered_candidates: Neo4j error fallback, empty records, ID filtering
- promote_to_shared: inserts dict entry, upserts Qdrant, creates Neo4j CROSS_BANK link
- reinforce_shared: strength capped at 1.0, update_strength + update_access called
- promote_batch: NOVEL path (promoted), REDUNDANT path (reinforced), skip below threshold,
                 cross-agent convergence lowers threshold, schema-trigger bypass
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.multi_bank_promoter import (
    CROSS_AGENT_MIN_COUNT,
    CROSS_AGENT_THRESHOLD,
    NOVELTY_SIMILARITY_THRESHOLD,
    REINFORCE_DELTA,
    SHARED_INITIAL_STRENGTH,
    NoveltyCheckResult,
    NoveltyResult,
    PromotionResult,
    check_novelty,
    count_agents_with_similar,
    find_promotion_candidates,
    find_schema_triggered_candidates,
    promote_batch,
    promote_to_shared,
    reinforce_shared,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qdrant(hits: list[dict] | None = None, error: Exception | None = None) -> MagicMock:
    q = MagicMock()
    if error:
        q.search_similar = AsyncMock(side_effect=error)
        q.upsert_point = AsyncMock(side_effect=error)
    else:
        q.search_similar = AsyncMock(return_value=hits or [])
        q.upsert_point = AsyncMock()
    return q


def _neo4j(records: list[dict] | None = None, error: Exception | None = None) -> MagicMock:
    n = MagicMock()
    if error:
        n.run_cypher = AsyncMock(side_effect=error)
        n.create_relationship = AsyncMock(side_effect=error)
    else:
        n.run_cypher = AsyncMock(return_value=records or [])
        n.create_relationship = AsyncMock()
    return n


def _pool() -> MagicMock:
    return MagicMock()


def _hit(score: float, engram_id: str = "abc-123", strength: float = 0.5) -> dict:
    return {
        "score": score,
        "engram_id": engram_id,
        "payload": {"strength": strength, "thalamus_overall": 0.5},
    }


def _candidate(
    engram_id: str = "agent-engram-1",
    strength: float = 0.7,
    bank_id: str = "agent-1:dictionary",
    embedding: list[float] | None = None,
) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": bank_id,
        "layer": "neocortex",
        "strength": strength,
        "status": "active",
        "embedding": embedding if embedding is not None else [0.1, 0.2, 0.3],
        "tags": ["physics"],
        "thalamus_overall": 0.6,
        "abstraction_level": 0.0,
    }


# ---------------------------------------------------------------------------
# check_novelty
# ---------------------------------------------------------------------------


class TestCheckNovelty:
    @pytest.mark.asyncio
    async def test_novel_when_no_hits(self):
        q = _qdrant(hits=[])
        result = await check_novelty(q, [0.1] * 4, "shared")
        assert result.result == NoveltyResult.NOVEL

    @pytest.mark.asyncio
    async def test_novel_when_below_threshold(self):
        q = _qdrant(hits=[_hit(score=0.80)])
        result = await check_novelty(q, [0.1] * 4, "shared", threshold=0.85)
        assert result.result == NoveltyResult.NOVEL

    @pytest.mark.asyncio
    async def test_redundant_when_above_threshold(self):
        q = _qdrant(hits=[_hit(score=0.91, engram_id="shared-123", strength=0.6)])
        result = await check_novelty(q, [0.1] * 4, "shared", threshold=0.85)
        assert result.result == NoveltyResult.REDUNDANT
        assert result.existing_engram_id == "shared-123"
        assert result.existing_strength == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_redundant_at_exact_threshold(self):
        q = _qdrant(hits=[_hit(score=0.85, engram_id="x")])
        result = await check_novelty(q, [0.1] * 4, "shared", threshold=0.85)
        assert result.result == NoveltyResult.REDUNDANT

    @pytest.mark.asyncio
    async def test_qdrant_error_returns_novel(self):
        """On Qdrant failure, assume NOVEL (safe fallback — promote rather than suppress)."""
        q = _qdrant(error=RuntimeError("Qdrant down"))
        result = await check_novelty(q, [0.1] * 4, "shared")
        assert result.result == NoveltyResult.NOVEL

    @pytest.mark.asyncio
    async def test_uses_shared_bank_id_as_filter(self):
        q = _qdrant(hits=[])
        await check_novelty(q, [0.1] * 4, "my-shared-bank")
        q.search_similar.assert_called_once()
        call_kwargs = q.search_similar.call_args.kwargs
        assert call_kwargs["filters"]["bank_id"] == "my-shared-bank"


# ---------------------------------------------------------------------------
# count_agents_with_similar
# ---------------------------------------------------------------------------


class TestCountAgentsWithSimilar:
    @pytest.mark.asyncio
    async def test_zero_when_no_agent_bank_ids(self):
        q = _qdrant()
        count = await count_agents_with_similar(q, [0.1] * 4, [])
        assert count == 0
        q.search_similar.assert_not_called()

    @pytest.mark.asyncio
    async def test_counts_agents_with_hit_above_threshold(self):
        q = MagicMock()
        q.search_similar = AsyncMock(
            side_effect=[
                [_hit(score=0.90)],  # agent-1: match
                [_hit(score=0.70)],  # agent-2: below threshold
                [_hit(score=0.88)],  # agent-3: match
            ]
        )
        count = await count_agents_with_similar(
            q, [0.1] * 4, ["agent-1:dict", "agent-2:dict", "agent-3:dict"], threshold=0.85
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_skips_empty_hit_lists(self):
        q = _qdrant(hits=[])
        count = await count_agents_with_similar(q, [0.1] * 4, ["agent-1:dict"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_qdrant_error_does_not_propagate(self):
        q = _qdrant(error=RuntimeError("Qdrant down"))
        count = await count_agents_with_similar(q, [0.1] * 4, ["agent-1:dict"])
        assert count == 0


# ---------------------------------------------------------------------------
# find_promotion_candidates
# ---------------------------------------------------------------------------


class TestFindPromotionCandidates:
    @pytest.mark.asyncio
    async def test_delegates_to_dict_repo_with_correct_params(self):
        pool = _pool()
        mock_entries = [_candidate()]
        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.filter_entries",
            AsyncMock(return_value=mock_entries),
        ) as mock_filter:
            result = await find_promotion_candidates(pool, "agent-1:dictionary", strength_threshold=0.6)
            mock_filter.assert_called_once_with(
                pool,
                bank_id="agent-1:dictionary",
                strength_min=0.6,
                layer="neocortex",
                status="active",
                limit=1000,
            )
        assert result == mock_entries

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_candidates(self):
        pool = _pool()
        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.filter_entries",
            AsyncMock(return_value=[]),
        ):
            result = await find_promotion_candidates(pool, "agent-1:dictionary")
        assert result == []


# ---------------------------------------------------------------------------
# find_schema_triggered_candidates
# ---------------------------------------------------------------------------


class TestFindSchemaTriggeredCandidates:
    @pytest.mark.asyncio
    async def test_neo4j_error_returns_empty(self):
        pool = _pool()
        neo4j = _neo4j(error=RuntimeError("Neo4j down"))
        result = await find_schema_triggered_candidates(pool, neo4j, "agent-1:dictionary")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_neo4j_records_returns_empty(self):
        pool = _pool()
        neo4j = _neo4j(records=[])
        result = await find_schema_triggered_candidates(pool, neo4j, "agent-1:dictionary")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_entries_by_schema_ids(self):
        pool = _pool()
        eid = "schema-engram-1"
        neo4j = _neo4j(records=[{"engram_id": eid}])
        entries = [_candidate(engram_id=eid), _candidate(engram_id="other-engram")]
        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.filter_entries",
            AsyncMock(return_value=entries),
        ):
            result = await find_schema_triggered_candidates(pool, neo4j, "agent-1:dictionary")
        assert len(result) == 1
        assert str(result[0]["engram_id"]) == eid

    @pytest.mark.asyncio
    async def test_records_without_engram_id_skipped(self):
        pool = _pool()
        neo4j = _neo4j(records=[{"engram_id": None}])
        result = await find_schema_triggered_candidates(pool, neo4j, "agent-1:dictionary")
        assert result == []


# ---------------------------------------------------------------------------
# promote_to_shared
# ---------------------------------------------------------------------------


class TestPromoteToShared:
    @pytest.mark.asyncio
    async def test_inserts_dict_entry_with_shared_strength(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(engram_id="orig-1", embedding=[0.1, 0.2])

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ) as mock_insert:
            shared_id = await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")

        assert shared_id  # UUID was minted
        mock_insert.assert_called_once()
        inserted = mock_insert.call_args.args[1]
        assert inserted["bank_id"] == "shared-bank"
        assert inserted["strength"] == pytest.approx(SHARED_INITIAL_STRENGTH)
        assert inserted["layer"] == "neocortex"

    @pytest.mark.asyncio
    async def test_upserts_qdrant_when_embedding_present(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(embedding=[0.1, 0.2, 0.3])

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ):
            await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")

        qdrant.upsert_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_qdrant_when_no_embedding(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(embedding=[])

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ):
            await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")

        qdrant.upsert_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_cross_bank_link_in_neo4j(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(engram_id="orig-1")

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ):
            shared_id = await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")

        neo4j.create_relationship.assert_called_once()
        call_kwargs = neo4j.create_relationship.call_args.kwargs
        assert call_kwargs["from_id"] == shared_id
        assert call_kwargs["to_id"] == "orig-1"
        assert call_kwargs["rel_type"] == "CROSS_BANK"

    @pytest.mark.asyncio
    async def test_qdrant_failure_does_not_raise(self):
        pool = _pool()
        qdrant = _qdrant(error=RuntimeError("Qdrant down"))
        neo4j = _neo4j()
        candidate = _candidate(embedding=[0.1])

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ):
            # Must not raise
            await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")

    @pytest.mark.asyncio
    async def test_neo4j_failure_does_not_raise(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j(error=RuntimeError("Neo4j down"))
        candidate = _candidate(embedding=[0.1])

        with patch(
            "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
            AsyncMock(),
        ):
            await promote_to_shared(pool, qdrant, neo4j, candidate, "shared-bank")


# ---------------------------------------------------------------------------
# reinforce_shared
# ---------------------------------------------------------------------------


class TestReinforceShared:
    @pytest.mark.asyncio
    async def test_increments_strength_by_delta(self):
        pool = _pool()
        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_strength",
                AsyncMock(),
            ) as mock_strength,
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_access",
                AsyncMock(),
            ),
        ):
            await reinforce_shared(pool, "shared-1", 0.5)
        mock_strength.assert_called_once_with(pool, "shared-1", pytest.approx(0.5 + REINFORCE_DELTA))

    @pytest.mark.asyncio
    async def test_caps_strength_at_1_0(self):
        pool = _pool()
        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_strength",
                AsyncMock(),
            ) as mock_strength,
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_access",
                AsyncMock(),
            ),
        ):
            await reinforce_shared(pool, "shared-1", 0.99)
        mock_strength.assert_called_once_with(pool, "shared-1", pytest.approx(1.0))

    @pytest.mark.asyncio
    async def test_calls_update_access(self):
        pool = _pool()
        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_strength",
                AsyncMock(),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_access",
                AsyncMock(),
            ) as mock_access,
        ):
            await reinforce_shared(pool, "shared-1", 0.5)
        mock_access.assert_called_once_with(pool, "shared-1")

    @pytest.mark.asyncio
    async def test_db_error_does_not_propagate(self):
        pool = _pool()
        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_strength",
                AsyncMock(side_effect=RuntimeError("DB down")),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_access",
                AsyncMock(),
            ),
        ):
            await reinforce_shared(pool, "shared-1", 0.5)


# ---------------------------------------------------------------------------
# promote_batch
# ---------------------------------------------------------------------------


class TestPromoteBatch:
    @pytest.mark.asyncio
    async def test_novel_candidate_is_promoted(self):
        pool = _pool()
        qdrant = _qdrant(hits=[])  # No hits → NOVEL
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value="new-shared-id"),
            ) as mock_promote,
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ) as mock_reinforce,
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert result.promoted == 1
        assert result.reinforced == 0
        mock_promote.assert_called_once()
        mock_reinforce.assert_not_called()

    @pytest.mark.asyncio
    async def test_redundant_candidate_is_reinforced(self):
        pool = _pool()
        qdrant = _qdrant(hits=[_hit(score=0.92, engram_id="existing-shared")])  # REDUNDANT
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(),
            ) as mock_promote,
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ) as mock_reinforce,
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert result.reinforced == 1
        assert result.promoted == 0
        mock_reinforce.assert_called_once()
        mock_promote.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_below_threshold_is_skipped(self):
        """Candidate with strength below threshold and no cross-agent convergence → skipped."""
        pool = _pool()
        qdrant = _qdrant(hits=[])
        neo4j = _neo4j()
        candidate = _candidate(strength=0.3)  # below default 0.6

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(),
            ) as mock_promote,
        ):
            result = await promote_batch(
                pool, qdrant, neo4j, "agent-1:dict", "shared-bank", strength_threshold=0.6
            )

        assert result.skipped == 1
        assert result.promoted == 0
        mock_promote.assert_not_called()

    @pytest.mark.asyncio
    async def test_cross_agent_convergence_lowers_threshold(self):
        """
        Candidate strength=0.45 (below default 0.6) but 2 agents agree → threshold
        drops to CROSS_AGENT_THRESHOLD=0.4 → candidate is eligible → promoted.
        """
        pool = _pool()
        qdrant = MagicMock()
        # search_similar returns 2 matching agents, then no shared hit for novelty check
        qdrant.search_similar = AsyncMock(
            side_effect=[
                [_hit(score=0.90)],  # agent-1: match
                [_hit(score=0.88)],  # agent-2: match
                [],  # novelty check: NOVEL
            ]
        )
        qdrant.upsert_point = AsyncMock()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.45)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value="new-id"),
            ) as mock_promote,
        ):
            result = await promote_batch(
                pool,
                qdrant,
                neo4j,
                "agent-1:dict",
                "shared-bank",
                agent_bank_ids=["agent-1:dict", "agent-2:dict"],
                strength_threshold=0.6,
            )

        assert result.promoted == 1
        assert result.skipped == 0
        mock_promote.assert_called_once()

    @pytest.mark.asyncio
    async def test_schema_triggered_count_recorded(self):
        pool = _pool()
        qdrant = _qdrant(hits=[])
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)
        schema_candidate = _candidate(engram_id="schema-1", strength=0.2)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[schema_candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value="new-id"),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ),
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert result.schema_triggered == 1

    @pytest.mark.asyncio
    async def test_deduplicates_ncr_and_schema_candidates(self):
        """Same engram_id in both NCR and schema channels should be processed once."""
        pool = _pool()
        qdrant = _qdrant(hits=[])
        neo4j = _neo4j()
        candidate = _candidate(engram_id="shared-engram", strength=0.7)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[candidate]),  # Same engram
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value="new-id"),
            ) as mock_promote,
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert mock_promote.call_count == 1
        assert result.promoted == 1

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_zero_result(self):
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert result.promoted == 0
        assert result.reinforced == 0
        assert result.skipped == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_candidate_without_embedding_is_skipped(self):
        pool = _pool()
        qdrant = _qdrant(hits=[])
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7, embedding=[])

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(),
            ) as mock_promote,
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank")

        assert result.skipped == 1
        mock_promote.assert_not_called()


# ---------------------------------------------------------------------------
# B2 — Conflict Resolution integration tests
# ---------------------------------------------------------------------------


def _conflict_candidate(
    engram_id: str = "cccccccc-cccc-cccc-cccc-cccccccccccc", similarity: float = 0.92
) -> "ConflictCandidate":
    from uuid import UUID
    from datetime import datetime, UTC
    from hindsight_api.engine.consolidation.conflict_resolution import ConflictCandidate

    return ConflictCandidate(
        engram_id=UUID(engram_id),
        similarity=similarity,
        text="conflicting text",
        strength=0.5,
        thalamus_overall=0.6,
        created_at=datetime.now(UTC),
    )


class TestConflictResolutionIntegration:
    """promote_batch with llm= wired — tests the B2 REDUNDANT path."""

    def _patches(self, candidate, novelty_result, conflicts, conflict_result):
        from hindsight_api.engine.consolidation.conflict_resolution import Resolution, ConflictResult
        from uuid import UUID

        return (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty_result),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.detect_conflicts",
                AsyncMock(return_value=conflicts),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.resolve_conflict",
                AsyncMock(return_value=conflict_result),
            ),
        )

    @pytest.mark.asyncio
    async def test_redundant_no_llm_falls_back_to_reinforce(self):
        """Without llm=, REDUNDANT always calls reinforce_shared (backward compat)."""
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)
        novelty = NoveltyCheckResult(result=NoveltyResult.REDUNDANT, existing_engram_id="eee-000", existing_strength=0.4)

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ) as mock_reinforce,
        ):
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank", llm=None)

        assert result.reinforced == 1
        mock_reinforce.assert_called_once()

    @pytest.mark.asyncio
    async def test_redundant_with_llm_keep_existing(self):
        """REDUNDANT + LLM + resolution=KEEP_EXISTING → reinforce_shared called."""
        from hindsight_api.engine.consolidation.conflict_resolution import Resolution, ConflictResult
        from uuid import UUID

        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)
        existing_id = "00000000-0000-0000-0000-000000000001"
        novelty = NoveltyCheckResult(
            result=NoveltyResult.REDUNDANT, existing_engram_id=existing_id, existing_strength=0.5
        )
        conflict = _conflict_candidate()
        cr = ConflictResult(
            resolution=Resolution.KEEP_EXISTING,
            winner_id=UUID(existing_id),
            loser_id=conflict.engram_id,
            description="existing wins",
        )

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.detect_conflicts",
                AsyncMock(return_value=[conflict]),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.resolve_conflict",
                AsyncMock(return_value=cr),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ) as mock_reinforce,
        ):
            mock_llm = MagicMock()
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank", llm=mock_llm)

        assert result.reinforced == 1
        mock_reinforce.assert_called_once()

    @pytest.mark.asyncio
    async def test_redundant_with_llm_replace(self):
        """REDUNDANT + LLM + resolution=REPLACE → promote_to_shared called."""
        from hindsight_api.engine.consolidation.conflict_resolution import Resolution, ConflictResult
        from uuid import UUID

        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.9)
        existing_id = "00000000-0000-0000-0000-000000000001"
        novelty = NoveltyCheckResult(
            result=NoveltyResult.REDUNDANT, existing_engram_id=existing_id, existing_strength=0.3
        )
        conflict = _conflict_candidate()
        cand_uuid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        cr = ConflictResult(
            resolution=Resolution.REPLACE,
            winner_id=cand_uuid,
            loser_id=UUID(existing_id),
            description="candidate stronger",
        )

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.detect_conflicts",
                AsyncMock(return_value=[conflict]),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.resolve_conflict",
                AsyncMock(return_value=cr),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value=str(cand_uuid)),
            ) as mock_promote,
        ):
            mock_llm = MagicMock()
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank", llm=mock_llm)

        assert result.promoted == 1
        mock_promote.assert_called_once()

    @pytest.mark.asyncio
    async def test_redundant_with_llm_contradiction_link(self):
        """REDUNDANT + LLM + resolution=CONTRADICTION_LINK → promote + create_contradiction_link."""
        from hindsight_api.engine.consolidation.conflict_resolution import Resolution, ConflictResult
        from uuid import UUID

        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.8)
        existing_id = "00000000-0000-0000-0000-000000000001"
        novelty = NoveltyCheckResult(
            result=NoveltyResult.REDUNDANT, existing_engram_id=existing_id, existing_strength=0.4
        )
        conflict = _conflict_candidate()
        winner_uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        cr = ConflictResult(
            resolution=Resolution.CONTRADICTION_LINK,
            winner_id=winner_uuid,
            loser_id=conflict.engram_id,
            description="contradiction detected",
        )

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.detect_conflicts",
                AsyncMock(return_value=[conflict]),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.resolve_conflict",
                AsyncMock(return_value=cr),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.promote_to_shared",
                AsyncMock(return_value=str(winner_uuid)),
            ) as mock_promote,
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.create_contradiction_link",
                AsyncMock(),
            ) as mock_link,
        ):
            mock_llm = MagicMock()
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank", llm=mock_llm)

        assert result.promoted == 1
        mock_promote.assert_called_once()
        mock_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_conflicts_error_falls_back_to_reinforce(self):
        """detect_conflicts() failure → graceful fallback to reinforce_shared."""
        pool = _pool()
        qdrant = _qdrant()
        neo4j = _neo4j()
        candidate = _candidate(strength=0.7)
        existing_id = "00000000-0000-0000-0000-000000000002"
        novelty = NoveltyCheckResult(
            result=NoveltyResult.REDUNDANT, existing_engram_id=existing_id, existing_strength=0.4
        )

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.check_novelty",
                AsyncMock(return_value=novelty),
            ),
            patch(
                "hindsight_api.engine.consolidation.conflict_resolution.detect_conflicts",
                AsyncMock(side_effect=RuntimeError("Qdrant down")),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.reinforce_shared",
                AsyncMock(),
            ) as mock_reinforce,
        ):
            mock_llm = MagicMock()
            result = await promote_batch(pool, qdrant, neo4j, "agent-1:dict", "shared-bank", llm=mock_llm)

        assert result.reinforced == 1
        mock_reinforce.assert_called_once()
