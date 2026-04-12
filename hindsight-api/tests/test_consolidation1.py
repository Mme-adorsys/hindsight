"""
Unit tests for Consolidation 1 — Session → Buffer.

Epic 12, Story 01 / Epic 24 (revised).

Tests cover:
- Gate 1: Novelty gate (low novelty → archive)
- Gate 2: Access gate (too few recalls → stay in WM)
- Score gate: Mode-dependent threshold
- ConsolidationResult dataclass defaults
- Consolidation1Service.run: happy path, batching, errors
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.consolidation1 import (
    Consolidation1Service,
    ConsolidationResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    engram_id: str,
    *,
    novelty: float = 0.7,
    emotional_valence: float = 0.6,
    surprise: float = 0.5,
    access_count: int = 10,
    created_at_op: int = 0,
    session_mode: str = "exploration",
    thalamus_overall: float = 0.6,
) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": "test-bank",
        "layer": None,
        "novelty": novelty,
        "emotional_valence": emotional_valence,
        "surprise": surprise,
        "thalamus_overall": thalamus_overall,
        "access_count": access_count,
        "created_at_op": created_at_op,
        "session_mode": session_mode,
        "strength": 0.0,
        "status": "active",
    }


def _make_service_and_run(
    entries_batches: list[list[dict]],
    bank_op_count: int = 20,
    update_side_effect: Exception | list | None = None,
):
    """Helper to build mocked service and return run coroutine components."""
    pool = MagicMock()
    storage = AsyncMock()

    if update_side_effect is not None:
        if isinstance(update_side_effect, Exception):
            storage.update_metadata.side_effect = update_side_effect
        else:
            storage.update_metadata.side_effect = update_side_effect

    return_values = entries_batches + [[]]

    mock_list = AsyncMock(side_effect=return_values)
    mock_op_count = AsyncMock(return_value=bank_op_count)

    return pool, storage, mock_list, mock_op_count


# ---------------------------------------------------------------------------
# ConsolidationResult
# ---------------------------------------------------------------------------


class TestConsolidationResult:
    def test_defaults(self):
        r = ConsolidationResult()
        assert r.total == 0
        assert r.consolidated == 0
        assert r.skipped == 0
        assert r.archived == 0
        assert r.errors == 0
        assert r.error_ids == []

    def test_error_ids_not_shared(self):
        r1 = ConsolidationResult()
        r2 = ConsolidationResult()
        r1.error_ids.append("x")
        assert r2.error_ids == []


# ---------------------------------------------------------------------------
# Gate 1: Novelty gate
# ---------------------------------------------------------------------------


class TestNoveltyGate:
    @pytest.mark.asyncio
    async def test_low_novelty_archived(self):
        """Engrams with novelty < 0.2 are archived regardless of other scores."""
        entry = _make_entry("aaa", novelty=0.1, access_count=20, emotional_valence=0.9)
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.archived == 1
        assert result.consolidated == 0
        updates = storage.update_metadata.call_args[0][1]
        assert updates["status"] == "archived"

    @pytest.mark.asyncio
    async def test_novelty_null_skips_gate(self):
        """Engrams with NULL novelty (observations) skip the novelty gate.

        Synthesized engrams from observation_regeneration have no thalamus
        scores — they're abstractions, not raw input. NULL novelty must be
        treated as "passes the gate", not as 0.0 (which would archive them).
        """
        entry = _make_entry("ccc", novelty=None, access_count=10, surprise=0.8)
        # Override _make_entry default to actually have None
        entry["novelty"] = None
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        # Must NOT be archived (NULL novelty bypasses gate)
        assert result.archived == 0

    @pytest.mark.asyncio
    async def test_novelty_at_threshold_not_archived(self):
        """Engrams at exactly 0.2 novelty pass the novelty gate."""
        entry = _make_entry("bbb", novelty=0.2, access_count=10)
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.archived == 0


# ---------------------------------------------------------------------------
# Gate 2: Access gate
# ---------------------------------------------------------------------------


class TestAccessGate:
    @pytest.mark.asyncio
    async def test_below_min_access_stays_in_wm(self):
        """Engrams with < 5 recalls stay in Working Memory."""
        entry = _make_entry("ccc", access_count=3, emotional_valence=0.9, surprise=0.9)
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.skipped == 1
        assert result.consolidated == 0
        updates = storage.update_metadata.call_args[0][1]
        assert "layer" not in updates  # not promoted
        assert "strength" in updates  # strength updated for tracking

    @pytest.mark.asyncio
    async def test_zero_access_stays_even_with_high_saliency(self):
        """Even highly salient engrams need recalls to be promoted."""
        entry = _make_entry("ddd", access_count=0, emotional_valence=1.0, surprise=1.0)
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.skipped == 1
        assert result.consolidated == 0

    @pytest.mark.asyncio
    async def test_exactly_5_access_passes_gate(self):
        """Engrams with exactly 5 recalls pass the access gate."""
        entry = _make_entry("eee", access_count=5, emotional_valence=0.8, session_mode="exploration")
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        # With exploration threshold (0.5) and high saliency, should promote
        assert result.consolidated == 1


# ---------------------------------------------------------------------------
# Score gate: mode-dependent thresholds
# ---------------------------------------------------------------------------


class TestScoreGate:
    @pytest.mark.asyncio
    async def test_high_saliency_promotes_in_exploration(self):
        """High saliency + 5 recalls → promoted in exploration mode (threshold 0.5)."""
        entry = _make_entry("fff", access_count=5, emotional_valence=0.8, surprise=0.7, session_mode="exploration")
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.consolidated == 1
        updates = storage.update_metadata.call_args[0][1]
        assert updates["layer"] == "buffer"

    @pytest.mark.asyncio
    async def test_same_score_skipped_in_precision(self):
        """Same engram that passes exploration may fail precision (threshold 0.8)."""
        # With access=5, cycles=20: recall_score ≈ 0.54, saliency=0.1
        # composite ≈ 0.54 + 0.03 = 0.57 < 0.8
        entry = _make_entry(
            "ggg",
            access_count=5,
            emotional_valence=0.1,
            surprise=0.1,
            session_mode="precision",
            created_at_op=0,
        )
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.skipped == 1
        assert result.consolidated == 0

    @pytest.mark.asyncio
    async def test_low_saliency_many_recalls_promoted(self):
        """Low saliency but 30 recalls → promoted even in precision mode."""
        entry = _make_entry("hhh", access_count=30, emotional_valence=0.1, surprise=0.1, session_mode="precision")
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.consolidated == 1

    @pytest.mark.asyncio
    async def test_none_mode_uses_default_threshold(self):
        """Engrams without session_mode use the default threshold (0.7)."""
        # access=30, low saliency → recall_score ≈ 1.38, composite ≈ 1.41 → > 0.7
        entry = _make_entry("iii", access_count=30, session_mode=None)
        # Override session_mode to None since _make_entry default is "exploration"
        entry["session_mode"] = None
        pool, storage, mock_list, mock_op = _make_service_and_run([[entry]])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.consolidated == 1


# ---------------------------------------------------------------------------
# Service: batching, errors, idempotency
# ---------------------------------------------------------------------------


class TestConsolidation1Service:
    @pytest.mark.asyncio
    async def test_single_batch_promoted(self):
        entries = [_make_entry("aaa"), _make_entry("bbb")]
        pool, storage, mock_list, mock_op = _make_service_and_run([entries])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 2
        assert result.consolidated == 2
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_empty_bank_returns_zero_result(self):
        pool, storage, mock_list, mock_op = _make_service_and_run([])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("empty-bank")

        assert result.total == 0
        assert result.consolidated == 0
        storage.update_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_in_one_entry_does_not_stop_others(self):
        entries = [_make_entry("eee"), _make_entry("fff")]
        pool, storage, mock_list, mock_op = _make_service_and_run(
            [entries], update_side_effect=[RuntimeError("DB down"), None]
        )

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 2
        assert result.errors == 1
        assert "eee" in result.error_ids

    @pytest.mark.asyncio
    async def test_multiple_batches_processed(self):
        batch1 = [_make_entry(f"id-{i}") for i in range(3)]
        batch2 = [_make_entry(f"id-{i}") for i in range(3, 5)]
        pool, storage, mock_list, mock_op = _make_service_and_run([batch1, batch2])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 5
        assert result.consolidated == 5

    @pytest.mark.asyncio
    async def test_mixed_outcomes(self):
        """One archived (low novelty), one skipped (low access), one promoted."""
        entries = [
            _make_entry("low-nov", novelty=0.05),  # → archived
            _make_entry("low-acc", access_count=2),  # → skipped
            _make_entry("good", access_count=10, emotional_valence=0.8),  # → promoted
        ]
        pool, storage, mock_list, mock_op = _make_service_and_run([entries])

        with (
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated", mock_list),
            patch("hindsight_api.engine.consolidation.consolidation1.dict_repo.get_bank_op_count", mock_op),
        ):
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 3
        assert result.archived == 1
        assert result.skipped == 1
        assert result.consolidated == 1
