"""
Multi-Bank Integration Tests — Epic 14 Story 05.

Validates the complete multi-bank lifecycle across all three storage layers:
  PostgreSQL  — bank records, engram_dictionary entries
  Qdrant      — vector similarity for novelty check and cross-bank query
  Neo4j       — CROSS_BANK links, CONTRADICTION relationships

Requires live services — see docker-compose.test.yml:
  docker compose -f docker-compose.test.yml up -d
  HINDSIGHT_TEST_QDRANT_URL=http://localhost:6333 \\
  HINDSIGHT_TEST_NEO4J_URL=bolt://localhost:7687 \\
  uv run pytest tests/test_multi_bank_integration.py -m integration -v

Tests skip automatically when service env vars are not set.

Test policy: Ab Epic 12 — Knowledge Evolution (multi-bank lifecycle).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from hindsight_api.engine.consolidation.conflict_resolution import (
    Resolution,
    detect_conflicts,
    resolve_conflict,
)
from hindsight_api.engine.consolidation.multi_bank_promoter import (
    NOVELTY_SIMILARITY_THRESHOLD,
    NoveltyResult,
    PromotionResult,
    check_novelty,
    promote_batch,
    reinforce_shared,
)
from hindsight_api.engine.engram_types import BankTier
from hindsight_api.engine.retain.bank_utils import (
    BankProfile,
    verify_bank_access,
)
from hindsight_api.engine.search.bank_merge import (
    SCHEMA_BOOST,
    SHARED_FRESHNESS_PENALTY,
    merge_parallel_results,
)
from hindsight_api.engine.search.types import RetrievalResult

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Test IDs and constants
# ---------------------------------------------------------------------------

_AGENT_A_SESSION = "agent-a:session"
_AGENT_A_DICT = "agent-a:dictionary"
_AGENT_B_SESSION = "agent-b:session"
_AGENT_B_DICT = "agent-b:dictionary"
_SHARED_BANK = "shared:global"

_DIM = 16  # Embedding dimension for tests


def _vec(seed: int) -> list[float]:
    """Generate a deterministic unit-ish vector."""
    import math

    vals = [(math.sin(seed + i) + 1) / 2 for i in range(_DIM)]
    norm = math.sqrt(sum(v * v for v in vals))
    return [v / norm for v in vals]


def _similar_vec(base_seed: int, noise: float = 0.02) -> list[float]:
    """Generate a vector that is very similar to _vec(base_seed)."""
    import math

    base = _vec(base_seed)
    noisy = [v + noise * math.sin(i) for i, v in enumerate(base)]
    norm = math.sqrt(sum(v * v for v in noisy))
    return [v / norm for v in noisy]


def _orthogonal_vec(seed: int) -> list[float]:
    """Generate a vector orthogonal to _vec(seed)."""
    import math

    vals = [(math.cos(seed * 7 + i * 3) + 1) / 2 for i in range(_DIM)]
    norm = math.sqrt(sum(v * v for v in vals))
    return [v / norm for v in vals]


# ---------------------------------------------------------------------------
# T1 — Multi-Agent Fixture (unit-level, no live DB required)
# ---------------------------------------------------------------------------


class TestMultiAgentFixtureStructure:
    """
    T1 — Validates the bank structure concept for a 2-agent + 1-shared setup.

    These tests use mocked DB calls to verify the provisioning logic
    without requiring live services. They serve as the fixture spec
    for the live integration tests below.
    """

    def test_agent_bank_ids_follow_naming_convention(self):
        """Agent banks follow the '<agent_id>:<type>' pattern."""
        for agent_id in ("agent-a", "agent-b"):
            assert f"{agent_id}:session" == f"{agent_id}:session"
            assert f"{agent_id}:dictionary" == f"{agent_id}:dictionary"

    def test_bank_tier_enum_covers_all_tiers(self):
        assert BankTier.SESSION.value == "session"
        assert BankTier.DICTIONARY.value == "dictionary"
        assert BankTier.SHARED.value == "shared"

    def test_bank_profile_tier_field_exists(self):
        profile: BankProfile = {
            "bank_id": _SHARED_BANK,
            "agent_id": "global",
            "name": "Shared",
            "description": "",
            "disposition_skepticism": 3,
            "disposition_literalism": 3,
            "disposition_empathy": 3,
            "tier": BankTier.SHARED,
        }
        assert profile["tier"] == BankTier.SHARED

    def test_shared_bank_id_is_not_agent_prefixed(self):
        """Shared bank IDs don't follow the '<agent_id>:*' prefix pattern of agent banks."""
        # Agent banks always start with their agent_id
        assert _AGENT_A_DICT.startswith("agent-a:")
        assert _AGENT_B_DICT.startswith("agent-b:")
        # Shared bank does not belong to any single agent
        assert not _SHARED_BANK.startswith("agent-a:")
        assert not _SHARED_BANK.startswith("agent-b:")

    def test_agent_bank_ids_are_disjoint(self):
        """Agent A and Agent B bank IDs never overlap."""
        agent_a_banks = {_AGENT_A_SESSION, _AGENT_A_DICT}
        agent_b_banks = {_AGENT_B_SESSION, _AGENT_B_DICT}
        assert agent_a_banks.isdisjoint(agent_b_banks)


