"""Unit tests for llm_routing — ModelTier enum, TASK_TIER_MAPPING, resolve_llm_config, LLMRegistry."""

import os
from unittest.mock import MagicMock

import pytest

from hindsight_api.engine.llm_routing import LLMRegistry, ModelTier, TASK_TIER_MAPPING, get_tier, resolve_llm_config


def _make_llm_config(provider: str = "openai", model: str = "gpt-4o", api_key: str = "test-key") -> MagicMock:
    """Create a lightweight LLMConfig-like mock for testing routing logic."""
    cfg = MagicMock()
    cfg.provider = provider
    cfg.model = model
    cfg.api_key = api_key
    cfg.base_url = ""
    return cfg


class TestModelTier:
    def test_enum_values_are_strings(self):
        assert ModelTier.SMALL == "small"
        assert ModelTier.MEDIUM == "medium"
        assert ModelTier.LARGE == "large"

    def test_all_three_tiers_exist(self):
        assert {ModelTier.SMALL, ModelTier.MEDIUM, ModelTier.LARGE} == set(ModelTier)


class TestTaskTierMapping:
    def test_retain_fact_extraction_is_large(self):
        assert TASK_TIER_MAPPING["retain.fact_extraction"] == ModelTier.LARGE

    def test_retain_observation_synthesis_is_medium(self):
        assert TASK_TIER_MAPPING["retain.observation_synthesis"] == ModelTier.MEDIUM

    def test_retain_thalamus_scoring_is_small(self):
        assert TASK_TIER_MAPPING["retain.thalamus_scoring"] == ModelTier.SMALL

    def test_retain_schema_fit_check_is_medium(self):
        assert TASK_TIER_MAPPING["retain.schema_fit_check"] == ModelTier.MEDIUM

    def test_retain_conflict_resolution_is_large(self):
        assert TASK_TIER_MAPPING["retain.conflict_resolution"] == ModelTier.LARGE

    def test_reflect_think_is_large(self):
        assert TASK_TIER_MAPPING["reflect.think"] == ModelTier.LARGE

    def test_reflect_opinion_extraction_is_medium(self):
        assert TASK_TIER_MAPPING["reflect.opinion_extraction"] == ModelTier.MEDIUM

    def test_reflect_constructive_memory_inference_is_large(self):
        assert TASK_TIER_MAPPING["reflect.constructive_memory_inference"] == ModelTier.LARGE

    def test_all_values_are_model_tier(self):
        for key, tier in TASK_TIER_MAPPING.items():
            assert isinstance(tier, ModelTier), f"{key} has non-ModelTier value: {tier}"


class TestGetTier:
    def test_known_key_returns_tier(self):
        assert get_tier("retain.fact_extraction") == ModelTier.LARGE
        assert get_tier("reflect.think") == ModelTier.LARGE
        assert get_tier("retain.thalamus_scoring") == ModelTier.SMALL

    def test_unknown_key_raises_key_error(self):
        with pytest.raises(KeyError):
            get_tier("unknown.subtask")

    def test_empty_key_raises_key_error(self):
        with pytest.raises(KeyError):
            get_tier("")


class TestResolveLlmConfig:
    def test_no_env_vars_returns_operation_config(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("openai", "gpt-4o")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result is op

    def test_subtask_model_env_var_overrides_model(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_FACT_EXTRACTION_LLM_MODEL", "claude-opus-4-6")
        op = _make_llm_config("anthropic", "claude-sonnet-4-6", api_key="key-op")
        gl = _make_llm_config("openai", "gpt-4o")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result.model == "claude-opus-4-6"
        assert result.provider == "anthropic"  # inherited from operation config
        assert result.api_key == "key-op"  # inherited from operation config

    def test_subtask_provider_env_var_overrides_provider(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_REFLECT_THINK_LLM_PROVIDER", "anthropic")
        op = _make_llm_config("openai", "gpt-4o")
        gl = _make_llm_config("openai", "gpt-4o-mini")
        result = resolve_llm_config("reflect", "think", op, gl)
        assert result.provider == "anthropic"
        assert result.model == "gpt-4o"  # inherited from operation config

    def test_both_subtask_env_vars_set(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_OBSERVATION_SYNTHESIS_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_OBSERVATION_SYNTHESIS_LLM_MODEL", "claude-haiku-4-5-20251001")
        op = _make_llm_config("openai", "gpt-4o")
        gl = _make_llm_config("openai", "gpt-4o-mini")
        result = resolve_llm_config("retain", "observation_synthesis", op, gl)
        assert result.provider == "anthropic"
        assert result.model == "claude-haiku-4-5-20251001"

    def test_unrelated_env_var_not_matched(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_FACT_EXTRACTION_LLM_MODEL", "claude-opus-4-6")
        op = _make_llm_config("openai", "gpt-4o")
        gl = _make_llm_config("openai", "gpt-4o-mini")
        # Different subtask — should not be affected
        result = resolve_llm_config("retain", "observation_synthesis", op, gl)
        assert result is op


class TestLLMRegistry:
    def test_get_llm_returns_operation_config_when_no_env(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        reflect_cfg = _make_llm_config("openai", "gpt-4o")
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg, "reflect": reflect_cfg})
        assert registry.get_llm("retain", "fact_extraction") is retain_cfg
        assert registry.get_llm("reflect", "think") is reflect_cfg

    def test_get_llm_falls_back_to_global_for_unknown_operation(self):
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {})
        assert registry.get_llm("unknown_op", "some_subtask") is global_cfg

    def test_get_llm_result_is_cached(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        first = registry.get_llm("retain", "fact_extraction")
        second = registry.get_llm("retain", "fact_extraction")
        assert first is second  # same object — cache hit

    def test_different_subtasks_get_different_cache_entries(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        # Prime the cache
        registry.get_llm("retain", "fact_extraction")
        registry.get_llm("retain", "observation_synthesis")
        assert len(registry._cache) == 2

    def test_subtask_env_var_override_is_respected(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_FACT_EXTRACTION_LLM_MODEL", "claude-opus-4-6")
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6", api_key="key")
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        result = registry.get_llm("retain", "fact_extraction")
        assert result.model == "claude-opus-4-6"
        assert result.provider == "anthropic"  # inherited from retain config
