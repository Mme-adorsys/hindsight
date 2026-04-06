"""
Unit tests for NCR Phase 1 — Decay.

Epic 12, Story 02, Task T5.

Tests cover:
- _apply_decay_formula: base decay, frequency bonus, extra decay, edge cases
- DecayResult dataclass defaults
- DecayProcessor.process: happy path, archiving, error isolation, batch processing
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.ncr_decay import (
    DecayConfig,
    DecayProcessor,
    DecayResult,
    _apply_decay_formula,
)


# ---------------------------------------------------------------------------
# _apply_decay_formula
# ---------------------------------------------------------------------------


class TestApplyDecayFormula:
    def test_zero_access_count_pure_decay(self):
        # frequency_bonus = 1.0 + log10(1)/10 = 1.0
        result = _apply_decay_formula(0.5, access_count=0, last_accessed=None)
        assert result == pytest.approx(0.5 * 0.9 * 1.0)

    def test_high_access_count_less_decay(self):
        # access_count=100: bonus = 1.0 + log10(101)/10 ≈ 1.0 + 2.004/10 = 1.2
        result = _apply_decay_formula(0.5, access_count=100, last_accessed=None)
        expected_bonus = 1.0 + math.log10(101) / 10
        assert result == pytest.approx(0.5 * 0.9 * expected_bonus)

    def test_frequency_bonus_example_100_approx_1_2(self):
        bonus = 1.0 + math.log10(1 + 100) / 10
        assert bonus == pytest.approx(1.2, abs=0.01)

    def test_no_extra_decay_within_30_days(self):
        recent = datetime.now(timezone.utc) - timedelta(days=15)
        result_with = _apply_decay_formula(0.5, 0, last_accessed=recent)
        result_without = _apply_decay_formula(0.5, 0, last_accessed=None)
        assert result_with == pytest.approx(result_without)

    def test_extra_decay_beyond_30_days(self):
        old = datetime.now(timezone.utc) - timedelta(days=60)
        result = _apply_decay_formula(0.5, 0, last_accessed=old)
        base = 0.5 * 0.9 * 1.0
        # Extra: 0.95^(60/30) = 0.95^2 = 0.9025
        expected = base * (0.95 ** (60 / 30))
        assert result == pytest.approx(expected, rel=1e-5)

    def test_extra_decay_exactly_30_days_no_extra(self):
        exactly_30 = datetime.now(timezone.utc) - timedelta(days=30)
        result_30 = _apply_decay_formula(0.5, 0, last_accessed=exactly_30)
        result_none = _apply_decay_formula(0.5, 0, last_accessed=None)
        # days=30 is NOT > 30, so no extra decay
        assert result_30 == pytest.approx(result_none)

    def test_custom_decay_rate(self):
        result = _apply_decay_formula(0.8, access_count=0, last_accessed=None, decay_rate=0.5)
        assert result == pytest.approx(0.8 * 0.5 * 1.0)

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.now() - timedelta(days=60)
        result = _apply_decay_formula(0.5, 0, last_accessed=naive)
        # Should not raise and should apply extra decay
        base = 0.5 * 0.9
        assert result < base  # extra decay applied

    def test_zero_strength_stays_zero(self):
        result = _apply_decay_formula(0.0, 0, last_accessed=None)
        assert result == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# DecayConfig + DecayResult
# ---------------------------------------------------------------------------


class TestDecayConfig:
    def test_defaults(self):
        c = DecayConfig()
        assert c.decay_rate == pytest.approx(0.9)
        assert c.archive_threshold == pytest.approx(0.05)

    def test_custom(self):
        c = DecayConfig(decay_rate=0.8, archive_threshold=0.1)
        assert c.decay_rate == pytest.approx(0.8)

    def test_frozen(self):
        c = DecayConfig()
        with pytest.raises(Exception):
            c.decay_rate = 0.5  # type: ignore[misc]


class TestDecayResult:
    def test_defaults(self):
        r = DecayResult()
        assert r.total == 0
        assert r.decayed == 0
        assert r.archived == 0
        assert r.unchanged == 0
        assert r.errors == 0
        assert r.error_ids == []

    def test_error_ids_not_shared(self):
        r1 = DecayResult()
        r2 = DecayResult()
        r1.error_ids.append("x")
        assert r2.error_ids == []


# ---------------------------------------------------------------------------
# DecayProcessor
# ---------------------------------------------------------------------------


def _make_entry(
    engram_id: str,
    strength: float = 0.3,
    access_count: int = 0,
    last_accessed: datetime | None = None,
    layer: str = "buffer",
) -> dict:
    return {
        "engram_id": engram_id,
        "bank_id": "test-bank",
        "layer": layer,
        "strength": strength,
        "access_count": access_count,
        "last_accessed": last_accessed,
        "status": "active",
    }


def _make_processor(
    batches: list[list[dict]],
    update_side_effect=None,
    qdrant_side_effect=None,
) -> tuple[DecayProcessor, AsyncMock, AsyncMock]:
    pool = MagicMock()
    storage = AsyncMock()
    qdrant = AsyncMock()

    if update_side_effect:
        storage.update_metadata.side_effect = update_side_effect
    if qdrant_side_effect:
        qdrant.set_payload_fields.side_effect = qdrant_side_effect

    return pool, storage, qdrant


class TestDecayProcessor:
    @pytest.mark.asyncio
    async def test_strength_decayed(self):
        entry = _make_entry("aaa", strength=0.5)
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.total == 1
        assert result.decayed == 1
        assert result.archived == 0
        call = storage.update_metadata.call_args
        assert call[0][1]["strength"] == pytest.approx(0.5 * 0.9 * 1.0, rel=1e-5)

    @pytest.mark.asyncio
    async def test_weak_engram_archived(self):
        entry = _make_entry("bbb", strength=0.04)  # below 0.05 threshold
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.archived == 1
        assert result.decayed == 0
        archive_call = storage.update_metadata.call_args
        assert archive_call[0][1] == {"layer": "archived", "status": "archived"}
        qdrant.set_payload_fields.assert_called_once_with("bbb", {"archived": True})

    @pytest.mark.asyncio
    async def test_qdrant_archive_failure_is_non_fatal(self):
        entry = _make_entry("ccc", strength=0.01)
        pool, storage, qdrant = _make_processor([])
        qdrant.set_payload_fields.side_effect = RuntimeError("Qdrant down")

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        # Archived in Dict/Neo4j despite Qdrant failure
        assert result.archived == 1
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_storage_error_counted(self):
        entry = _make_entry("ddd", strength=0.3)
        pool, storage, qdrant = _make_processor([])
        storage.update_metadata.side_effect = RuntimeError("DB error")

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.errors == 1
        assert "ddd" in result.error_ids
        assert result.decayed == 0

    @pytest.mark.asyncio
    async def test_error_does_not_stop_other_entries(self):
        entries = [
            _make_entry("eee", strength=0.3),
            _make_entry("fff", strength=0.3),
        ]
        pool, storage, qdrant = _make_processor([])
        storage.update_metadata.side_effect = [RuntimeError("fail"), None]

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [entries, []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.errors == 1
        assert result.decayed == 1

    @pytest.mark.asyncio
    async def test_multiple_batches(self):
        batch1 = [_make_entry(f"id-{i}", strength=0.4) for i in range(3)]
        batch2 = [_make_entry(f"id-{i}", strength=0.4) for i in range(3, 5)]
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [batch1, batch2, []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.total == 5
        assert result.decayed == 5

    @pytest.mark.asyncio
    async def test_empty_bank(self):
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = []
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("empty-bank")

        assert result.total == 0
        storage.update_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_config_archive_threshold(self):
        entry = _make_entry("ggg", strength=0.08)  # below custom threshold 0.1
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            config = DecayConfig(archive_threshold=0.1)
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant, config=config)
            result = await proc.process("test-bank")

        assert result.archived == 1

    @pytest.mark.asyncio
    async def test_neocortex_layer_also_processed(self):
        entry = _make_entry("hhh", strength=0.5, layer="neocortex")
        pool, storage, qdrant = _make_processor([])

        with patch(
            "hindsight_api.engine.consolidation.ncr_decay.dict_repo.list_active_for_decay",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.side_effect = [[entry], []]
            proc = DecayProcessor(pool=pool, storage_service=storage, qdrant=qdrant)
            result = await proc.process("test-bank")

        assert result.decayed == 1
