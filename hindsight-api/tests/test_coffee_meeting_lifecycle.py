"""Coffee-Meeting Schema-Lifecycle E2E test — Epic 25 Story 20.

Acceptance gate for the CLS refactor. Walks 30 synthetic coffee-meeting
engrams through the full new pipeline:

  Phase 1: Retain         — 30 engrams land at layer='working'
  Phase 2: C1             — promote to layer='buffer'
  Phase 3: C2 (×2)        — R2 maturation needs ≥ 2 cycles, then R4 mints
                            a `coffee_meeting` Schema with evidence_count ≥ 5
  Phase 4: Recall         — HybridRetriever + Top-N evidence resolution
                            returns the Schema *and* concrete engrams
  Phase 5: Reflect-payload— the bundle handed to the Reflect/Constructive
                            pipeline carries both Schema description and
                            ≥ 1 evidence engram (the LLM call itself is
                            out of scope — we verify the data shape)

LLM-free: schema_description uses the deterministic template fallback,
the assertion stops at the recall payload. Tests skip without
HINDSIGHT_TEST_QDRANT_URL / HINDSIGHT_TEST_NEO4J_URL / pg0 — same gate
as Story 19.
"""

from __future__ import annotations

import uuid

import asyncpg
import numpy as np
import pytest
import pytest_asyncio

from hindsight_api.engine.consolidation.ncr_orchestrator import run_c2_phase, run_c3_phase
from hindsight_api.engine.neo4j_client import Neo4jEngineClient
from hindsight_api.engine.qdrant_client import QdrantEngineClient
from hindsight_api.engine.schema.schema_repository import list_active_schemas
from hindsight_api.engine.search.evidence_resolver import resolve_all_schema_evidence
from hindsight_api.engine.search.hybrid_retriever import HybridRetriever
from hindsight_api.engine.session.mode_config import RetrievalMode

pytestmark = pytest.mark.integration

QDRANT_COLLECTION = "engrams-coffee-e2e"
EMBEDDING_DIM = 384
COFFEE_MEETING_COUNT = 30


# ---------------------------------------------------------------------------
# Live-store fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pg_pool(pg0_db_url):
    pool = await asyncpg.create_pool(pg0_db_url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
async def qdrant(qdrant_test_url):
    client = QdrantEngineClient(url=qdrant_test_url, api_key=None, collection=QDRANT_COLLECTION)
    await client.connect()
    await client.ensure_collection()
    yield client
    await client.close()


@pytest_asyncio.fixture(scope="module")
async def neo4j(neo4j_test_dsn):
    url, user, password = neo4j_test_dsn
    client = Neo4jEngineClient(bolt_url=url, username=user, password=password)
    await client.connect()
    await client.ensure_schema()
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# Synthetic data — 30 coffee-meeting engrams clustered tightly so HDBSCAN
# discovers them as a single coherent cluster (R1 cohesion ≥ 0.75).
# ---------------------------------------------------------------------------


def _coffee_centroid() -> np.ndarray:
    rng = np.random.default_rng(seed=4242)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _coffee_engram_vec(member: int) -> list[float]:
    centre = _coffee_centroid()
    rng = np.random.default_rng(seed=4242 * 1000 + member)
    noise = rng.standard_normal(EMBEDDING_DIM).astype(np.float32) * 0.01
    v = centre + noise
    v /= np.linalg.norm(v)
    return v.tolist()


def _coffee_engram_tags(member: int) -> list[str]:
    """Slight tag variation so property aggregation has something to roll up."""
    hour = 14 + (member % 4)  # 14–17h
    duration = 30 + (member % 7) * 5  # 30–60 min
    return [
        "activity:coffee",
        "format:1on1",
        f"hour:{hour}",
        f"duration:{duration}",
        "mood:productive",
    ]


def _coffee_text(member: int) -> str:
    hour = 14 + (member % 4)
    return f"Coffee 1:1 with Anna at {hour}:00 — productive working session"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_engram(pool, qdrant_client, *, bank_id: str, layer: str, member: int) -> uuid.UUID:
    eid = uuid.uuid4()
    embedding = _coffee_engram_vec(member)
    tags = _coffee_engram_tags(member)
    text = _coffee_text(member)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO memory_units (id, bank_id, text, fact_type) VALUES ($1, $2, $3, 'experience')",
            eid,
            bank_id,
            text,
        )
        await conn.execute(
            """
            INSERT INTO engram_dictionary
                (engram_id, bank_id, strength, layer, status, tags, thalamus_overall,
                 novelty, surprise, task_relevance, emotional_valence)
            VALUES ($1, $2, 0.7, $3, 'active', $4, 0.75, 0.6, 0.5, 0.7, 0.2)
            """,
            eid,
            bank_id,
            layer,
            tags,
        )
    await qdrant_client.upsert_point(
        engram_id=str(eid),
        embedding=embedding,
        payload={"bank_id": bank_id, "tags": tags, "text": text},
    )
    return eid


