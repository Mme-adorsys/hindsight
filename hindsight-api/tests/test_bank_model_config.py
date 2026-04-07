"""Unit tests for BankModelConfigRepo — cache, CRUD, override chain, validation."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api.engine.bank_model_config import (
    PROFILE_MAP,
    BankModelConfigRepo,
    _apply_overrides,
    _validate_overrides,
)
from hindsight_api.engine.llm_routing import HIGH_BUDGET, LOW_BUDGET, MID_BUDGET, ModelTier, PipelineStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(row: dict | None = None) -> AsyncMock:
    """Return an asyncpg-like mock connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=_dict_to_record(row) if row is not None else None)
    conn.execute = AsyncMock(return_value=None)
    return conn


def _dict_to_record(d: dict):
    """Simulate an asyncpg Record that supports dict-like access."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: d[key]
    record.keys = lambda self: d.keys()
    # Support `dict(record)` pattern
    record.__iter__ = lambda self: iter(d)
    return record


# ---------------------------------------------------------------------------
# _validate_overrides
# ---------------------------------------------------------------------------


class TestValidateOverrides:
    def test_valid_overrides_pass(self):
        _validate_overrides({"r0_sequence_analysis": "small", "reflect": "large"})

    def test_invalid_step_key_raises(self):
        with pytest.raises(ValueError, match="Invalid pipeline step"):
            _validate_overrides({"not_a_step": "small"})

    def test_invalid_tier_value_raises(self):
        with pytest.raises(ValueError, match="Invalid model tier"):
            _validate_overrides({"r0_sequence_analysis": "not_a_tier"})

    def test_empty_dict_is_valid(self):
        _validate_overrides({})


# ---------------------------------------------------------------------------
# _apply_overrides
# ---------------------------------------------------------------------------


class TestApplyOverrides:
    def test_no_overrides_returns_base(self):
        result = _apply_overrides(MID_BUDGET, {})
        assert result is MID_BUDGET

    def test_valid_override_applied(self):
        result = _apply_overrides(LOW_BUDGET, {"reflect": "large"})
        assert result.get_tier(PipelineStep.REFLECT) == ModelTier.LARGE
        # Other steps unchanged
        assert result.get_tier(PipelineStep.R0_SEQUENCE_ANALYSIS) == ModelTier.SMALL

    def test_invalid_override_skipped_with_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="hindsight_api.engine.bank_model_config"):
            result = _apply_overrides(MID_BUDGET, {"bad_step": "small"})
        assert result is MID_BUDGET  # unchanged


# ---------------------------------------------------------------------------
# BankModelConfigRepo.get_profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_no_row_returns_mid_budget(self):
        conn = _make_conn(row=None)
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_profile(conn, "bank1"))
        assert result is MID_BUDGET

    def test_low_profile_returned(self):
        conn = _make_conn(row={"budget_profile": "low", "step_overrides": {}})
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_profile(conn, "bank1"))
        assert result.get_tier(PipelineStep.R0_SEQUENCE_ANALYSIS) == ModelTier.SMALL

    def test_high_profile_returned(self):
        conn = _make_conn(row={"budget_profile": "high", "step_overrides": {}})
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_profile(conn, "bank1"))
        assert result.get_tier(PipelineStep.R0_SEQUENCE_ANALYSIS) == ModelTier.LARGE

    def test_step_override_applied(self):
        conn = _make_conn(row={"budget_profile": "mid", "step_overrides": {"reflect": "large"}})
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_profile(conn, "bank1"))
        assert result.get_tier(PipelineStep.REFLECT) == ModelTier.LARGE
        assert result.get_tier(PipelineStep.R0_SEQUENCE_ANALYSIS) == ModelTier.MEDIUM  # unchanged

    def test_result_cached_on_second_call(self):
        conn = _make_conn(row={"budget_profile": "mid", "step_overrides": {}})
        repo = BankModelConfigRepo()
        asyncio.run(repo.get_profile(conn, "bank1"))
        asyncio.run(repo.get_profile(conn, "bank1"))
        # fetchrow called only once (second call is a cache hit)
        assert conn.fetchrow.call_count == 1

    def test_unknown_profile_falls_back_to_mid(self):
        conn = _make_conn(row={"budget_profile": "ultra", "step_overrides": {}})
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_profile(conn, "bank1"))
        assert result is MID_BUDGET


# ---------------------------------------------------------------------------
# BankModelConfigRepo.set_config
# ---------------------------------------------------------------------------


class TestSetConfig:
    def test_set_config_upserts_new_bank(self):
        conn = _make_conn(row=None)  # no existing row
        repo = BankModelConfigRepo()
        asyncio.run(repo.set_config(conn, "bank1", budget_profile="low"))
        assert conn.execute.called
        call_args = conn.execute.call_args
        assert "bank1" in call_args.args
        assert "low" in call_args.args

    def test_set_config_invalid_profile_raises(self):
        conn = _make_conn(row=None)
        repo = BankModelConfigRepo()
        with pytest.raises(ValueError, match="Invalid budget_profile"):
            asyncio.run(repo.set_config(conn, "bank1", budget_profile="ultra"))

    def test_set_config_invalid_override_raises(self):
        conn = _make_conn(row=None)
        repo = BankModelConfigRepo()
        with pytest.raises(ValueError, match="Invalid pipeline step"):
            asyncio.run(repo.set_config(conn, "bank1", step_overrides={"bad_step": "small"}))

    def test_set_config_invalidates_cache(self):
        conn = _make_conn(row={"budget_profile": "mid", "step_overrides": {}})
        repo = BankModelConfigRepo()
        # Populate cache
        asyncio.run(repo.get_profile(conn, "bank1"))
        assert "bank1" in repo._cache
        # set_config should evict it
        asyncio.run(repo.set_config(conn, "bank1", budget_profile="low"))
        assert "bank1" not in repo._cache

    def test_set_config_merges_existing_overrides(self):
        existing = {"budget_profile": "mid", "step_overrides": {"reflect": "large"}}
        conn = _make_conn(row=existing)
        repo = BankModelConfigRepo()
        asyncio.run(repo.set_config(conn, "bank1", step_overrides={"r0_sequence_analysis": "small"}))
        call_args = conn.execute.call_args
        # The JSONB arg (4th positional) should contain both keys
        jsonb_arg = call_args.args[3]
        merged = json.loads(jsonb_arg)
        assert "reflect" in merged
        assert "r0_sequence_analysis" in merged


# ---------------------------------------------------------------------------
# BankModelConfigRepo.get_config_row
# ---------------------------------------------------------------------------


class TestGetConfigRow:
    def test_no_row_returns_mid_defaults(self):
        conn = _make_conn(row=None)
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_config_row(conn, "bank1"))
        assert result["budget_profile"] == "mid"
        assert result["step_overrides"] == {}
        assert "effective_tiers" in result
        assert len(result["effective_tiers"]) == len(list(PipelineStep))

    def test_effective_tiers_reflect_overrides(self):
        conn = _make_conn(row={"budget_profile": "low", "step_overrides": {"reflect": "large"}})
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_config_row(conn, "bank1"))
        assert result["effective_tiers"]["reflect"] == "large"
        assert result["effective_tiers"]["r0_sequence_analysis"] == "small"

    def test_effective_tiers_keys_are_step_values(self):
        conn = _make_conn(row=None)
        repo = BankModelConfigRepo()
        result = asyncio.run(repo.get_config_row(conn, "bank1"))
        expected_keys = {s.value for s in PipelineStep}
        assert set(result["effective_tiers"].keys()) == expected_keys


# ---------------------------------------------------------------------------
# PROFILE_MAP completeness
# ---------------------------------------------------------------------------


class TestProfileMap:
    def test_all_three_profiles_present(self):
        assert "low" in PROFILE_MAP
        assert "mid" in PROFILE_MAP
        assert "high" in PROFILE_MAP

    def test_profile_map_values_are_budget_profiles(self):
        from hindsight_api.engine.llm_routing import BudgetProfile

        for name, profile in PROFILE_MAP.items():
            assert isinstance(profile, BudgetProfile), f"{name} is not a BudgetProfile"