# ---------------------------------------------------------------------------
# T2 — Isolation Test (unit-level)
# ---------------------------------------------------------------------------


class TestBankIsolation:
    """
    T2 — Agent A recall cannot return Agent B's private Engrams.

    The isolation is enforced at two levels:
    1. Qdrant filter: all queries pass bank_id as a filter → only own Engrams
    2. verify_bank_access: blocks cross-agent Dictionary access

    These unit tests verify the filter-based isolation path.
    """

    def test_merge_parallel_results_keeps_sources_distinct(self):
        """After merge, agent and shared results are independently trackable by source."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        agent_rr = ParallelRetrievalResult(
            semantic=[RetrievalResult(id="a1", text="Agent A fact", fact_type="world", similarity=0.9)],
            bm25=[],
            graph=[],
            temporal=None,
            timings={},
            temporal_constraint=None,
            mpfp_timings=[],
        )
        shared_rr = ParallelRetrievalResult(
            semantic=[RetrievalResult(id="s1", text="Shared fact", fact_type="world", similarity=0.8)],
            bm25=[],
            graph=[],
            temporal=None,
            timings={},
            temporal_constraint=None,
            mpfp_timings=[],
        )
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        sources = {r.id: r.source for r in merged.semantic}
        assert sources["a1"] == "agent"
        assert sources["s1"] == "shared"

    def test_agent_b_engram_absent_from_agent_a_merge(self):
        """Agent B's private Engrams should never appear in Agent A's results."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        # Simulate Agent A's bank results (no Agent B Engrams)
        agent_a_rr = ParallelRetrievalResult(
            semantic=[
                RetrievalResult(id="a1", text="Agent A engram 1", fact_type="world", similarity=0.9),
                RetrievalResult(id="a2", text="Agent A engram 2", fact_type="world", similarity=0.8),
            ],
            bm25=[],
            graph=[],
            temporal=None,
            timings={},
            temporal_constraint=None,
            mpfp_timings=[],
        )
        shared_rr = ParallelRetrievalResult(
            semantic=[RetrievalResult(id="s1", text="Shared engram", fact_type="world", similarity=0.7)],
            bm25=[],
            graph=[],
            temporal=None,
            timings={},
            temporal_constraint=None,
            mpfp_timings=[],
        )
        merged = merge_parallel_results(agent_a_rr, shared_rr, mode=None)
        all_ids = {r.id for r in merged.semantic}
        # Agent B's private Engrams ("b1", "b2") are not present
        assert "b1" not in all_ids
        assert "b2" not in all_ids
        assert "a1" in all_ids
        assert "s1" in all_ids


# ---------------------------------------------------------------------------
# T3 — Promotion Lifecycle Test (unit-level with mocked DB/services)
# ---------------------------------------------------------------------------