async def _cleanup(pool, qdrant_client, neo4j_client, bank_id: str, engram_ids: list[uuid.UUID]) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM engram_dictionary WHERE bank_id = $1", bank_id)
        await conn.execute("DELETE FROM memory_units WHERE bank_id = $1", bank_id)
        await conn.execute("DELETE FROM c2_cluster_fingerprints WHERE bank_id = $1", bank_id)
    for eid in engram_ids:
        try:
            await qdrant_client.delete_by_id(str(eid))
        except Exception:
            pass
    try:
        # Schemas may have been minted — drop everything that still has
        # this run's bank_id stamped on the centroid payload.
        rows = await neo4j_client.run_cypher("MATCH (s:Schema) RETURN s.id AS id")
        for r in rows:
            try:
                await qdrant_client.delete_by_id(r["id"])
            except Exception:
                pass
        await neo4j_client.run_cypher(
            "MATCH (s) WHERE (s:Schema OR s:HyperSchema) AND s.id IS NOT NULL DETACH DELETE s"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase walk — single test executes the full lifecycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coffee_meeting_full_lifecycle(pg_pool, qdrant, neo4j):
    bank_id = f"coffee-e2e-{uuid.uuid4().hex[:8]}"
    engram_ids: list[uuid.UUID] = []
    try:
        # ---- Phase 1: Retain — 30 working-layer engrams ------------------
        for i in range(COFFEE_MEETING_COUNT):
            eid = await _insert_engram(pg_pool, qdrant, bank_id=bank_id, layer="working", member=i)
            engram_ids.append(eid)

        async with pg_pool.acquire() as conn:
            n_working = await conn.fetchval(
                "SELECT COUNT(*) FROM engram_dictionary WHERE bank_id = $1 AND layer = 'working'",
                bank_id,
            )
        assert n_working == COFFEE_MEETING_COUNT

        # ---- Phase 2: C1 — promote all to buffer -------------------------
        # The C1 service has its own unit tests; here we simulate a
        # successful C1 run (composite-driven promotion) so the rest of
        # the pipeline can operate on buffer engrams.
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE engram_dictionary SET layer = 'buffer' WHERE bank_id = $1",
                bank_id,
            )

        # ---- Phase 3: C2 ×2 — first run cycles=1 (immature), second matures
        await run_c2_phase(bank_id, pool=pg_pool, qdrant=qdrant, neo4j=neo4j, llm_caller=None)
        c2_report = await run_c2_phase(bank_id, pool=pg_pool, qdrant=qdrant, neo4j=neo4j, llm_caller=None)
        assert c2_report.candidates_detected >= 1, "HDBSCAN should find ≥ 1 cluster"
        assert c2_report.matured >= 1, "second C2 run should mature the cluster"
        assert c2_report.created + c2_report.reinforced >= 1, "schema must be minted or reinforced"

        # The bank now holds at least one Schema with evidence_count ≥ 5.
        schemas = await list_active_schemas(neo4j, label="Schema", limit=10)
        coffee_schemas = [s for s in schemas if s.evidence_count >= 5]
        assert coffee_schemas, "expected a coffee-meeting schema with evidence_count ≥ 5"
        coffee_schema = coffee_schemas[0]

        # ---- Phase 4: Recall — HybridRetriever + Top-N Evidence ----------
        retriever = HybridRetriever(qdrant=qdrant, neo4j=neo4j, pg_pool=pg_pool)
        query_vec = _coffee_centroid().tolist()
        hits = await retriever.retrieve(query_vec, bank_id, k=10, mode=RetrievalMode.PRECISION)
        assert hits, "Recall must return at least one hit"

        # Schema must appear in the result list (Precision biases schemas).
        schema_hits = [h for h in hits if h.kind == "schema" and h.id == coffee_schema.id]
        assert schema_hits, "Schema centroid must appear in recall hits"

        # ---- Phase 5: Reflect-payload — pair Schema with Top-N evidence --
        bundles = await resolve_all_schema_evidence(hits, pool=pg_pool, bank_id=bank_id)
        schema_bundles = [(hit, ev) for hit, ev in bundles if hit.kind == "schema" and ev]
        assert schema_bundles, "Schema hit must come with ≥ 1 resolved evidence engram"
        hit, evidence = schema_bundles[0]
        # Description either came from the template fallback or stays empty;
        # what matters for the Reflect payload is the *paired* engrams.
        assert hit.description is not None
        assert all(e.text and e.id for e in evidence)

        # The complete Reflect payload thus contains both schema-level
        # generalisation (description + properties) and concrete instances.
        assert hit.properties or hit.description, "schema must carry generalisation content"
    finally:
        await _cleanup(pg_pool, qdrant, neo4j, bank_id, engram_ids)
