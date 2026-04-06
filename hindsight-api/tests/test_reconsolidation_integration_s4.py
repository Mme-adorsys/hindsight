"""
Reconsolidation Integration Tests — Epic 10 Story 04.

Tests validate the reconsolidation pipeline against live databases:
  PostgreSQL (engram_dictionary + memory_units)
  Qdrant     (semantic trigger — skipped if HINDSIGHT_TEST_QDRANT_URL not set)

Tests skip automatically when services are not available.

Run with live services:
  docker compose -f docker-compose.test.yml up -d
  uv run pytest tests/test_reconsolidation_integration_s4.py -m integration -v

Test policy: Ab Epic 07 — Retrieval/Integration tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group(name="recon_integration")]

# ---------------------------------------------------------------------------
# Bank identifier for this test suite
# ---------------------------------------------------------------------------
RECON_TEST_BANK = "recon-test-bank"
_NS = uuid.UUID("deadbeef-dead-beef-dead-deadbeefcafe")


def _uid(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


# ---------------------------------------------------------------------------
# Fixture data — 20 engrams with controlled strengths
# ---------------------------------------------------------------------------
# Strengths spread from 0.05 (very weak) to 0.95 (very strong)
_STRENGTHS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.28, 0.35, 0.45, 0.50, 0.55,
              0.60, 0.65, 0.70, 0.75, 0.80, 0.82, 0.85, 0.88, 0.92, 0.95]

FIXTURE_ENGRAMS = [
    {
        "id": _uid(f"recon-{i:02d}"),
        "text": f"Test memory unit {i:02d} — reconsolidation fixture.",
        "strength": _STRENGTHS[i],
        "last_accessed": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
    }
    for i in range(20)
]

# 2 Engrams flagged as Prediction Errors (strong ones — to verify PE priority)
PE_ENGRAM_IDS = [_uid("recon-18"), _uid("recon-19")]  # strength 0.92, 0.95


# ---------------------------------------------------------------------------
# Postgres fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_pool(pg0_db_url):
    pool = await asyncpg.create_pool(pg0_db_url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def recon_data(pg_pool):
    """Insert fixture Engrams into PostgreSQL, yield, then clean up."""
    async with pg_pool.acquire() as conn:
        # Ensure bank exists
        await conn.execute(
            """
            INSERT INTO banks (bank_id, name, disposition, background)
            VALUES ($1, $1, '{"skepticism": 3, "literalism": 3, "empathy": 3}'::jsonb, '')
            ON CONFLICT (bank_id) DO NOTHING
            """,
            RECON_TEST_BANK,
        )
        for e in FIXTURE_ENGRAMS:
            await conn.execute(
                """
                INSERT INTO memory_units (id, bank_id, text, fact_type, event_date)
                VALUES ($1::uuid, $2, $3, 'world', NOW())
                ON CONFLICT (id) DO NOTHING
                """,
                e["id"],
                RECON_TEST_BANK,
                e["text"],
            )
            await conn.execute(
                """
                INSERT INTO engram_dictionary
                    (engram_id, bank_id, strength, status, last_accessed)
                VALUES ($1::uuid, $2, $3, 'active', $4)
                ON CONFLICT (engram_id) DO NOTHING
                """,
                e["id"],
                RECON_TEST_BANK,
                e["strength"],
                e["last_accessed"],
            )

    yield

    # Cleanup
    ids = [e["id"] for e in FIXTURE_ENGRAMS]
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM engram_dictionary WHERE engram_id::text = ANY($1)", ids
        )
        await conn.execute(
            "DELETE FROM memory_units WHERE id::text = ANY($1)", ids
        )


# ---------------------------------------------------------------------------
# Qdrant fixture (optional — skipped if URL not set)
# ---------------------------------------------------------------------------

QDRANT_URL = os.getenv("HINDSIGHT_TEST_QDRANT_URL", "")
skip_no_qdrant = pytest.mark.skipif(not QDRANT_URL, reason="HINDSIGHT_TEST_QDRANT_URL not set")


# ---------------------------------------------------------------------------
# T2 — Priority Queue ordering from DB data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
class TestPriorityQueueFromDB:
    async def test_filter_entries_returns_all_active(self, pg_pool, recon_data):
        """filter_entries returns all 20 active Engrams for the bank."""
        from hindsight_api.engine.engram_dictionary import filter_entries

        rows = await filter_entries(pg_pool, bank_id=RECON_TEST_BANK, status="active", limit=50)
        assert len(rows) == 20

    async def test_pe_engrams_first_in_queue(self, pg_pool, recon_data):
        """PE-flagged Engrams come first regardless of strength."""
        from hindsight_api.engine.engram_dictionary import filter_entries
        from hindsight_api.engine.reflect.prediction_error_registry import PredictionErrorRegistry
        from hindsight_api.engine.reflect.reconsolidation_queue import (
            WEAK_STRENGTH_THRESHOLD,
            build_reconsolidation_queue,
        )
        from hindsight_api.engine.response_models import EngramMetadata, FullEngram
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        from hindsight_api.engine.session.session_manager import RetrievalMode

        rows = await filter_entries(pg_pool, bank_id=RECON_TEST_BANK, status="active", limit=50)
        engrams = [
            FullEngram(
                engram_id=r["engram_id"],
                metadata=EngramMetadata(
                    engram_id=r["engram_id"],
                    bank_id=RECON_TEST_BANK,
                    strength=float(r.get("strength", 0.0)),
                    last_accessed=r.get("last_accessed"),
                ),
            )
            for r in rows
        ]

        pe_reg = PredictionErrorRegistry()
        for eid in PE_ENGRAM_IDS:
            pe_reg.flag_prediction_error(eid, "test-pe")

        mode = MODE_PROFILES[RetrievalMode.PRECISION].with_overrides(reconsolidation_level="aggressive")
        queue = build_reconsolidation_queue(engrams, pe_reg, mode)

        # First 2 must be PE-flagged
        top2_ids = {str(q.engram_id) for q in queue[:2]}
        assert top2_ids == set(PE_ENGRAM_IDS)

    async def test_weak_engrams_before_strong_in_queue(self, pg_pool, recon_data):
        """After PE entries, all weak Engrams (strength < 0.3) come before stronger ones."""
        from hindsight_api.engine.engram_dictionary import filter_entries
        from hindsight_api.engine.reflect.reconsolidation_queue import (
            WEAK_STRENGTH_THRESHOLD,
            build_reconsolidation_queue,
        )
        from hindsight_api.engine.response_models import EngramMetadata, FullEngram
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        from hindsight_api.engine.session.session_manager import RetrievalMode

        rows = await filter_entries(pg_pool, bank_id=RECON_TEST_BANK, status="active", limit=50)
        engrams = [
            FullEngram(
                engram_id=r["engram_id"],
                metadata=EngramMetadata(
                    engram_id=r["engram_id"],
                    bank_id=RECON_TEST_BANK,
                    strength=float(r.get("strength", 0.0)),
                    last_accessed=r.get("last_accessed"),
                ),
            )
            for r in rows
        ]

        mode = MODE_PROFILES[RetrievalMode.PRECISION].with_overrides(reconsolidation_level="aggressive")
        queue = build_reconsolidation_queue(engrams, None, mode)

        # Find last weak engram and first strong engram in queue
        last_weak_idx = max(
            (i for i, e in enumerate(queue) if e.metadata.strength < WEAK_STRENGTH_THRESHOLD),
            default=-1,
        )
        first_strong_idx = min(
            (i for i, e in enumerate(queue) if e.metadata.strength >= WEAK_STRENGTH_THRESHOLD),
            default=len(queue),
        )
        # All weak must appear before all strong
        assert last_weak_idx < first_strong_idx

    async def test_budget_minimal_returns_5(self, pg_pool, recon_data):
        """Minimal budget caps queue at 5."""
        from hindsight_api.engine.engram_dictionary import filter_entries
        from hindsight_api.engine.reflect.reconsolidation_queue import build_reconsolidation_queue
        from hindsight_api.engine.response_models import EngramMetadata, FullEngram
        from hindsight_api.engine.session.mode_config import MODE_PROFILES
        from hindsight_api.engine.session.session_manager import RetrievalMode

        rows = await filter_entries(pg_pool, bank_id=RECON_TEST_BANK, status="active", limit=50)
        engrams = [
            FullEngram(
                engram_id=r["engram_id"],
                metadata=EngramMetadata(
                    engram_id=r["engram_id"],
                    bank_id=RECON_TEST_BANK,
                    strength=float(r.get("strength", 0.0)),
                    last_accessed=r.get("last_accessed"),
                ),
            )
            for r in rows
        ]

        mode = MODE_PROFILES[RetrievalMode.PRECISION].with_overrides(reconsolidation_level="minimal")
        queue = build_reconsolidation_queue(engrams, None, mode)
        assert len(queue) == 5


# ---------------------------------------------------------------------------
# T4 — Strength update persists to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
class TestStrengthUpdatePersistence:
    async def test_update_strength_reflects_in_filter_entries(self, pg_pool, recon_data):
        """After update_strength(), the new value is visible via filter_entries."""
        from hindsight_api.engine.engram_dictionary import filter_entries, update_strength

        target_id = _uid("recon-00")  # strength=0.05
        await update_strength(pg_pool, target_id, 0.15)

        rows = await filter_entries(pg_pool, bank_id=RECON_TEST_BANK, status="active", limit=50)
        updated = next((r for r in rows if str(r["engram_id"]) == target_id), None)
        assert updated is not None
        assert abs(float(updated["strength"]) - 0.15) < 0.001


# ---------------------------------------------------------------------------
# T5 — Disposition variation: different outcomes for same raw_delta
# ---------------------------------------------------------------------------


class TestDispositionVariation:
    def test_analytical_updates_stronger_than_conservative(self):
        """Analytical produces a larger absolute delta than Conservative for confirmed outcome."""
        from hindsight_api.engine.reflect.disposition_profile import (
            ANALYTICAL,
            CONSERVATIVE,
            apply_modulated_strength_delta,
        )

        raw_delta = +0.1
        current = 0.5
        sim = 0.8

        analytical_result = apply_modulated_strength_delta(current, raw_delta, True, sim, ANALYTICAL)
        conservative_result = apply_modulated_strength_delta(current, raw_delta, True, sim, CONSERVATIVE)

        # Analytical has evidence_weight=1.2 vs Conservative 0.8 + no bias
        assert analytical_result > conservative_result

    def test_conservative_suppresses_contradiction_at_low_similarity(self):
        """Conservative disposition blocks contradiction at low similarity; Analytical does not."""
        from hindsight_api.engine.reflect.disposition_profile import (
            ANALYTICAL,
            CONSERVATIVE,
            apply_modulated_strength_delta,
        )

        raw_delta = -0.2
        current = 0.6
        # sim=0.4: above ANALYTICAL tolerance (0.3) so contradiction applied,
        # but below CONSERVATIVE tolerance (0.7) so contradiction suppressed
        low_sim = 0.4

        analytical_result = apply_modulated_strength_delta(current, raw_delta, False, low_sim, ANALYTICAL)
        conservative_result = apply_modulated_strength_delta(current, raw_delta, False, low_sim, CONSERVATIVE)

        assert conservative_result == pytest.approx(current)  # suppressed (0.4 < 0.7)
        assert analytical_result < current  # applied (0.4 >= 0.3)

    def test_optimistic_bias_lifts_confirmed_above_analytical(self):
        """Optimistic adds update_bias=0.2 on CONFIRMED; net result may beat Analytical despite lower weight."""
        from hindsight_api.engine.reflect.disposition_profile import (
            ANALYTICAL,
            OPTIMISTIC,
            apply_modulated_strength_delta,
        )

        raw_delta = +0.1
        current = 0.4
        sim = 0.7

        analytical = apply_modulated_strength_delta(current, raw_delta, True, sim, ANALYTICAL)
        optimistic = apply_modulated_strength_delta(current, raw_delta, True, sim, OPTIMISTIC)

        # Optimistic: 0.4 + (0.1*0.8) + 0.2 = 0.68; Analytical: 0.4 + (0.1*1.2) = 0.52
        assert optimistic > analytical


# ---------------------------------------------------------------------------
# T3 — Semantic Trigger (Qdrant required)
# ---------------------------------------------------------------------------


class TestSemanticTriggerWithQdrant:
    @skip_no_qdrant
    @pytest.mark.asyncio
    async def test_qdrant_returns_candidates_above_threshold(self):
        """find_reconsolidation_candidates returns hits with score >= 0.6."""
        import numpy as np

        from hindsight_api.engine.qdrant_client import QdrantEngineClient
        from hindsight_api.engine.reflect.semantic_trigger import find_reconsolidation_candidates

        qdrant = QdrantEngineClient(url=QDRANT_URL, collection="engrams-test")
        # Use a random embedding — just validates that the function runs without error
        rng = np.random.default_rng(seed=99)
        embedding = (rng.standard_normal(384).astype(np.float32)).tolist()
        # Normalise
        norm = float(np.linalg.norm(embedding))
        embedding = [v / norm for v in embedding]

        candidates = await find_reconsolidation_candidates(
            qdrant_client=qdrant,
            query_embedding=embedding,
            bank_id=RECON_TEST_BANK,
        )
        # All returned candidates must have score >= 0.6
        for c in candidates:
            assert c.similarity_score >= 0.6