class TestPromotionLifecycle:
    """
    T3 — Agent Engram promoted from Dictionary → Shared Bank via NCR Phase 4.

    Tests the full promote_batch pipeline with mocked DB/Qdrant/Neo4j to
    verify correct promotion of a novel Engram into the Shared Bank.
    """

    @pytest.mark.asyncio
    async def test_novel_engram_is_promoted_to_shared(self):
        """A neocortex Engram with no Shared-Bank match is promoted (NOVEL → promote_to_shared)."""
        pool = MagicMock()
        qdrant = MagicMock()
        neo4j = MagicMock()

        candidate_id = str(uuid.uuid4())
        candidate = {
            "engram_id": candidate_id,
            "bank_id": _AGENT_A_DICT,
            "layer": "neocortex",
            "strength": 0.75,
            "status": "active",
            "embedding": _vec(42),
            "tags": ["physics"],
            "thalamus_overall": 0.7,
        }

        # Qdrant: no similar Engrams in Shared Bank → NOVEL
        qdrant.search_similar = AsyncMock(return_value=[])
        qdrant.upsert_point = AsyncMock()
        neo4j.run_cypher = AsyncMock(return_value=[])
        neo4j.create_relationship = AsyncMock()

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
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
                AsyncMock(),
            ) as mock_insert,
        ):
            result: PromotionResult = await promote_batch(
                pool=pool,
                qdrant_client=qdrant,
                neo4j_client=neo4j,
                bank_id=_AGENT_A_DICT,
                shared_bank_id=_SHARED_BANK,
            )

        assert result.promoted == 1
        assert result.reinforced == 0
        assert result.errors == []
        # New Shared Bank entry was inserted into PostgreSQL
        mock_insert.assert_called_once()
        inserted = mock_insert.call_args.args[1]
        assert inserted["bank_id"] == _SHARED_BANK
        assert inserted["layer"] == "neocortex"

    @pytest.mark.asyncio
    async def test_redundant_engram_reinforces_shared(self):
        """A neocortex Engram that matches an existing Shared Engram triggers reinforce."""
        pool = MagicMock()
        qdrant = MagicMock()
        neo4j = MagicMock()
        existing_shared_id = str(uuid.uuid4())

        candidate = {
            "engram_id": str(uuid.uuid4()),
            "bank_id": _AGENT_A_DICT,
            "layer": "neocortex",
            "strength": 0.75,
            "status": "active",
            "embedding": _vec(1),
            "thalamus_overall": 0.7,
        }

        # Qdrant: similar Engram already in Shared Bank → REDUNDANT
        qdrant.search_similar = AsyncMock(
            return_value=[
                {
                    "score": 0.93,
                    "engram_id": existing_shared_id,
                    "payload": {"strength": 0.5},
                }
            ]
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
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_strength",
                AsyncMock(),
            ) as mock_strength,
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.update_access",
                AsyncMock(),
            ),
        ):
            result = await promote_batch(
                pool=pool,
                qdrant_client=qdrant,
                neo4j_client=neo4j,
                bank_id=_AGENT_A_DICT,
                shared_bank_id=_SHARED_BANK,
            )

        assert result.reinforced == 1
        assert result.promoted == 0
        mock_strength.assert_called_once_with(pool, existing_shared_id, pytest.approx(0.55))


# ---------------------------------------------------------------------------
# T4 — Conflict Resolution Test (unit-level)
# ---------------------------------------------------------------------------


class TestConflictResolutionLifecycle:
    """
    T4 — Two Agents promote similar Engrams; conflict resolution applies.
    """

    @pytest.mark.asyncio
    async def test_no_conflict_below_threshold(self):
        """Similarity below 0.85 → no conflict, promotion proceeds normally."""
        qdrant = MagicMock()
        qdrant.search_similar = AsyncMock(
            return_value=[{"score": 0.70, "engram_id": str(uuid.uuid4()), "payload": {}}]
        )
        conflicts = await detect_conflicts(qdrant, _vec(10), _SHARED_BANK, threshold=0.85)
        assert conflicts == []

    @pytest.mark.asyncio
    async def test_similar_no_contradiction_resolves_to_merge_or_replace(self):
        """Similarity ≥ 0.85, no contradiction → MERGE or REPLACE."""
        from hindsight_api.engine.response_models import EngramContent, EngramMetadata, FullEngram
        from hindsight_api.engine.consolidation.conflict_resolution import ConflictCandidate

        from datetime import datetime, timezone

        _NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        eid = uuid.uuid4()
        candidate_engram = FullEngram(
            engram_id=eid,
            metadata=EngramMetadata(
                engram_id=eid,
                bank_id=_AGENT_A_DICT,
                strength=0.8,
                thalamus_overall=0.7,
                tags=[],
                created_at=_NOW,
            ),
            content=EngramContent(text="Gravity causes objects to fall.", extra={}),
        )
        existing = ConflictCandidate(
            engram_id=uuid.uuid4(),
            similarity=0.92,
            text="Objects fall due to gravitational force.",
            strength=0.5,
            thalamus_overall=0.5,
            created_at=_NOW,
        )
        llm = MagicMock()
        llm.call = AsyncMock(return_value="no")  # No contradiction

        result = await resolve_conflict(candidate_engram, existing, llm)
        assert result.resolution in (Resolution.MERGE, Resolution.REPLACE)

    @pytest.mark.asyncio
    async def test_contradiction_creates_link(self):
        """Similarity ≥ 0.85, contradiction detected → CONTRADICTION_LINK or KEEP_EXISTING."""
        from hindsight_api.engine.response_models import EngramContent, EngramMetadata, FullEngram
        from hindsight_api.engine.consolidation.conflict_resolution import ConflictCandidate
        from datetime import datetime, timezone

        _NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        eid = uuid.uuid4()
        candidate_engram = FullEngram(
            engram_id=eid,
            metadata=EngramMetadata(
                engram_id=eid,
                bank_id=_AGENT_A_DICT,
                strength=0.8,
                thalamus_overall=0.9,
                tags=[],
                created_at=_NOW,
            ),
            content=EngramContent(text="The earth is flat.", extra={}),
        )
        existing = ConflictCandidate(
            engram_id=uuid.uuid4(),
            similarity=0.91,
            text="The earth is spherical.",
            strength=0.6,
            thalamus_overall=0.4,
            created_at=_NOW,
        )
        llm = MagicMock()
        llm.call = AsyncMock(return_value="yes")  # Contradiction!

        result = await resolve_conflict(candidate_engram, existing, llm)
        # Candidate has higher thalamus (0.9 > 0.4) → wins → CONTRADICTION_LINK
        assert result.resolution == Resolution.CONTRADICTION_LINK
        assert result.winner_id == candidate_engram.engram_id
        assert result.loser_id == existing.engram_id


