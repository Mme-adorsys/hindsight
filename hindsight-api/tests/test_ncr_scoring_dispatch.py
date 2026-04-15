"""
Dispatch-level tests for the Epic 24 Story 06 NCR migration.

Replaces the old per-phase mock tests (test_consolidation1.py,
test_ncr_decay.py, test_ncr_strengthen.py, test_knowledge_evolution.py,
test_composite_strength.py) that relied on the deleted mode-based scoring.

The formula primitives themselves are covered by
``test_sessions_alive.py``, ``test_equilibrium_rate.py``,
``test_decay_composite.py``, and ``test_tag_thresholds.py``. This file only
verifies that the three NCR services wire the primitives together correctly:

- Consolidation1Service: fetches session_count + engram_count, computes
  composite, and routes to promote/skip/archive based on the new thresholds.
- DecayProcessor: persists the recomputed composite as strength, archives
  below the layer-appropriate threshold, and guards neocortex.
- StrengthenProcessor: reads composite from strength, uses
  passes_hard_gates + tag threshold + karenz period, and promotes without
  the old +0.1 boost.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.consolidation1 import Consolidation1Service
from hindsight_api.engine.consolidation.ncr_decay import DecayProcessor
from hindsight_api.engine.consolidation.ncr_strengthen import StrengthenProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engram_row(
    engram_id: str,
    *,
    layer: str | None = None,
    tags: list[str] | None = None,
    strength: float = 0.0,
    access_count: int = 0,
    created_at_session: int = 0,
    novelty: float | None = 0.7,
    surprise: float = 0.5,
    task_relevance: float = 0.5,
    emotional_valence: float = 0.5,
    thalamus_overall: float = 0.7,
    session_mode: str | None = "precision",
    ncr_cycles_survived: int = 0,
) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": "test-bank",
        "layer": layer,
        "tags": tags,
        "strength": strength,
        "access_count": access_count,
        "created_at_session": created_at_session,
        "novelty": novelty,
        "surprise": surprise,
        "task_relevance": task_relevance,
        "emotional_valence": emotional_valence,
        "thalamus_overall": thalamus_overall,
        "session_mode": session_mode,
        "ncr_cycles_survived": ncr_cycles_survived,
        "status": "active",
        "last_accessed": None,
    }


async def _run_service_with_batches(svc, batches: list[list[dict]], patch_targets: dict) -> object:
    """Patch ``dict_repo`` entry points and run the service once."""
    return_values = batches + [[]]
    mock_list = AsyncMock(side_effect=return_values)

    mocks = {
        "hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_session_count": AsyncMock(
            return_value=patch_targets.get("session_count", 10)
        ),
        "hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_engram_count": AsyncMock(
            return_value=patch_targets.get("engram_count", 1000)
        ),
        "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated": mock_list,
        "hindsight_api.engine.consolidation.ncr_decay.dict_repo.get_bank_session_count": AsyncMock(
            return_value=patch_targets.get("session_count", 10)
        ),
        "hindsight_api.engine.consolidation.ncr_decay.dict_repo.get_bank_engram_count": AsyncMock(
            return_value=patch_targets.get("engram_count", 1000)
        ),
        "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay": mock_list,
        "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.get_bank_engram_count": AsyncMock(
            return_value=patch_targets.get("engram_count", 1000)
        ),
        "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen": mock_list,
        "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles": AsyncMock(),
    }

    patches = [patch(path, m) for path, m in mocks.items()]
    for p in patches:
        p.start()
    try:
        if isinstance(svc, Consolidation1Service):
            return await svc.run("test-bank")
        if isinstance(svc, DecayProcessor):
            return await svc.process("test-bank")
        return await svc.process("test-bank")
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Consolidation1Service (C1)
# ---------------------------------------------------------------------------


class TestConsolidation1Dispatch:
    @pytest.mark.asyncio
    async def test_low_novelty_archives_engram(self) -> None:
        storage = AsyncMock()
        entry = _engram_row("e1", novelty=0.1, thalamus_overall=0.5, access_count=20)
        svc = Consolidation1Service(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.archived == 1
        assert result.consolidated == 0
        storage.update_metadata.assert_awaited_with("e1", {"status": "archived", "strength": 0.0})

    @pytest.mark.asyncio
    async def test_synthesized_engram_bypasses_novelty_gate(self) -> None:
        """NULL novelty → no novelty check, bank-size min_access still enforced."""
        storage = AsyncMock()
        entry = _engram_row(
            "e2",
            novelty=None,
            thalamus_overall=0.9,
            access_count=5,  # min_access at bank_size=1000 is exactly 5
            tags=["experience"],  # threshold 0.4
        )
        svc = Consolidation1Service(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.consolidated == 1
        # Promoted to buffer with composite-as-strength
        args, _ = storage.update_metadata.await_args
        assert args[0] == "e2"
        assert args[1]["layer"] == "buffer"
        assert args[1]["strength"] > 0.0

    @pytest.mark.asyncio
    async def test_hard_gates_block_promotion(self) -> None:
        """Strong thalamus + too few accesses → stays in WM with updated strength."""
        storage = AsyncMock()
        entry = _engram_row(
            "e3",
            novelty=0.8,
            thalamus_overall=0.9,
            access_count=2,  # below min_access(1000)=5
            tags=["fact"],
        )
        svc = Consolidation1Service(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.skipped == 1
        assert result.consolidated == 0
        args, _ = storage.update_metadata.await_args
        assert args[0] == "e3"
        assert "layer" not in args[1]  # no promote
        assert "strength" in args[1]

    @pytest.mark.asyncio
    async def test_fact_tagged_engram_needs_higher_composite(self) -> None:
        """Fact threshold is 0.7 — a composite of 0.5 stays in WM even after gates pass."""
        storage = AsyncMock()
        entry = _engram_row(
            "e4",
            novelty=0.8,
            thalamus_overall=0.5,  # composite at sessions_alive=0 is just 0.5
            access_count=10,
            tags=["fact"],
            created_at_session=10,  # fresh (same as bank counter → sessions_alive=0)
        )
        svc = Consolidation1Service(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(
            svc,
            [[entry]],
            {"session_count": 10, "engram_count": 1000},
        )

        assert result.skipped == 1


# ---------------------------------------------------------------------------
# DecayProcessor (C2a)
# ---------------------------------------------------------------------------


class TestDecayDispatch:
    @pytest.mark.asyncio
    async def test_neocortex_is_skipped(self) -> None:
        storage = AsyncMock()
        qdrant = AsyncMock()
        entry = _engram_row("e5", layer="neocortex", strength=0.9, thalamus_overall=0.9)
        svc = DecayProcessor(pool=MagicMock(), storage_service=storage, qdrant=qdrant)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        # Neocortex never touched
        storage.update_metadata.assert_not_awaited()
        assert result.unchanged == 1
        assert result.archived == 0

    @pytest.mark.asyncio
    async def test_active_engram_strength_updated_to_composite(self) -> None:
        storage = AsyncMock()
        qdrant = AsyncMock()
        # Fresh buffer engram: sessions_alive = 10 - 10 = 0 → decay=1.0 →
        # composite = thalamus_overall = 0.9. Old strength 0.4 differs → decayed.
        entry = _engram_row(
            "e6",
            layer="buffer",
            strength=0.4,
            thalamus_overall=0.9,
            access_count=5,
            created_at_session=10,
        )
        svc = DecayProcessor(pool=MagicMock(), storage_service=storage, qdrant=qdrant)

        result = await _run_service_with_batches(
            svc,
            [[entry]],
            {"session_count": 10, "engram_count": 1000},
        )

        assert result.decayed == 1
        args, _ = storage.update_metadata.await_args
        assert args[0] == "e6"
        assert args[1]["strength"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_sub_threshold_composite_archives(self) -> None:
        storage = AsyncMock()
        qdrant = AsyncMock()
        # Unused working engram, many sessions old → decay ≈ 0 → composite ≈ 0
        entry = _engram_row(
            "e7",
            layer="working",
            strength=0.5,
            thalamus_overall=0.5,
            access_count=0,
            created_at_session=0,
        )
        svc = DecayProcessor(pool=MagicMock(), storage_service=storage, qdrant=qdrant)

        result = await _run_service_with_batches(
            svc,
            [[entry]],
            {"session_count": 100, "engram_count": 1000},
        )

        assert result.archived == 1
        # Dictionary + Neo4j update
        archived_calls = [c for c in storage.update_metadata.await_args_list if c.args[1].get("status") == "archived"]
        assert len(archived_calls) == 1


# ---------------------------------------------------------------------------
# StrengthenProcessor (C2b)
# ---------------------------------------------------------------------------


class TestStrengthenDispatch:
    @pytest.mark.asyncio
    async def test_promotes_on_tag_threshold_and_gates(self) -> None:
        storage = AsyncMock()
        entry = _engram_row(
            "e8",
            layer="buffer",
            strength=0.6,  # composite already computed in C2a, exceeds experience=0.4
            tags=["experience"],
            access_count=10,
            novelty=0.8,
            ncr_cycles_survived=3,  # above karenz period (2)
        )
        svc = StrengthenProcessor(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.promoted == 1
        # Promotion persists layer=neocortex + current strength (no +0.1 boost)
        args, _ = storage.update_metadata.await_args
        assert args[0] == "e8"
        assert args[1]["layer"] == "neocortex"
        assert args[1]["strength"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_skips_within_karenz_period(self) -> None:
        storage = AsyncMock()
        entry = _engram_row(
            "e9",
            layer="buffer",
            strength=0.9,
            tags=["experience"],
            access_count=20,
            novelty=0.9,
            ncr_cycles_survived=1,  # below karenz (2)
        )
        svc = StrengthenProcessor(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.promoted == 0
        assert result.incremented == 1
        storage.update_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fact_tag_needs_higher_composite(self) -> None:
        storage = AsyncMock()
        entry = _engram_row(
            "e10",
            layer="buffer",
            strength=0.5,  # below fact threshold (0.7)
            tags=["fact"],
            access_count=10,
            novelty=0.8,
            ncr_cycles_survived=3,
        )
        svc = StrengthenProcessor(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 1000})

        assert result.promoted == 0
        assert result.incremented == 1

    @pytest.mark.asyncio
    async def test_hard_gate_blocks_promotion(self) -> None:
        storage = AsyncMock()
        # Tiny bank → min_access=9, access_count=5 fails the gate
        entry = _engram_row(
            "e11",
            layer="buffer",
            strength=0.9,
            tags=["experience"],
            access_count=5,
            novelty=0.8,
            ncr_cycles_survived=3,
        )
        svc = StrengthenProcessor(pool=MagicMock(), storage_service=storage)

        result = await _run_service_with_batches(svc, [[entry]], {"engram_count": 50})

        assert result.promoted == 0
        assert result.incremented == 1
