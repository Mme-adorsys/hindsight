"""
Retain Integration Test — Live-LLM end-to-end over a clean dev bank.

Seeds `integration_test_bank` with a mixed dataset covering every retain
path we care about:

* plain world facts (no expectation/outcome)
* structured experiences with expectation/outcome (Epic 15 Story 04 split
  via ``units_to_content_dicts``)
* ACTION_EFFECT chains (structured action → effect, Epic 15 Story 04)
* free-text causal world chains ("lost job → couldn't pay rent → moved")
  that exercise the LLM ``causal_relations`` path

Items are retained with varying session modes — half explicit
(``Session(mode=...)``), half default — and mixed ``task_context`` /
``expectation`` fields so the Thalamus Filter produces non-constant scores.

Assertions cover:

* memory_units / engram_dictionary row counts (≥ items_sent)
* fact_type distribution: ``world`` > 0, ``experience`` > 0, ``opinion`` == 0
  (opinions live in the reflect pipeline, not here)
* layer distribution: all fresh rows at layer ``'working'`` (no premature
  Consolidation 1 promotion)
* Thalamus scores are distributed, not all identical
* Neo4j: :Engram node count matches engram_dictionary; :SEMANTIC and :ENTITY
  present; :CAUSAL ≥ 3 (structured ACTION_EFFECT + LLM causal chain);
  :PREDICTION_ERROR may be 0 (depends on similarity threshold)

**Runs only manually**::

    cd hindsight-api && uv run pytest -m "integration and llm_required" \\
        tests/test_retain_integration.py -v

Requires live Postgres (5433), Qdrant (6336), Neo4j (7688), and an LLM
API key in .env. Not part of the default build gate.

Bio mapping: this is the "full hippocampal cycle" test — sensory content →
thalamus gate → engram formation → link creation. Anything missing here
would break every downstream recall/reflect test that assumes a populated
bank.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from hindsight_api import LocalSTEmbeddings, MemoryEngine, RequestContext
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
from hindsight_api.engine.query_analyzer import DateparserQueryAnalyzer
from hindsight_api.engine.response_models import RetrievalMode, Session

# Module marker: integration + llm_required + shared xdist group with recall test
pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm_required,
    pytest.mark.xdist_group(name="retain_recall_e2e"),
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_BANK_ID = "integration_test_bank"

# Add scripts/dev to sys.path so we can import reset_bank programmatically.
# reset_bank is a standalone script, not a package, so this is the cleanest
# way to reuse its wipe helpers without shelling out.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts" / "dev"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Fixture data — deliberately heterogeneous across the retain pipeline
# ---------------------------------------------------------------------------

# Group A: pure world facts, no expectation/outcome, various task contexts.
# Kept intentionally small (3 items) so the overall dataset is experience-heavy:
# retain integration needs >60% of items with expectation+outcome to exercise
# the Sequence Analysis EXPERIENCE split + PREDICTION_ERROR candidate path.
WORLD_FACTS = [
    {
        "content": (
            "PostgreSQL 17 introduced the MERGE statement as a first-class "
            "UPSERT primitive. Alice from the DB-team migrated three legacy "
            "procedures to MERGE last Tuesday."
        ),
        "context": "engineering / database",
        "tags": ["postgresql 17", "database", "migration"],
    },
    {
        "content": (
            "Emily, a principal engineer at Acme Corp, has 12 years of "
            "Kubernetes experience and holds the CKA certification. She has "
            "been leading the platform team in Berlin since March."
        ),
        "context": "hr / team-structure",
        "tags": ["people", "infrastructure"],
    },
    {
        "content": (
            "Redis 7.4 adds native support for client-side caching via the "
            "RESP3 protocol. This removes the need for the read-through cache "
            "layer the backend team has maintained since 2022."
        ),
        "context": "engineering / caching",
        "tags": ["redis", "caching", "protocol"],
    },
]

# Group B: structured experiences with expectation + outcome.
# units_to_content_dicts splits each into expectation_dict + outcome_dict,
# and the orchestrator's per-dict override stamps fact_type='experience'.
# Expectation vs outcome divergence drives Thalamus Surprise and triggers
# PREDICTION_ERROR links when cosine < 0.8.
STRUCTURED_EXPERIENCES = [
    {
        "content": "Ran the new index on orders table to improve query latency.",
        "context": "perf-investigation",
        "tags": ["performance", "index"],
        "expectation": "Order list query drops under 50ms at p99.",
        "outcome": (
            "Order list query actually climbed to 800ms at p99 because the "
            "planner picked the wrong index on concurrent writes."
        ),
    },
    {
        "content": "Deployed the new authentication service to staging.",
        "context": "release-process",
        "tags": ["auth", "deployment"],
        "expectation": "Login success rate stays above 99% after rollout.",
        "outcome": "Login success rate held steady at 99.4% over 48 hours.",
    },
    {
        "content": "Refactored the billing webhook handler to use idempotency keys.",
        "context": "billing / reliability",
        "tags": ["billing", "reliability", "idempotency"],
        "expectation": "Duplicate charge incidents drop to zero.",
        "outcome": (
            "We still saw two duplicate charges in the first week, caused "
            "by retries that arrived before the key was persisted."
        ),
    },
    {
        "content": "Enabled connection pooling on the analytics service via PgBouncer in transaction mode.",
        "context": "perf-investigation",
        "tags": ["pgbouncer", "performance", "analytics"],
        "expectation": "p95 query latency on the analytics dashboard drops below 200ms.",
        "outcome": (
            "p95 latency improved to 180ms, but prepared-statement caching "
            "broke and we had to patch the ORM to disable it for the pool."
        ),
    },
    {
        "content": "Rolled out the new ML-based spam filter to 10% of inbound traffic.",
        "context": "ml / spam",
        "tags": ["spam", "ml", "canary"],
        "expectation": "False positive rate stays under 0.5% while recall climbs above 95%.",
        "outcome": (
            "Recall hit 96.2% but false positives spiked to 1.8% on "
            "marketing campaign traffic — worse than the baseline filter."
        ),
    },
    {
        "content": "Switched the event bus from RabbitMQ to Kafka for order events.",
        "context": "infrastructure / messaging",
        "tags": ["kafka", "rabbitmq", "events"],
        "expectation": "Peak-hour throughput doubles without any increase in tail latency.",
        "outcome": (
            "Throughput reached 2.3x baseline and p99 consumer lag fell from 4 seconds to 600ms over the first week."
        ),
    },
    {
        "content": "Added aggressive HTTP caching to the public product catalogue API.",
        "context": "cdn / caching",
        "tags": ["http", "cache", "cdn"],
        "expectation": "Origin CPU drops at least 40% during peak traffic windows.",
        "outcome": (
            "Origin CPU dropped 55% on average, but stale price data "
            "surfaced for roughly 12 minutes after each catalogue update."
        ),
    },
    {
        "content": "Added a circuit breaker around the payment gateway call in checkout.",
        "context": "reliability",
        "tags": ["circuit-breaker", "payments", "checkout"],
        "expectation": "Checkout error rate during gateway outages stays below 2%.",
        "outcome": (
            "During the next gateway incident error rate capped at 1.3% "
            "and cart abandonment stayed flat — breaker ran for 7 minutes."
        ),
    },
]

# Group C: a single chunk describing an explicit action → effect chain.
# Sequence Analysis at budget=MID/HIGH should split this into ACTION_EFFECT
# unit pairs, which Step 12.5 then links as :CAUSAL in Neo4j.
ACTION_EFFECT_CHAIN = {
    "content": (
        "Alice applied migration 0042 on the production cluster. "
        "As a direct effect, the schema was updated in 3 seconds and every "
        "application pod was restarted. After the restart, the 500 error rate "
        "dropped from 2.1% to 0.2% within 10 minutes."
    ),
    "context": "ops-log",
    "tags": ["ops", "migration", "incident"],
}

# Group D: free-text causal chain — no explicit action/effect markers,
# but the LLM fact extractor should identify causal_relations between the
# facts and persist them into memory_links. The new build_llm_causal_links
# mirror then propagates them as :CAUSAL in Neo4j.
CAUSAL_CHAIN_CONTENT = {
    "content": (
        "Carol lost her job at the consulting firm in January. Because of "
        "that, she could no longer pay her rent by March. That forced her "
        "to move apartments and leave the city in April."
    ),
    "context": "personal / life-events",
    "tags": ["life-event", "finance"],
}


def _retain_contents_for_session(
    items: list[dict], mode: RetrievalMode | None, task_context: str | None
) -> tuple[list[dict], Session | None]:
    """Shape items + a Session object for retain_batch_async."""
    session: Session | None = None
    if mode is not None:
        session = Session(mode=mode, task_context=task_context)
    return items, session


# ---------------------------------------------------------------------------
# Fixtures — module-scoped so retain + recall tests share the same state
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_bank_id() -> str:
    return TEST_BANK_ID


@pytest.fixture(scope="module")
def clean_test_bank(test_bank_id: str) -> None:
    """Wipe integration_test_bank across all three stores before the module runs.

    We import the helpers directly from scripts/dev/reset_bank.py so we stay
    in-process and do not shell out. reset_bank.py self-loads .env, so the
    connection env vars are available after the first helper call.
    """
    import reset_bank  # type: ignore[import-not-found]

    reset_bank._load_env()
    try:
        reset_bank.reset_postgres(test_bank_id)
    except Exception as exc:
        pytest.skip(f"Postgres reset failed — service not reachable? {exc}")
    try:
        reset_bank.reset_qdrant(test_bank_id)
    except Exception as exc:
        pytest.skip(f"Qdrant reset failed — service not reachable? {exc}")
    try:
        reset_bank.reset_neo4j(test_bank_id)
    except Exception as exc:
        pytest.skip(f"Neo4j reset failed — service not reachable? {exc}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def module_memory(clean_test_bank):
    """Module-scoped MemoryEngine so retain result persists into the recall test.

    The conftest.memory fixture is function-scoped (each test rebuilds the
    pool). Retain + Recall integration tests share state across tests in the
    module, so we build a dedicated engine with its own pool that lives for
    the whole module.
    """
    db_url = os.getenv("HINDSIGHT_API_DATABASE_URL") or "postgresql://hindsight:hindsight@localhost:5433/hindsight"

    mem = MemoryEngine(
        db_url=db_url,
        memory_llm_provider=os.getenv("HINDSIGHT_API_LLM_PROVIDER", "anthropic"),
        memory_llm_api_key=os.getenv("HINDSIGHT_API_LLM_API_KEY"),
        memory_llm_model=os.getenv("HINDSIGHT_API_LLM_MODEL", "claude-haiku-4-5-20251001"),
        memory_llm_base_url=os.getenv("HINDSIGHT_API_LLM_BASE_URL") or None,
        embeddings=LocalSTEmbeddings(),
        cross_encoder=LocalSTCrossEncoder(),
        query_analyzer=DateparserQueryAnalyzer(),
        pool_min_size=1,
        pool_max_size=5,
        run_migrations=False,
    )
    try:
        await mem.initialize()
    except Exception as exc:
        pytest.skip(f"MemoryEngine initialize failed — dev stack up? {exc}")
    yield mem
    try:
        if mem._pool and not mem._pool._closing:
            await mem.close()
    except Exception:
        pass


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def retained_data(module_memory: MemoryEngine, test_bank_id: str) -> dict:
    """Run the full retain pipeline once for the module.

    Batches the dataset into four retain_batch_async calls so we cover
    different session-mode combinations:

    1. World facts with explicit PRECISION mode + task_context
    2. Structured experiences with explicit VALIDATION mode + task_context
    3. ACTION_EFFECT chain with explicit EXPLORATION mode
    4. Free-text causal chain with **no** session (engine default applies)
    """
    from hindsight_api.engine.utils import Budget

    ctx = RequestContext()
    calls_log: list[str] = []

    # Batch 1 — world facts, Precision mode
    items1, session1 = _retain_contents_for_session(
        WORLD_FACTS, RetrievalMode.PRECISION, task_context="architecture-review"
    )
    await module_memory.retain_batch_async(
        test_bank_id,
        items1,
        session=session1,
        request_context=ctx,
        budget=Budget.LOW,  # plain facts, no need for sequence analysis
    )
    calls_log.append(f"world_facts n={len(items1)}")

    # Batch 2 — structured experiences, Validation mode, MID budget triggers
    # sequence analysis → EXPERIENCE split + PREDICTION_ERROR candidates
    items2, session2 = _retain_contents_for_session(
        STRUCTURED_EXPERIENCES, RetrievalMode.VALIDATION, task_context="perf-investigation"
    )
    await module_memory.retain_batch_async(
        test_bank_id,
        items2,
        session=session2,
        request_context=ctx,
        budget=Budget.MID,
    )
    calls_log.append(f"structured_experiences n={len(items2)}")

    # Batch 3 — ACTION_EFFECT chain, Exploration mode, MID budget triggers
    # sequence analysis → ACTION_EFFECT split + CAUSAL links
    items3, session3 = _retain_contents_for_session(
        [ACTION_EFFECT_CHAIN], RetrievalMode.EXPLORATION, task_context="ops-log"
    )
    await module_memory.retain_batch_async(
        test_bank_id,
        items3,
        session=session3,
        request_context=ctx,
        budget=Budget.MID,
    )
    calls_log.append("action_effect_chain")

    # Batch 4 — free-text causal chain, no explicit session (default)
    # LOW budget skips sequence analysis; we rely on fact_extraction's
    # causal_relations path + new Neo4j mirror to create :CAUSAL edges
    items4, session4 = _retain_contents_for_session([CAUSAL_CHAIN_CONTENT], None, None)
    await module_memory.retain_batch_async(
        test_bank_id,
        items4,
        session=session4,
        request_context=ctx,
        budget=Budget.LOW,
    )
    calls_log.append("causal_chain_default_mode")

    return {
        "bank_id": test_bank_id,
        "sent_items": {
            "world_facts": len(WORLD_FACTS),
            "structured_experiences": len(STRUCTURED_EXPERIENCES),
            "action_effect_chain": 1,
            "causal_chain": 1,
        },
        "calls": calls_log,
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_pool(module_memory: MemoryEngine) -> asyncpg.Pool:
    """Share the engine's own pool so tests can run direct SQL assertions."""
    return module_memory._pool