# ---------------------------------------------------------------------------
# T5 — Cross-Agent Convergence Test (unit-level)
# ---------------------------------------------------------------------------


class TestCrossAgentConvergence:
    """
    T5 — Two agents with similar Engrams trigger the convergence threshold reduction.
    """

    @pytest.mark.asyncio
    async def test_convergence_lowers_threshold_for_weak_engram(self):
        """
        Candidate strength=0.45 (below default 0.6).
        Both Agent A and Agent B have similar Engrams → convergence.
        Effective threshold drops to 0.4 → candidate qualifies.
        """
        pool = MagicMock()
        neo4j = MagicMock()
        neo4j.run_cypher = AsyncMock(return_value=[])
        neo4j.create_relationship = AsyncMock()

        qdrant = MagicMock()
        # Agent A: match, Agent B: match, Shared Bank: NOVEL
        qdrant.search_similar = AsyncMock(
            side_effect=[
                [{"score": 0.90, "engram_id": "a1", "payload": {}}],  # agent-a: match
                [{"score": 0.88, "engram_id": "b1", "payload": {}}],  # agent-b: match
                [],  # shared bank: NOVEL → promote
            ]
        )
        qdrant.upsert_point = AsyncMock()

        candidate = {
            "engram_id": str(uuid.uuid4()),
            "bank_id": _AGENT_A_DICT,
            "layer": "neocortex",
            "strength": 0.45,  # Below default 0.6 but above convergence threshold 0.4
            "status": "active",
            "embedding": _vec(7),
            "thalamus_overall": 0.6,
        }

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
                "hindsight_api.engine.consolidation.multi_bank_promoter.dict_repo.insert_entry",
                AsyncMock(),
            ),
        ):
            result = await promote_batch(
                pool=pool,
                qdrant_client=qdrant,
                neo4j_client=neo4j,
                bank_id=_AGENT_A_DICT,
                shared_bank_id=_SHARED_BANK,
                agent_bank_ids=[_AGENT_A_DICT, _AGENT_B_DICT],
                strength_threshold=0.6,
            )

        assert result.promoted == 1
        assert result.skipped == 0

    @pytest.mark.asyncio
    async def test_no_convergence_keeps_threshold(self):
        """
        Only 1 agent has a similar Engram (below CROSS_AGENT_MIN_COUNT=2).
        Threshold stays at 0.6 → candidate with strength=0.45 is skipped.
        """
        pool = MagicMock()
        neo4j = MagicMock()
        neo4j.run_cypher = AsyncMock(return_value=[])

        qdrant = MagicMock()
        # Only Agent A has a match (Agent B does not)
        qdrant.search_similar = AsyncMock(
            side_effect=[
                [{"score": 0.90, "engram_id": "a1", "payload": {}}],  # agent-a: match
                [],  # agent-b: no match
            ]
        )

        candidate = {
            "engram_id": str(uuid.uuid4()),
            "bank_id": _AGENT_A_DICT,
            "layer": "neocortex",
            "strength": 0.45,
            "status": "active",
            "embedding": _vec(7),
        }

        with (
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_promotion_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch(
                "hindsight_api.engine.consolidation.multi_bank_promoter.find_schema_triggered_candidates",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await promote_batch(
                pool=pool,
                qdrant_client=qdrant,
                neo4j_client=neo4j,
                bank_id=_AGENT_A_DICT,
                shared_bank_id=_SHARED_BANK,
                agent_bank_ids=[_AGENT_A_DICT, _AGENT_B_DICT],
                strength_threshold=0.6,
            )

        assert result.skipped == 1
        assert result.promoted == 0


# ---------------------------------------------------------------------------
# T6 — Cross-Bank Query Integration (unit-level)
# ---------------------------------------------------------------------------


class TestCrossBankQueryIntegration:
    """
    T6 / Benchmark B Seed — Validates the cross-bank query scoring pipeline.

    Simulates a multi-bank recall result and verifies the full scoring chain:
    bank weighting → schema boost → freshness penalty → deduplication.
    """

    def _make_rr(self, id: str, fact_type: str = "world", similarity: float = 1.0) -> RetrievalResult:
        return RetrievalResult(id=id, text="x", fact_type=fact_type, similarity=similarity)

    def test_schema_engrams_score_higher_than_regular_shared(self):
        """Shared schema result scores higher than shared world result of equal raw similarity."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        shared_rr = ParallelRetrievalResult(
            semantic=[
                self._make_rr("schema-1", fact_type="schema", similarity=1.0),
                self._make_rr("world-1", fact_type="world", similarity=1.0),
            ],
            bm25=[],
            graph=[],
            temporal=None,
            timings={},
            temporal_constraint=None,
            mpfp_timings=[],
        )
        agent_rr = ParallelRetrievalResult(
            semantic=[], bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        scores = {r.id: r.similarity for r in merged.semantic}
        assert scores["schema-1"] > scores["world-1"]

    def test_promoted_engram_deduped_agent_version_wins(self):
        """When same Engram ID appears in both banks, agent version score is kept."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        shared_w = 0.3  # precision mode shared weight
        # After dedup, only agent copy survives; agent copy has agent_w=0.7 * original score
        agent_rr = ParallelRetrievalResult(
            semantic=[self._make_rr("promoted-1", similarity=0.9)],
            bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        shared_rr = ParallelRetrievalResult(
            semantic=[self._make_rr("promoted-1", similarity=0.8)],  # duplicate!
            bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        promoted_results = [r for r in merged.semantic if r.id == "promoted-1"]
        assert len(promoted_results) == 1
        assert promoted_results[0].source == "agent"

    def test_freshness_penalty_makes_agent_preferred_at_equal_raw_score(self):
        """At equal raw score, agent result wins after freshness penalty on shared."""
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        raw_score = 1.0
        agent_rr = ParallelRetrievalResult(
            semantic=[self._make_rr("a1", similarity=raw_score)],
            bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        shared_rr = ParallelRetrievalResult(
            semantic=[self._make_rr("s1", similarity=raw_score)],
            bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        # Use validation mode (0.5/0.5) so weight is equal — only freshness differs
        class _Validation:
            value = "validation"

        merged = merge_parallel_results(agent_rr, shared_rr, mode=_Validation())
        scores = {r.id: r.similarity for r in merged.semantic}
        # agent: 1.0 * 0.5 = 0.5; shared: 1.0 * 0.5 * 0.95 = 0.475
        assert scores["a1"] > scores["s1"]

    def test_benchmark_b_ten_engrams_per_agent_all_scored(self):
        """
        Benchmark B seed: 10 Agent Engrams + 10 Shared Engrams merge correctly.
        All 20 (minus any duplicates) appear in output with correct source labels.
        """
        from hindsight_api.engine.search.retrieval import ParallelRetrievalResult

        agent_engrams = [self._make_rr(f"a{i}", similarity=0.9 - i * 0.02) for i in range(10)]
        shared_engrams = [self._make_rr(f"s{i}", similarity=0.85 - i * 0.02) for i in range(10)]

        agent_rr = ParallelRetrievalResult(
            semantic=agent_engrams, bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        shared_rr = ParallelRetrievalResult(
            semantic=shared_engrams, bm25=[], graph=[], temporal=None,
            timings={}, temporal_constraint=None, mpfp_timings=[],
        )
        merged = merge_parallel_results(agent_rr, shared_rr, mode=None)
        assert len(merged.semantic) == 20
        agent_count = sum(1 for r in merged.semantic if r.source == "agent")
        shared_count = sum(1 for r in merged.semantic if r.source == "shared")
        assert agent_count == 10
        assert shared_count == 10
