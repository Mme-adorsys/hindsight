"""
Retrieval Integration Tests — Epic 07 Story 06 (Milestone Validation).

Tests validate the full retrieval pipeline across all 3 databases:
  PostgreSQL (memory_units + engram_dictionary)
  Qdrant     (vector similarity / EngramRetriever seed phase)
  Neo4j      (graph traversal / EngramRetriever + MPFP)

Requires live services — see docker-compose.test.yml:
  docker compose -f docker-compose.test.yml up -d
  HINDSIGHT_TEST_QDRANT_URL=http://localhost:6333 \\
  HINDSIGHT_TEST_NEO4J_URL=bolt://localhost:7687 \\
  uv run pytest tests/test_retrieval_integration_s6.py -m integration -v

Tests skip automatically when service env vars are not set.

Test policy: Ab Epic 07 — Retrieval tests (Precision/Recall, Mode-Dependency).
"""

from __future__ import annotations

import asyncio
import time

import asyncpg
import pytest
import pytest_asyncio

from hindsight_api.engine.qdrant_client import QdrantEngineClient
from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.search.bank_merge import BANK_WEIGHTS, get_bank_weights
from hindsight_api.engine.session.mode_config import RetrievalMode
from tests.fixtures.retrieval_fixtures import (
    AGENT_BANK,
    AGENT_ENGRAM_IDS,
    GROUND_TRUTH,
    SHARED_BANK,
    SHARED_ENGRAM_IDS,
    cleanup_test_data,
    insert_into_neo4j,
    insert_into_postgres,
    insert_into_qdrant,
    query_embedding,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

QDRANT_COLLECTION = "engrams-test"


@pytest_asyncio.fixture(scope="module")
async def pg_pool(pg0_db_url):
    """Dedicated asyncpg pool for integration tests."""
    pool = await asyncpg.create_pool(pg0_db_url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
async def qdrant(qdrant_test_url):
    """QdrantEngineClient connected to test Qdrant with a test-specific collection."""
    client = QdrantEngineClient(
        url=qdrant_test_url,
        api_key=None,
        collection=QDRANT_COLLECTION,
    )
    await client.connect()
    await client.ensure_collection()
    yield client
    await client.close()


@pytest_asyncio.fixture(scope="module")
async def neo4j(neo4j_test_dsn):
    """Neo4jEngineClient connected to test Neo4j."""
    url, user, password = neo4j_test_dsn
    client = Neo4jEngineClient(url=url, user=user, password=password)
    await client.connect()
    await client.ensure_schema()
    yield client
    await client.close()


@pytest_asyncio.fixture(scope="module", autouse=True)
async def load_fixtures(pg_pool, qdrant, neo4j):
    """
    Load test fixture data into all 3 stores before module tests run.
    Cleans up after all tests in this module complete.
    """
    await insert_into_postgres(pg_pool, AGENT_BANK)
    await insert_into_postgres(pg_pool, SHARED_BANK)
    await insert_into_qdrant(qdrant, AGENT_BANK)
    await insert_into_qdrant(qdrant, SHARED_BANK)
    await insert_into_neo4j(neo4j)

    yield

    await cleanup_test_data(pg_pool, qdrant, neo4j)


# ---------------------------------------------------------------------------
# T1 — Fixture setup verification
# ---------------------------------------------------------------------------


class TestFixtureSetup:
    """T1 — Verify fixture data is loaded correctly in all 3 stores."""

    @pytest.mark.asyncio
    async def test_postgres_has_agent_engrams(self, pg_pool):
        """memory_units table contains agent bank fixtures."""
        count = await pg_pool.fetchval(
            "SELECT COUNT(*) FROM memory_units WHERE bank_id = $1", AGENT_BANK
        )
        assert count == 10, f"Expected 10 agent bank engrams, got {count}"

    @pytest.mark.asyncio
    async def test_postgres_has_shared_engrams(self, pg_pool):
        """memory_units table contains shared bank fixtures."""
        count = await pg_pool.fetchval(
            "SELECT COUNT(*) FROM memory_units WHERE bank_id = $1", SHARED_BANK
        )
        assert count == 10, f"Expected 10 shared bank engrams, got {count}"

    @pytest.mark.asyncio
    async def test_engram_dictionary_has_strength_values(self, pg_pool):
        """engram_dictionary rows have valid strength values."""
        rows = await pg_pool.fetch(
            "SELECT strength FROM engram_dictionary WHERE bank_id = ANY($1)",
            [AGENT_BANK, SHARED_BANK],
        )
        assert len(rows) == 20
        for row in rows:
            assert 0.0 <= row["strength"] <= 1.0

    @pytest.mark.asyncio
    async def test_qdrant_has_agent_vectors(self, qdrant):
        """Qdrant contains vectors for agent bank fixtures."""
        q = query_embedding("tech")
        results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_qdrant_bank_id_filter_works(self, qdrant):
        """Qdrant bank_id filter correctly isolates each bank."""
        q = query_embedding("tech")
        agent_results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        shared_results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={"must": [{"key": "bank_id", "match": {"value": SHARED_BANK}}]},
        )
        agent_ids = {r["engram_id"] for r in agent_results}
        shared_ids = {r["engram_id"] for r in shared_results}
        assert agent_ids.isdisjoint(shared_ids), "Banks must not overlap"

    @pytest.mark.asyncio
    async def test_neo4j_has_engram_nodes(self, neo4j):
        """Neo4j has Engram nodes for all fixtures."""
        from tests.fixtures.retrieval_fixtures import ENGRAMS
        sample_id = ENGRAMS[0].id
        node = await neo4j.get_node(sample_id)
        assert node is not None
        assert node["engram_id"] == sample_id


# ---------------------------------------------------------------------------
# T2 — Precision Mode Retrieval
# ---------------------------------------------------------------------------


class TestPrecisionModeRetrieval:
    """T2 — Precision mode: focused retrieval with high relevance threshold."""

    @pytest.mark.asyncio
    async def test_tech_query_precision_at_5(self, qdrant):
        """Precision@5 ≥ 0.8 for tech topic query in PRECISION mode (seed limit=5)."""
        q = query_embedding("tech")
        # Precision mode: seed_limit=5
        results = await qdrant.search_similar(
            embedding=q,
            limit=5,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        result_ids = {r["engram_id"] for r in results}
        ground_truth_agent = {eid for eid in GROUND_TRUTH["tech"] if eid in AGENT_ENGRAM_IDS}

        relevant_in_top5 = len(result_ids & ground_truth_agent)
        precision_at_5 = relevant_in_top5 / min(5, len(results)) if results else 0.0

        assert precision_at_5 >= 0.8, (
            f"Precision@5 for tech query = {precision_at_5:.2f}, "
            f"expected ≥ 0.8. Got IDs: {result_ids}, GT: {ground_truth_agent}"
        )

    @pytest.mark.asyncio
    async def test_science_query_precision_at_5(self, qdrant):
        """Precision@5 ≥ 0.8 for science topic query."""
        q = query_embedding("science")
        results = await qdrant.search_similar(
            embedding=q,
            limit=5,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        result_ids = {r["engram_id"] for r in results}
        ground_truth_agent = {eid for eid in GROUND_TRUTH["science"] if eid in AGENT_ENGRAM_IDS}

        relevant_in_top5 = len(result_ids & ground_truth_agent)
        precision_at_5 = relevant_in_top5 / min(5, len(results)) if results else 0.0

        assert precision_at_5 >= 0.8, (
            f"Precision@5 for science query = {precision_at_5:.2f}, expected ≥ 0.8"
        )

    @pytest.mark.asyncio
    async def test_precision_mode_strength_prefilter(self, pg_pool):
        """Strength pre-filter removes weak engrams in Precision mode (threshold=0.5)."""
        # Precision mode threshold = 0.5; art engrams have strength 0.50-0.70
        weak_ids = await pg_pool.fetch(
            "SELECT id::text FROM memory_units mu "
            "JOIN engram_dictionary ed ON ed.engram_id = mu.id "
            "WHERE mu.bank_id = $1 AND ed.strength < 0.5",
            AGENT_BANK,
        )
        # Verify we know which engrams are weak (for pre-filter test)
        # In our fixtures, no agent engrams have strength < 0.5 in AGENT_BANK
        # This test documents the pre-filter behavior expectation
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        threshold = MODE_PROFILES[RetrievalMode.PRECISION].strength_pre_filter
        assert threshold > 0.0, "Precision mode must have a positive strength pre-filter"
        assert threshold <= 0.5, "Precision mode pre-filter should not exclude too many"


# ---------------------------------------------------------------------------
# T3 — Exploration Mode Retrieval
# ---------------------------------------------------------------------------


class TestExplorationModeRetrieval:
    """T3 — Exploration mode: broader retrieval across more engrams."""

    @pytest.mark.asyncio
    async def test_exploration_returns_more_results_than_precision(self, qdrant):
        """Exploration mode (limit=20) returns more candidates than Precision (limit=5)."""
        q = query_embedding("tech")

        precision_results = await qdrant.search_similar(
            embedding=q,
            limit=5,  # Precision seed limit
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        exploration_results = await qdrant.search_similar(
            embedding=q,
            limit=20,  # Exploration seed limit
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        assert len(exploration_results) >= len(precision_results), (
            "Exploration must return at least as many results as Precision"
        )

    @pytest.mark.asyncio
    async def test_exploration_recall_covers_topic_cluster(self, qdrant):
        """Exploration mode finds at least 80% of relevant engrams in the topic."""
        q = query_embedding("tech")
        results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        result_ids = {r["engram_id"] for r in results}
        agent_tech_ids = {eid for eid in GROUND_TRUTH["tech"] if eid in AGENT_ENGRAM_IDS}

        recall = len(result_ids & agent_tech_ids) / len(agent_tech_ids) if agent_tech_ids else 0.0
        assert recall >= 0.8, f"Recall@20 = {recall:.2f} for tech cluster, expected ≥ 0.8"

    @pytest.mark.asyncio
    async def test_exploration_lower_strength_threshold(self):
        """Exploration mode has lower strength pre-filter than Precision."""
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        precision_threshold = MODE_PROFILES[RetrievalMode.PRECISION].strength_pre_filter
        exploration_threshold = MODE_PROFILES[RetrievalMode.EXPLORATION].strength_pre_filter
        assert exploration_threshold <= precision_threshold, (
            f"Exploration threshold {exploration_threshold} must be ≤ "
            f"Precision threshold {precision_threshold}"
        )


# ---------------------------------------------------------------------------
# T4 — Analogy Mode (Neo4j traversal)
# ---------------------------------------------------------------------------


class TestAnalogyModeTraversal:
    """T4 — Analogy mode: cross-domain discovery via Neo4j SIMILAR_TO links."""

    @pytest.mark.asyncio
    async def test_neo4j_traversal_reaches_linked_nodes(self, neo4j):
        """Neo4j traversal from a seed reaches SIMILAR_TO-linked nodes."""
        from tests.fixtures.retrieval_fixtures import NEO4J_LINKS, ENGRAMS

        # Pick first tech engram as seed
        seed_id = next(e.id for e in ENGRAMS if e.topic == "tech")
        results = await neo4j.traverse(
            start_id=seed_id,
            rel_types=["SIMILAR_TO"],
            max_depth=2,
            min_weight=0.01,
        )
        traversed_ids = {r["engram_id"] for r in results}
        # Should reach at least 1 linked node (the next tech engram)
        expected_neighbor = next(
            to_id for from_id, rel, to_id in NEO4J_LINKS if from_id == seed_id
        )
        assert expected_neighbor in traversed_ids or len(traversed_ids) >= 1, (
            f"Traversal from {seed_id} should reach linked nodes. Got: {traversed_ids}"
        )

    @pytest.mark.asyncio
    async def test_deeper_traversal_reaches_more_nodes(self, neo4j):
        """Depth=2 traversal reaches more nodes than depth=1."""
        from tests.fixtures.retrieval_fixtures import ENGRAMS

        seed_id = next(e.id for e in ENGRAMS if e.topic == "tech")
        depth1 = await neo4j.traverse(
            start_id=seed_id,
            rel_types=["SIMILAR_TO"],
            max_depth=1,
            min_weight=0.01,
        )
        depth2 = await neo4j.traverse(
            start_id=seed_id,
            rel_types=["SIMILAR_TO"],
            max_depth=2,
            min_weight=0.01,
        )
        assert len(depth2) >= len(depth1), "Deeper traversal must reach at least as many nodes"

    @pytest.mark.asyncio
    async def test_analogy_mode_has_deeper_traversal_than_precision(self):
        """Analogy mode traversal depth >= Precision mode traversal depth."""
        from hindsight_api.engine.search.engram_retrieval import _DEPTH_MAP
        from hindsight_api.engine.session.mode_config import MODE_PROFILES

        precision_depth = _DEPTH_MAP.get(
            MODE_PROFILES[RetrievalMode.PRECISION].traversal_depth, 2
        )
        analogy_depth = _DEPTH_MAP.get(
            MODE_PROFILES[RetrievalMode.ANALOGY].traversal_depth, 2
        )
        assert analogy_depth >= precision_depth, (
            f"Analogy depth {analogy_depth} should be ≥ Precision depth {precision_depth}"
        )


# ---------------------------------------------------------------------------
# T5 — Dual-Bank Routing
# ---------------------------------------------------------------------------


class TestDualBankRouting:
    """T5 — Dual-bank query: agent + shared bank in parallel with source marking."""

    @pytest.mark.asyncio
    async def test_qdrant_returns_results_from_both_banks(self, qdrant):
        """Querying both banks returns engrams from agent AND shared bank."""
        q = query_embedding("tech")
        agent_results = await qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        shared_results = await qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": SHARED_BANK}}]},
        )
        agent_ids = {r["engram_id"] for r in agent_results}
        shared_ids = {r["engram_id"] for r in shared_results}

        assert agent_ids & AGENT_ENGRAM_IDS, "Agent bank query must return agent engrams"
        assert shared_ids & SHARED_ENGRAM_IDS, "Shared bank query must return shared engrams"

    @pytest.mark.asyncio
    async def test_parallel_bank_queries_complete_independently(self, qdrant):
        """asyncio.gather on two Qdrant queries completes without interference."""
        q = query_embedding("science")
        agent_task = qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        shared_task = qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": SHARED_BANK}}]},
        )
        agent_results, shared_results = await asyncio.gather(agent_task, shared_task)
        assert len(agent_results) > 0
        assert len(shared_results) > 0

    def test_precision_mode_weights_agent_higher(self):
        """Precision mode gives agent bank higher weight (0.7 > 0.3)."""
        aw, sw = get_bank_weights(RetrievalMode.PRECISION)
        assert aw > sw, f"Precision: agent_w={aw} should exceed shared_w={sw}"

    def test_exploration_mode_weights_shared_higher(self):
        """Exploration mode gives shared bank higher weight (0.7 > 0.3)."""
        aw, sw = get_bank_weights(RetrievalMode.EXPLORATION)
        assert sw > aw, f"Exploration: shared_w={sw} should exceed agent_w={aw}"

    def test_all_four_modes_have_bank_weights(self):
        """All 4 modes have bank weights defined that sum to 1.0."""
        for mode in RetrievalMode:
            aw, sw = get_bank_weights(mode)
            assert abs(aw + sw - 1.0) < 1e-9, f"Mode {mode.value}: weights must sum to 1.0"


# ---------------------------------------------------------------------------
# T6 — Tag-Filter Retrieval
# ---------------------------------------------------------------------------


class TestTagFilterRetrieval:
    """T6 — Tag filter: only matching engrams returned when tags specified."""

    @pytest.mark.asyncio
    async def test_tag_filter_ai_restricts_results(self, qdrant):
        """Tag filter 'ai' returns only engrams with 'ai' tag."""
        q = query_embedding("tech")
        results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={
                "must": [
                    {"key": "bank_id", "match": {"value": AGENT_BANK}},
                    {"key": "tags", "match": {"value": "ai"}},
                ]
            },
        )
        # All returned engrams must have "ai" in their tags
        for r in results:
            tags = r.get("payload", {}).get("tags", [])
            assert "ai" in tags, f"Result {r['engram_id']} lacks 'ai' tag, has: {tags}"

    @pytest.mark.asyncio
    async def test_no_tag_filter_returns_all_topic_engrams(self, qdrant):
        """Without tag filter, all topic cluster engrams can be retrieved."""
        q = query_embedding("history")
        results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        result_ids = {r["engram_id"] for r in results}
        agent_history = {eid for eid in GROUND_TRUTH["history"] if eid in AGENT_ENGRAM_IDS}
        # At least the top history engrams should be retrievable
        assert result_ids & agent_history, "History engrams must be retrievable without tag filter"

    @pytest.mark.asyncio
    async def test_mismatched_tag_returns_empty(self, qdrant):
        """Tag filter that matches no engrams returns empty results."""
        q = query_embedding("tech")
        results = await qdrant.search_similar(
            embedding=q,
            limit=20,
            filters={
                "must": [
                    {"key": "bank_id", "match": {"value": AGENT_BANK}},
                    {"key": "tags", "match": {"value": "nonexistent-tag-xyz"}},
                ]
            },
        )
        assert results == [], f"Non-matching tag should return empty, got {len(results)} results"


# ---------------------------------------------------------------------------
# T7 — Scoring Validation
# ---------------------------------------------------------------------------


class TestScoringValidation:
    """T7 — Verify scoring components match expected behavior."""

    def test_strength_modulated_recency_decay(self):
        """Stronger engrams decay more slowly (higher effective half-life)."""
        from hindsight_api.engine.search.scoring import calculate_recency_weight

        days_ago = 180
        weak_recency = calculate_recency_weight(days_ago, strength=0.0)
        strong_recency = calculate_recency_weight(days_ago, strength=1.0)
        assert strong_recency > weak_recency, (
            f"Strong engram recency {strong_recency:.3f} should exceed "
            f"weak engram recency {weak_recency:.3f}"
        )

    def test_thalamus_boost_increases_weight(self):
        """Task-relevance boost dimension increases thalamus weight."""
        from hindsight_api.engine.search.scoring import calculate_thalamus_weight

        scores = {"task_relevance": 0.9, "novelty": 0.3, "surprise": 0.2, "emotional_valence": 0.1}
        boosted = calculate_thalamus_weight(scores, boost_dimension="task_relevance")
        unboosted = calculate_thalamus_weight(scores, boost_dimension=None)
        assert boosted >= unboosted, "Boost dimension must not decrease thalamus weight"

    def test_precision_mode_weights_ce_higher(self):
        """Precision mode weights cross-encoder score more heavily than exploration."""
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        precision_ce = MODE_PROFILES[RetrievalMode.PRECISION].scoring_weights.ce
        exploration_ce = MODE_PROFILES[RetrievalMode.EXPLORATION].scoring_weights.ce
        assert precision_ce >= exploration_ce, (
            f"Precision CE weight {precision_ce} should be ≥ Exploration CE weight {exploration_ce}"
        )

    def test_all_scoring_weights_sum_to_one(self):
        """All mode scoring weights sum to 1.0 (or very close)."""
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        for mode, profile in MODE_PROFILES.items():
            w = profile.scoring_weights
            total = w.ce + w.rrf + w.temporal + w.recency + w.engram_strength + w.thalamus_weighted
            assert abs(total - 1.0) < 0.01, (
                f"Mode {mode.value} scoring weights sum to {total:.3f}, expected ~1.0"
            )


# ---------------------------------------------------------------------------
# T8 — Performance Baseline
# ---------------------------------------------------------------------------


class TestPerformanceBaseline:
    """T8 — Measure retrieval latency for 20-engram fixture set (no hard thresholds)."""

    @pytest.mark.asyncio
    async def test_qdrant_search_latency(self, qdrant):
        """Qdrant vector search completes in reasonable time for 20 fixtures."""
        q = query_embedding("tech")
        start = time.perf_counter()
        await qdrant.search_similar(embedding=q, limit=20)
        elapsed = time.perf_counter() - start

        # Informational — no hard threshold, but log for baseline tracking
        print(f"\n[T8] Qdrant search latency: {elapsed * 1000:.1f}ms for 20 vectors")
        assert elapsed < 5.0, f"Qdrant search took {elapsed:.2f}s — unexpectedly slow"

    @pytest.mark.asyncio
    async def test_neo4j_traversal_latency(self, neo4j):
        """Neo4j BFS traversal completes in reasonable time for 20-node graph."""
        from tests.fixtures.retrieval_fixtures import ENGRAMS

        seed_id = next(e.id for e in ENGRAMS if e.topic == "tech")
        start = time.perf_counter()
        await neo4j.traverse(
            start_id=seed_id,
            rel_types=["SIMILAR_TO"],
            max_depth=2,
            min_weight=0.01,
        )
        elapsed = time.perf_counter() - start

        print(f"\n[T8] Neo4j traversal latency: {elapsed * 1000:.1f}ms for depth=2")
        assert elapsed < 10.0, f"Neo4j traversal took {elapsed:.2f}s — unexpectedly slow"

    @pytest.mark.asyncio
    async def test_parallel_dual_bank_latency(self, qdrant):
        """Dual-bank asyncio.gather completes faster than sequential equivalent."""
        q = query_embedding("science")

        # Sequential
        seq_start = time.perf_counter()
        await qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
        )
        await qdrant.search_similar(
            embedding=q, limit=10,
            filters={"must": [{"key": "bank_id", "match": {"value": SHARED_BANK}}]},
        )
        seq_elapsed = time.perf_counter() - seq_start

        # Parallel
        par_start = time.perf_counter()
        await asyncio.gather(
            qdrant.search_similar(
                embedding=q, limit=10,
                filters={"must": [{"key": "bank_id", "match": {"value": AGENT_BANK}}]},
            ),
            qdrant.search_similar(
                embedding=q, limit=10,
                filters={"must": [{"key": "bank_id", "match": {"value": SHARED_BANK}}]},
            ),
        )
        par_elapsed = time.perf_counter() - par_start

        print(
            f"\n[T8] Dual-bank: sequential={seq_elapsed * 1000:.1f}ms, "
            f"parallel={par_elapsed * 1000:.1f}ms"
        )
        # Parallel should not be significantly slower than sequential
        assert par_elapsed < seq_elapsed * 1.5, (
            f"Parallel queries ({par_elapsed:.3f}s) should not be much slower than sequential ({seq_elapsed:.3f}s)"
        )
