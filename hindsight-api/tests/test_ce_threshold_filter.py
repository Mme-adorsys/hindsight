"""
Regression test for the CE-threshold filter bug.

Before the fix: the recall orchestrator compared the raw cross-encoder logit
(cross_encoder_score) against ce_min_score, but the mode-config thresholds
(0.01-0.05) were calibrated for the sigmoid-normalized score. Relevant
candidates with raw CE logits in the negative range (common for single-token
queries against long multilingual engrams) were silently dropped.

This test seeds a single-token-matchable German engram and asserts exploration
mode surfaces it — which is only possible when the filter uses
cross_encoder_score_normalized.

Uses real embeddings + real local cross-encoder + real PostgreSQL. LLM
verification is skipped so the test does not need credentials.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
from hindsight_api.engine.embeddings import LocalSTEmbeddings
from hindsight_api.engine.query_analyzer import DateparserQueryAnalyzer
from hindsight_api.engine.response_models import RetrievalMode, Session

pytestmark = [pytest.mark.integration]

# Single-token German keyword chosen because:
#  - 'simple' tsvector produces an exact token match → BM25 finds it
#  - semantic similarity of the single word against the full sentence is low
#  - local cross-encoder returns a negative raw logit for this pair
# Together this is the exact condition under which the old filter dropped
# the candidate. After the fix, sigmoid-normalized score > 0.01 (exploration
# threshold) lets it through.
BANK_ID = "ce-threshold-test-bank"
ENGRAM_TEXT = (
    "Ab Git Version 2.28 wird der Standard-Branch-Name 'main' automatisch "
    "für neue Repositories verwendet."
)
QUERY = "branch"


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def isolated_memory(pg0_db_url):
    """Dedicated MemoryEngine that skips LLM verification — recall does not call the LLM."""
    mem = MemoryEngine(
        db_url=pg0_db_url,
        memory_llm_provider=os.getenv("HINDSIGHT_API_LLM_PROVIDER", "anthropic"),
        memory_llm_api_key=os.getenv("HINDSIGHT_API_LLM_API_KEY", "not-used-in-this-test"),
        memory_llm_model=os.getenv("HINDSIGHT_API_LLM_MODEL", "claude-haiku-4-5-20251001"),
        memory_llm_base_url=os.getenv("HINDSIGHT_API_LLM_BASE_URL") or None,
        embeddings=LocalSTEmbeddings(),
        cross_encoder=LocalSTCrossEncoder(),
        query_analyzer=DateparserQueryAnalyzer(),
        pool_min_size=1,
        pool_max_size=3,
        run_migrations=False,
        skip_llm_verification=True,
    )
    await mem.initialize()
    try:
        yield mem
    finally:
        try:
            if mem._pool and not mem._pool._closing:
                await mem.close()
        except Exception:
            pass


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def seeded_bank(isolated_memory: MemoryEngine):
    """Insert one engram that BM25 can find by 'branch', then clean up afterwards."""
    pool = isolated_memory._pool
    assert pool is not None, "Pool must be initialized"

    vector = isolated_memory.embeddings.encode([ENGRAM_TEXT])[0]
    embedding_literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
    engram_id = uuid.uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory_units (id, bank_id, text, context, fact_type, embedding, event_date)
            VALUES ($1, $2, $3, $4, $5, $6::vector, $7)
            """,
            engram_id,
            BANK_ID,
            ENGRAM_TEXT,
            "",
            "world",
            embedding_literal,
            datetime.now(UTC),
        )

    try:
        yield engram_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_units WHERE bank_id = $1",
                BANK_ID,
            )


@pytest.mark.asyncio(loop_scope="function")
async def test_exploration_recalls_single_token_bm25_match(
    isolated_memory: MemoryEngine,
    seeded_bank,
):
    """
    Single-token query 'branch' against a German engram that contains the
    word 'Branch-Name'. BM25 finds it, semantic similarity is too low for
    the Exploration threshold (0.5), and the cross-encoder returns a
    negative raw logit.

    Before the fix: empty results (raw logit < ce_min_score=0.01).
    After the fix:  at least one result (sigmoid(logit) >= 0.01).
    """
    result = await isolated_memory.recall_async(
        BANK_ID,
        QUERY,
        session=Session(mode=RetrievalMode.EXPLORATION),
        request_context=RequestContext(),
    )

    facts = result.results
    assert facts, (
        "Exploration-mode recall returned no results. The CE-threshold filter "
        "likely compared a raw logit against a sigmoid-scaled threshold again."
    )
    texts = " | ".join(getattr(f, "text", "") or "" for f in facts)
    assert "Branch" in texts or "branch" in texts, (
        f"Expected a 'branch' engram in results, got: {texts[:200]}"
    )


