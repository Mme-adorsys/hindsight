"""
Unit tests for NCR Phase 2 — Strengthen.

Epic 12, Story 03, Task T5.

Tests cover:
- StrengthenConfig defaults and frozen
- StrengthenResult defaults
- Promotion criteria (all three thresholds)
- Promotion: layer='neocortex', strength+0.1, promoted_at set
- ncr_cycles_survived increment for non-promoted entries
- Error isolation
- Batch processing
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from hindsight_api.engine.consolidation.ncr_strengthen import (
    StrengthenConfig,
    StrengthenProcessor,
    StrengthenResult,
    _PROMOTION_STRENGTH_BOOST,
    _STRENGTH_MAX,
)


# ---------------------------------------------------------------------------
# StrengthenConfig
# ---------------------------------------------------------------------------


class TestStrengthenConfig:
    def test_defaults(self):
        c = StrengthenConfig()
        assert c.promotion_strength_threshold == pytest.approx(0.4)
        assert c.promotion_access_threshold == 3
        assert c.promotion_ncr_cycles == 2

    def test_frozen(self):
        c = StrengthenConfig()
        with pytest.raises(Exception):
            c.promotion_strength_threshold = 0.1  # type: ignore[misc]

    def test_custom(self):
        c = StrengthenConfig(promotion_strength_threshold=0.6, promotion_access_threshold=5)
        assert c.promotion_strength_threshold == pytest.approx(0.6)
        assert c.promotion_access_threshold == 5


# ---------------------------------------------------------------------------
# StrengthenResult
# ---------------------------------------------------------------------------


class TestStrengthenResult:
    def test_defaults(self):
        r = StrengthenResult()
        assert r.total == 0
        assert r.promoted == 0
        assert r.incremented == 0
        assert r.errors == 0
        assert r.error_ids == []

    def test_error_ids_not_shared(self):
        r1 = StrengthenResult()
        r2 = StrengthenResult()
        r1.error_ids.append("x")
        assert r2.error_ids == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    engram_id: str,
    strength: float = 0.5,
    access_count: int = 5,
    ncr_cycles_survived: int = 3,
    layer: str = "buffer",
) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": "test-bank",
        "layer": layer,
        "strength": strength,
        "access_count": access_count,
        "ncr_cycles_survived": ncr_cycles_survived,
        "status": "active",
    }


# ---------------------------------------------------------------------------
# StrengthenProcessor
# ---------------------------------------------------------------------------


class TestStrengthenProcessor:
    @pytest.mark.asyncio
    async def test_eligible_entry_promoted(self):
        entry = _make_entry("aaa", strength=0.5, access_count=5, ncr_cycles_survived=3)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ) as mock_inc,
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.promoted == 1
        assert result.incremented == 0
        mock_inc.assert_not_called()
        call_args = storage.update_metadata.call_args
        assert call_args[0][0] == "aaa"
        updates = call_args[0][1]
        assert updates["layer"] == "neocortex"
        assert updates["strength"] == pytest.approx(0.5 + _PROMOTION_STRENGTH_BOOST)

    @pytest.mark.asyncio
    async def test_promotion_strength_boost_capped(self):
        entry = _make_entry("bbb", strength=0.95, access_count=10, ncr_cycles_survived=5)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ),
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            await proc.process("test-bank")

        updates = storage.update_metadata.call_args[0][1]
        assert updates["strength"] <= _STRENGTH_MAX

    @pytest.mark.asyncio
    async def test_promoted_at_is_set(self):
        entry = _make_entry("ccc", strength=0.5, access_count=5, ncr_cycles_survived=3)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ),
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            await proc.process("test-bank")

        updates = storage.update_metadata.call_args[0][1]
        assert "promoted_at" in updates
        assert updates["promoted_at"] is not None

    @pytest.mark.asyncio
    async def test_low_strength_not_promoted(self):
        entry = _make_entry("ddd", strength=0.3, access_count=5, ncr_cycles_survived=3)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ) as mock_inc,
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.promoted == 0
        assert result.incremented == 1
        mock_inc.assert_called_once()
        storage.update_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_access_count_not_promoted(self):
        entry = _make_entry("eee", strength=0.5, access_count=1, ncr_cycles_survived=3)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ) as mock_inc,
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.promoted == 0
        assert result.incremented == 1
        mock_inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_insufficient_ncr_cycles_not_promoted(self):
        entry = _make_entry("fff", strength=0.5, access_count=5, ncr_cycles_survived=1)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ) as mock_inc,
        ):
            mock_list.side_effect = [[entry], []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.promoted == 0
        assert result.incremented == 1
        mock_inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_does_not_stop_others(self):
        entries = [
            _make_entry("ggg", strength=0.5, access_count=5, ncr_cycles_survived=3),
            _make_entry("hhh", strength=0.5, access_count=5, ncr_cycles_survived=3),
        ]
        pool = MagicMock()
        storage = AsyncMock()
        storage.update_metadata.side_effect = [RuntimeError("fail"), None]

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ),
        ):
            mock_list.side_effect = [entries, []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.errors == 1
        assert result.promoted == 1
        assert "ggg" in result.error_ids

    @pytest.mark.asyncio
    async def test_empty_bank(self):
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ),
        ):
            mock_list.return_value = []
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("empty-bank")

        assert result.total == 0
        storage.update_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_batches(self):
        batch1 = [_make_entry(f"id-{i}", strength=0.5, access_count=5, ncr_cycles_survived=3) for i in range(3)]
        batch2 = [_make_entry(f"id-{i}", strength=0.2, access_count=1, ncr_cycles_survived=0) for i in range(3, 5)]
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ) as mock_inc,
        ):
            mock_list.side_effect = [batch1, batch2, []]
            proc = StrengthenProcessor(pool=pool, storage_service=storage)
            result = await proc.process("test-bank")

        assert result.total == 5
        assert result.promoted == 3  # batch1 all meet criteria
        assert result.incremented == 2  # batch2 all below threshold
        assert mock_inc.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_config_thresholds(self):
        # Lower thresholds — entry that normally wouldn't qualify should now qualify
        entry = _make_entry("iii", strength=0.2, access_count=1, ncr_cycles_survived=1)
        pool = MagicMock()
        storage = AsyncMock()

        with (
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.list_buffer_for_strengthen",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "hindsight_api.engine.consolidation.ncr_strengthen.dict_repo.increment_ncr_cycles",
                new_callable=AsyncMock,
            ),
        ):
            mock_list.side_effect = [[entry], []]
            config = StrengthenConfig(
                promotion_strength_threshold=0.2,
                promotion_access_threshold=1,
                promotion_ncr_cycles=1,
            )
            proc = StrengthenProcessor(pool=pool, storage_service=storage, config=config)
            result = await proc.process("test-bank")

        assert result.promoted == 1
