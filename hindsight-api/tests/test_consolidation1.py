"""
Unit tests for Consolidation 1 — Session → Buffer.

Epic 12, Story 01, Task T5.

Tests cover:
- Strength computation (_compute_initial_strength)
- ConsolidationResult dataclass defaults
- Consolidation1Service.run: happy path, idempotency, batch processing, errors
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.consolidation1 import (
    ConsolidationResult,
    Consolidation1Service,
    _compute_initial_strength,
)


# ---------------------------------------------------------------------------
# _compute_initial_strength
# ---------------------------------------------------------------------------


class TestComputeInitialStrength:
    def test_none_returns_base(self):
        assert _compute_initial_strength(None) == pytest.approx(0.1)

    def test_zero_thalamus_returns_base(self):
        assert _compute_initial_strength(0.0) == pytest.approx(0.1)

    def test_full_thalamus_capped_at_buffer_max(self):
        # 1.0 * 0.5 + 0.1 = 0.6 → capped at 0.5
        assert _compute_initial_strength(1.0) == pytest.approx(0.5)

    def test_mid_thalamus(self):
        # 0.6 * 0.5 + 0.1 = 0.4
        assert _compute_initial_strength(0.6) == pytest.approx(0.4)

    def test_low_thalamus_below_cap(self):
        # 0.4 * 0.5 + 0.1 = 0.3
        assert _compute_initial_strength(0.4) == pytest.approx(0.3)

    def test_cap_applied_for_very_high_thalamus(self):
        # 2.0 * 0.5 + 0.1 = 1.1 → capped at 0.5
        assert _compute_initial_strength(2.0) == pytest.approx(0.5)

    def test_negative_thalamus_clamps_to_base(self):
        # -0.5 * 0.5 + 0.1 = -0.15 → but cap only applies at top
        # result: -0.15 (no lower bound in formula — test documents behavior)
        result = _compute_initial_strength(-0.5)
        assert result < 0.1  # below base, no lower clamp in formula


# ---------------------------------------------------------------------------
# ConsolidationResult
# ---------------------------------------------------------------------------


class TestConsolidationResult:
    def test_defaults(self):
        r = ConsolidationResult()
        assert r.total == 0
        assert r.consolidated == 0
        assert r.skipped == 0
        assert r.errors == 0
        assert r.error_ids == []

    def test_error_ids_not_shared(self):
        r1 = ConsolidationResult()
        r2 = ConsolidationResult()
        r1.error_ids.append("x")
        assert r2.error_ids == []


# ---------------------------------------------------------------------------
# Consolidation1Service
# ---------------------------------------------------------------------------


def _make_entry(engram_id: str, thalamus_overall: float | None = 0.6) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": "test-bank",
        "layer": None,
        "thalamus_overall": thalamus_overall,
        "strength": 0.0,
        "status": "active",
    }


def _make_service(
    unconsolidated_batches: list[list[dict]],
    update_raises: Exception | None = None,
) -> tuple[Consolidation1Service, AsyncMock]:
    """Build a service with mocked pool and storage."""
    pool = MagicMock()
    storage = AsyncMock()

    if update_raises:
        storage.update_metadata.side_effect = update_raises

    # Patch list_unconsolidated to return batches in sequence, then empty
    return_values = unconsolidated_batches + [[]]

    with patch(
        "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
        new_callable=AsyncMock,
    ) as mock_list:
        mock_list.side_effect = return_values
        svc = Consolidation1Service(pool=pool, storage_service=storage)
        return svc, storage, mock_list


class TestConsolidation1Service:
    @pytest.mark.asyncio
    async def test_single_batch_consolidated(self):
        entries = [_make_entry("aaa"), _make_entry("bbb")]
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [entries, []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 2
        assert result.consolidated == 2
        assert result.errors == 0
        assert storage.update_metadata.call_count == 2

    @pytest.mark.asyncio
    async def test_layer_and_strength_passed_correctly(self):
        entry = _make_entry("ccc", thalamus_overall=0.6)
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            await svc.run("test-bank")

        call_args = storage.update_metadata.call_args
        assert call_args[0][0] == "ccc"
        updates = call_args[0][1]
        assert updates["layer"] == "buffer"
        assert updates["strength"] == pytest.approx(0.4)  # 0.6*0.5+0.1

    @pytest.mark.asyncio
    async def test_no_thalamus_uses_base_strength(self):
        entry = _make_entry("ddd", thalamus_overall=None)
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            await svc.run("test-bank")

        updates = storage.update_metadata.call_args[0][1]
        assert updates["strength"] == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_empty_bank_returns_zero_result(self):
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = []
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("empty-bank")

        assert result.total == 0
        assert result.consolidated == 0
        storage.update_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_in_one_entry_does_not_stop_others(self):
        entries = [_make_entry("eee"), _make_entry("fff")]
        pool = MagicMock()
        storage = AsyncMock()
        storage.update_metadata.side_effect = [RuntimeError("DB down"), None]

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [entries, []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 2
        assert result.consolidated == 1
        assert result.errors == 1
        assert "eee" in result.error_ids

    @pytest.mark.asyncio
    async def test_multiple_batches_processed(self):
        batch1 = [_make_entry(f"id-{i}") for i in range(3)]
        batch2 = [_make_entry(f"id-{i}") for i in range(3, 5)]
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [batch1, batch2, []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            result = await svc.run("test-bank")

        assert result.total == 5
        assert result.consolidated == 5
        assert storage.update_metadata.call_count == 5

    @pytest.mark.asyncio
    async def test_idempotency_via_list_unconsolidated_filter(self):
        """
        Idempotency is guaranteed by list_unconsolidated filtering on layer IS NULL.
        After consolidation, entries have layer='buffer' and are excluded.
        Simulate by returning empty on second run.
        """
        entries = [_make_entry("ggg")]
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            # First run: one entry pending
            mock_list.side_effect = [entries, [], [], []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)

            result1 = await svc.run("test-bank")
            result2 = await svc.run("test-bank")

        assert result1.consolidated == 1
        assert result2.consolidated == 0  # Nothing left to consolidate

    @pytest.mark.asyncio
    async def test_strength_capped_at_buffer_max(self):
        entry = _make_entry("hhh", thalamus_overall=1.0)
        pool = MagicMock()
        storage = AsyncMock()

        with patch(
            "hindsight_api.engine.consolidation.consolidation1.dict_repo.list_unconsolidated",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            svc = Consolidation1Service(pool=pool, storage_service=storage)
            await svc.run("test-bank")

        updates = storage.update_metadata.call_args[0][1]
        assert updates["strength"] <= 0.5