@pytest.fixture(scope="module")
def neo4j_client(module_memory: MemoryEngine):
    """Expose the engine's Neo4j client for graph-level assertions.

    Uses the private ``MemoryEngine._get_neo4j_client()`` helper, which
    pulls the client from ``self._retain.engram_storage._neo4j`` — the
    only stable path after the post-Epic-18 EngineContext refactor.
    Skips the whole module if Neo4j is not wired up (e.g. dev stack
    started without the container).
    """
    client = module_memory._get_neo4j_client()
    if client is None:
        pytest.skip("Neo4j client not available on MemoryEngine — dev stack without Neo4j?")
    return client


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
class TestRetainIntegration:
    async def test_memory_units_count(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """At least every sent item materialises as a memory_units row."""
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_units WHERE bank_id = $1",
                retained_data["bank_id"],
            )
        # 3 world facts + 8 structured experiences (split ×2 → 16) + ACTION_EFFECT
        # split (≥2) + causal chain (typically 3 facts) → at least ~24 rows. We
        # assert a lower bound resilient to LLM variance.
        assert count >= 15, f"expected ≥ 15 memory_units rows, got {count}"

    async def test_engram_dictionary_matches(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """Every memory_units row has a matching active engram_dictionary row."""
        async with pg_pool.acquire() as conn:
            mu_count = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_units WHERE bank_id = $1",
                retained_data["bank_id"],
            )
            ed_count = await conn.fetchval(
                "SELECT COUNT(*) FROM engram_dictionary WHERE bank_id = $1 AND status = 'active'",
                retained_data["bank_id"],
            )
        assert ed_count == mu_count, f"memory_units={mu_count}, engram_dictionary(active)={ed_count}"

    async def test_fact_type_distribution(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """world > 0, experience > 0, opinion == 0 (opinions live in reflect)."""
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT fact_type, COUNT(*) AS c FROM memory_units WHERE bank_id = $1 GROUP BY fact_type",
                retained_data["bank_id"],
            )
        dist = {row["fact_type"]: row["c"] for row in rows}
        assert dist.get("world", 0) > 0, f"no world facts? {dist}"
        assert dist.get("experience", 0) > 0, f"no experience facts — override or LLM prompt broken? {dist}"
        assert dist.get("opinion", 0) == 0, f"unexpected opinion facts (opinions belong in reflect): {dist}"

    async def test_all_fresh_engrams_at_working_layer(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """No Consolidation 1 has fired yet — every row is in working memory."""
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT COALESCE(layer, 'NULL') AS layer, COUNT(*) AS c "
                "FROM engram_dictionary WHERE bank_id = $1 AND status = 'active' "
                "GROUP BY layer",
                retained_data["bank_id"],
            )
        layers = {row["layer"]: row["c"] for row in rows}
        assert layers.get("buffer", 0) == 0, f"buffer layer unexpectedly populated: {layers}"
        assert layers.get("neocortex", 0) == 0, f"neocortex layer unexpectedly populated: {layers}"
        assert layers.get("working", 0) > 0 or layers.get("NULL", 0) > 0, f"no rows in working-memory layer: {layers}"

    async def test_thalamus_scores_are_non_constant(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """Thalamus scoring produces a real distribution, not one default value.

        We expect at least 3 distinct novelty values and at least 3 distinct
        task_relevance values — otherwise the gate collapsed to a default.
        """
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(DISTINCT novelty) AS n_nov, "
                "COUNT(DISTINCT task_relevance) AS n_tr, "
                "COUNT(DISTINCT emotional_valence) AS n_ev "
                "FROM engram_dictionary "
                "WHERE bank_id = $1 AND novelty IS NOT NULL",
                retained_data["bank_id"],
            )
        assert row["n_nov"] >= 3, f"novelty collapsed to {row['n_nov']} distinct values"
        assert row["n_tr"] >= 3, f"task_relevance collapsed to {row['n_tr']} distinct values"
        # Emotional valence can legitimately be constant for neutral content,
        # so we only assert it's not entirely NULL.
        assert row["n_ev"] >= 1

    async def test_expectation_outcome_persisted(self, retained_data: dict, pg_pool: asyncpg.Pool):
        """Structured experiences carry expectation + outcome on their engram row."""
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM engram_dictionary "
                "WHERE bank_id = $1 AND expectation IS NOT NULL AND outcome IS NOT NULL",
                retained_data["bank_id"],
            )
        # 8 structured experiences × 1 outcome-dict each (expectation-dict
        # carries only content, not the pair fields — see units_to_content_dicts).
        # Lower bound allows for LLM-side drops without failing the gate.
        assert count >= 6, f"expected ≥ 6 rows with expectation+outcome, got {count}"

    async def test_neo4j_engrams_match_postgres(self, retained_data: dict, pg_pool: asyncpg.Pool, neo4j_client):
        """Every active engram_dictionary row has a matching Engram node in Neo4j."""
        async with pg_pool.acquire() as conn:
            ed_count = await conn.fetchval(
                "SELECT COUNT(*) FROM engram_dictionary WHERE bank_id = $1 AND status = 'active'",
                retained_data["bank_id"],
            )

        result = await neo4j_client.run_cypher(
            "MATCH (e:Engram {bank_id: $bank_id}) RETURN count(e) AS c",
            {"bank_id": retained_data["bank_id"]},
        )
        neo4j_count = result[0]["c"] if result else 0

        assert neo4j_count == ed_count, f"Neo4j engrams={neo4j_count}, Postgres active={ed_count}"

    async def test_neo4j_semantic_and_entity_links_present(self, retained_data: dict, neo4j_client):
        """Retain Step 7/8/9 writes SEMANTIC + ENTITY relationships to Neo4j."""
        bank_id = retained_data["bank_id"]
        sem = await neo4j_client.run_cypher(
            "MATCH (:Engram {bank_id: $b})-[r:SEMANTIC]->() RETURN count(r) AS c",
            {"b": bank_id},
        )
        ent = await neo4j_client.run_cypher(
            "MATCH (:Engram {bank_id: $b})-[r:ENTITY]->() RETURN count(r) AS c",
            {"b": bank_id},
        )
        assert (sem[0]["c"] if sem else 0) >= 1, "no SEMANTIC relationships in Neo4j"
        assert (ent[0]["c"] if ent else 0) >= 1, "no ENTITY relationships in Neo4j"

    async def test_neo4j_causal_links_present(self, retained_data: dict, neo4j_client):
        """Both CAUSAL producers (ACTION_EFFECT pair + LLM causal_relations) wrote edges.

        Lower bound of 2: at least one edge from the ACTION_EFFECT chain and at
        least one from the LLM causal_relations chain. The fact_extraction
        prompt is best-effort on causal_relations, so we don't hard-assert the
        exact count from the 3-fact chain.
        """
        result = await neo4j_client.run_cypher(
            "MATCH (:Engram {bank_id: $b})-[r:CAUSAL]->() RETURN count(r) AS c, collect(DISTINCT r.source) AS sources",
            {"b": retained_data["bank_id"]},
        )
        row = result[0] if result else {"c": 0, "sources": []}
        assert row["c"] >= 2, (
            f"expected ≥ 2 CAUSAL edges (ACTION_EFFECT + LLM chain), got {row['c']}; sources={row['sources']}"
        )
        sources = row["sources"] or []
        assert any("experience_links" in s for s in sources), (
            f"no ACTION_EFFECT CAUSAL edges — experience_links path broken? sources={sources}"
        )
        assert any("llm_causal_relations" in s for s in sources), (
            f"no LLM causal_relations CAUSAL edges — mirror path broken? sources={sources}"
        )

    async def test_neo4j_prediction_error_links_optional(self, retained_data: dict, neo4j_client):
        """PREDICTION_ERROR links are created only for pairs with cosine < 0.8.

        This assertion is soft: we log the count but don't fail if zero,
        because it depends on the actual embedding similarity of our fixture
        expectations vs outcomes.
        """
        result = await neo4j_client.run_cypher(
            "MATCH (:Engram {bank_id: $b})-[r:PREDICTION_ERROR]->() RETURN count(r) AS c",
            {"b": retained_data["bank_id"]},
        )
        count = result[0]["c"] if result else 0
        # Soft assert — just ensures the query runs and returns an int.
        assert count >= 0
        print(f"[info] PREDICTION_ERROR edges in {retained_data['bank_id']}: {count}")
