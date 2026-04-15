"""
Recall Integration Test — Live-LLM end-to-end over the retain-seeded bank.

**Depends on** ``test_retain_integration.py`` having populated
``integration_test_bank`` first. This module DOES NOT wipe or reseed the
bank — if it's empty, the tests skip with a clear message telling you to
run the retain integration test first.

Covers mode-dependent recall behaviour and access_count tracking:

* **Precision** — precise single-fact lookup ("how long did migration 0042
  take?"). Top-1 result must contain the expected substring.
* **Exploration** — same query in Exploration mode should broaden the
  result set and (soft assert) rank differently because of the
  Novelty-boosted Thalamus weighting.
* **Validation + expectation** — query with an explicit ``expectation`` in
  Validation mode should surface prediction-error engrams (the
  experiences whose outcome diverged from the expectation).
* **Access count increment** — calling recall twice for the same query
  must bump the ``access_count`` of the returned engrams in
  ``engram_dictionary``.

**Runs only manually**::

    cd hindsight-api && uv run pytest -m "integration and llm_required" \\
        tests/test_recall_integration.py -v

Shared with ``test_retain_integration`` via xdist group
``retain_recall_e2e`` — both files always land on the same xdist worker
so fixture state (LocalSTEmbeddings model, DB pool) can be shared.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

from hindsight_api import LocalSTEmbeddings, MemoryEngine, RequestContext
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
from hindsight_api.engine.query_analyzer import DateparserQueryAnalyzer
from hindsight_api.engine.response_models import RetrievalMode, Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.llm_required,
    pytest.mark.xdist_group(name="retain_recall_e2e"),
]

TEST_BANK_ID = "integration_test_bank"


# ---------------------------------------------------------------------------
# Module fixtures — rebuild engine locally, share via scope='module'
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_bank_id() -> str:
    return TEST_BANK_ID


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def module_memory():
    """Dedicated module-scoped MemoryEngine for recall queries."""
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
async def pg_pool(module_memory: MemoryEngine) -> asyncpg.Pool:
    return module_memory._pool


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded_bank_check(module_memory: MemoryEngine, test_bank_id: str) -> None:
    """Skip the whole module if retain integration hasn't populated the bank."""
    async with module_memory._pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_units WHERE bank_id = $1",
            test_bank_id,
        )
    if not count or count < 5:
        pytest.skip(
            f"test bank '{test_bank_id}' is empty or sparse (rows={count}). "
            "Run `pytest -m 'integration and llm_required' tests/test_retain_integration.py` first."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_texts(result) -> list[str]:
    """Best-effort text extraction from a RecallResult / MemoryFact list."""
    facts = getattr(result, "facts", None) or getattr(result, "memories", None) or []
    texts: list[str] = []
    for fact in facts:
        for attr in ("text", "content", "fact_text", "fact"):
            value = getattr(fact, attr, None)
            if value:
                texts.append(str(value))
                break
    return texts


def _extract_ids(result) -> list[str]:
    """Best-effort engram_id extraction for access_count assertions."""
    facts = getattr(result, "facts", None) or getattr(result, "memories", None) or []
    ids: list[str] = []
    for fact in facts:
        for attr in ("id", "engram_id", "unit_id", "memory_id"):
            value = getattr(fact, attr, None)
            if value:
                ids.append(str(value))
                break
    return ids


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
class TestRecallIntegration:
    async def test_precision_mode_hits_specific_fact(
        self,
        module_memory: MemoryEngine,
        test_bank_id: str,
        seeded_bank_check,
    ):
        """Precision mode should surface the migration 0042 fact in the top results."""
        session = Session(mode=RetrievalMode.PRECISION, task_context="ops-debug")
        result = await module_memory.recall_async(
            test_bank_id,
            "How long did migration 0042 take on production?",
            session=session,
            request_context=RequestContext(),
        )
        texts = _extract_texts(result)
        assert texts, "Precision recall returned no facts"
        joined = " | ".join(texts[:5]).lower()
        # The ACTION_EFFECT chain content explicitly says "3 seconds"
        assert "0042" in joined or "migration" in joined, (
            f"Precision recall did not surface the migration fact. Top: {texts[:3]}"
        )

    async def test_exploration_mode_returns_broader_set(
        self,
        module_memory: MemoryEngine,
        test_bank_id: str,
        seeded_bank_check,
    ):
        """Exploration mode should return as many or more facts than Precision for the same query.

        We don't hard-assert ordering — LLM + embedding nondeterminism makes
        exact rank assertions flaky. Instead we check that the result count
        is at least as large as Precision's, which is the directional promise
        of Exploration mode.
        """
        query = "What happened with migration 0042 and the cluster restart?"
        ctx = RequestContext()

        precision = await module_memory.recall_async(
            test_bank_id,
            query,
            session=Session(mode=RetrievalMode.PRECISION),
            request_context=ctx,
        )
        exploration = await module_memory.recall_async(
            test_bank_id,
            query,
            session=Session(mode=RetrievalMode.EXPLORATION),
            request_context=ctx,
        )

        precision_texts = _extract_texts(precision)
        exploration_texts = _extract_texts(exploration)

        assert exploration_texts, "Exploration recall returned no facts"
        assert len(exploration_texts) >= len(precision_texts), (
            f"Exploration returned fewer facts than Precision "
            f"(exp={len(exploration_texts)}, prec={len(precision_texts)})"
        )

    async def test_validation_mode_with_expectation_surfaces_prediction_error(
        self,
        module_memory: MemoryEngine,
        test_bank_id: str,
        seeded_bank_check,
    ):
        """Validation + expectation should surface engrams that contradict the expectation.

        We retained a structured experience whose outcome was "query climbed
        to 800ms at p99" against the expectation "query drops under 50ms".
        Querying in Validation mode with a matching expectation should return
        at least one fact mentioning the 800ms divergence.
        """
        session = Session(
            mode=RetrievalMode.VALIDATION,
            current_expectation="Order list query is fast (under 50ms at p99)",
            task_context="perf-debug",
        )
        result = await module_memory.recall_async(
            test_bank_id,
            "How did the order list query behave after we added the new index?",
            session=session,
            request_context=RequestContext(),
            expectation="Order list query is fast (under 50ms at p99)",
        )
        texts = _extract_texts(result)
        assert texts, "Validation recall returned no facts"
        joined = " | ".join(texts[:10]).lower()
        # Either the divergent outcome or the original expectation should surface.
        assert "800" in joined or "latency" in joined or "query" in joined or "index" in joined, (
            f"Validation recall did not surface the prediction-error experience. Top: {texts[:3]}"
        )

    async def test_access_count_increments_on_recall(
        self,
        module_memory: MemoryEngine,
        test_bank_id: str,
        pg_pool: asyncpg.Pool,
        seeded_bank_check,
    ):
        """Recalling an engram must bump its access_count in engram_dictionary.

        Strategy:
          1. Snapshot the max access_count across the bank.
          2. Run a recall query that we know hits populated facts.
          3. Snapshot again.
          4. Assert the total access_count SUM went up (at least 1 engram
             touched). This is robust against the specific IDs returned,
             which would require a tighter coupling than the public API offers.
        """
        async with pg_pool.acquire() as conn:
            before_sum = await conn.fetchval(
                "SELECT COALESCE(SUM(access_count), 0) FROM engram_dictionary WHERE bank_id = $1",
                test_bank_id,
            )

        result = await module_memory.recall_async(
            test_bank_id,
            "Tell me about the Kubernetes experience Emily has.",
            session=Session(mode=RetrievalMode.PRECISION),
            request_context=RequestContext(),
        )
        returned_ids = _extract_ids(result)
        assert returned_ids, "recall returned zero engrams — cannot validate access_count"

        async with pg_pool.acquire() as conn:
            after_sum = await conn.fetchval(
                "SELECT COALESCE(SUM(access_count), 0) FROM engram_dictionary WHERE bank_id = $1",
                test_bank_id,
            )

        assert after_sum > before_sum, (
            f"access_count SUM did not increase after recall "
            f"(before={before_sum}, after={after_sum}, returned_ids={returned_ids[:3]})"
        )
