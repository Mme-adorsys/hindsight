"""Unit tests for llm_routing — ModelTier, PipelineStep, BudgetProfile, resolve_llm_config, LLMRegistry."""

import os
from unittest.mock import MagicMock

import pytest

from hindsight_api.engine.llm_routing import (
    HIGH_BUDGET,
    LOW_BUDGET,
    MID_BUDGET,
    BudgetProfile,
    LLMRegistry,
    ModelTier,
    PIPELINE_STEP_TASK_KEY,
    PROVIDER_TIER_MODELS,
    TASK_TIER_MAPPING,
    PipelineStep,
    get_tier,
    resolve_llm_config,
)


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
    def test_no_env_vars_unknown_provider_returns_operation_config(self):
        # lmstudio has no PROVIDER_TIER_MODELS entry → falls through to operation config unchanged
        op = _make_llm_config("lmstudio", "my-local-model")
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
        # Different subtask — env var for fact_extraction should not affect observation_synthesis
        # openai observation_synthesis → MEDIUM tier default = gpt-4o (same as op.model)
        result = resolve_llm_config("retain", "observation_synthesis", op, gl)
        assert result.model == "gpt-4o"
        assert result.provider == "openai"


class TestLLMRegistry:
    def test_get_llm_returns_tier_default_when_no_env(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        reflect_cfg = _make_llm_config("openai", "gpt-4o")
        global_cfg = _make_llm_config("openai", "gpt-4o-mini")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg, "reflect": reflect_cfg})
        # anthropic fact_extraction → LARGE tier → claude-opus-4-6
        assert registry.get_llm("retain", "fact_extraction").model == "claude-opus-4-6"
        assert registry.get_llm("retain", "fact_extraction").provider == "anthropic"
        # openai think → LARGE tier → gpt-4o
        assert registry.get_llm("reflect", "think").model == "gpt-4o"
        assert registry.get_llm("reflect", "think").provider == "openai"

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


class TestProviderTierModels:
    def test_anthropic_all_tiers_defined(self):
        assert ModelTier.SMALL in PROVIDER_TIER_MODELS["anthropic"]
        assert ModelTier.MEDIUM in PROVIDER_TIER_MODELS["anthropic"]
        assert ModelTier.LARGE in PROVIDER_TIER_MODELS["anthropic"]

    def test_openai_all_tiers_defined(self):
        assert ModelTier.SMALL in PROVIDER_TIER_MODELS["openai"]
        assert ModelTier.MEDIUM in PROVIDER_TIER_MODELS["openai"]
        assert ModelTier.LARGE in PROVIDER_TIER_MODELS["openai"]

    def test_anthropic_model_names(self):
        assert PROVIDER_TIER_MODELS["anthropic"][ModelTier.SMALL] == "claude-haiku-4-5-20251001"
        assert PROVIDER_TIER_MODELS["anthropic"][ModelTier.MEDIUM] == "claude-sonnet-4-6"
        assert PROVIDER_TIER_MODELS["anthropic"][ModelTier.LARGE] == "claude-opus-4-6"

    def test_openai_model_names(self):
        assert PROVIDER_TIER_MODELS["openai"][ModelTier.SMALL] == "gpt-4o-mini"
        assert PROVIDER_TIER_MODELS["openai"][ModelTier.MEDIUM] == "gpt-4o"

    def test_ollama_not_in_mapping(self):
        assert "ollama" not in PROVIDER_TIER_MODELS


class TestResolveLlmConfigWithTierDefaults:
    def test_anthropic_fact_extraction_gets_opus(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result.model == "claude-opus-4-6"
        assert result.provider == "anthropic"

    def test_anthropic_observation_synthesis_gets_sonnet(self):
        op = _make_llm_config("anthropic", "claude-opus-4-6")
        gl = _make_llm_config("anthropic", "claude-opus-4-6")
        result = resolve_llm_config("retain", "observation_synthesis", op, gl)
        assert result.model == "claude-sonnet-4-6"

    def test_anthropic_thalamus_scoring_gets_haiku(self):
        op = _make_llm_config("anthropic", "claude-opus-4-6")
        gl = _make_llm_config("anthropic", "claude-opus-4-6")
        result = resolve_llm_config("retain", "thalamus_scoring", op, gl)
        assert result.model == "claude-haiku-4-5-20251001"

    def test_openai_fact_extraction_gets_gpt4o(self):
        op = _make_llm_config("openai", "gpt-4o-mini")
        gl = _make_llm_config("openai", "gpt-4o-mini")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result.model == "gpt-4o"  # LARGE tier → gpt-4o (no stronger model)

    def test_unknown_provider_falls_back_to_operation_config(self):
        op = _make_llm_config("lmstudio", "my-local-model")
        gl = _make_llm_config("lmstudio", "my-local-model")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result is op  # no tier mapping for lmstudio → unchanged

    def test_subtask_env_var_overrides_tier_default(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_FACT_EXTRACTION_LLM_MODEL", "my-override-model")
        op = _make_llm_config("anthropic", "claude-sonnet-4-6", api_key="key")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        # Explicit env var wins over tier default (claude-opus-4-6)
        assert result.model == "my-override-model"

    def test_unknown_subtask_falls_back_to_operation_config(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "not_in_mapping", op, gl)
        assert result is op  # not in TASK_TIER_MAPPING → no tier default


class TestPipelineStep:
    def test_all_steps_are_strings(self):
        for step in PipelineStep:
            assert isinstance(step.value, str)

    def test_expected_steps_exist(self):
        assert PipelineStep.R0_SEQUENCE_ANALYSIS
        assert PipelineStep.R1_FACT_EXTRACTION
        assert PipelineStep.R4_ENTITY_DISAMBIGUATION
        assert PipelineStep.REFLECT
        assert PipelineStep.CONSTRUCTIVE_MEMORY
        assert PipelineStep.SCHEMA_COMPRESSION
        assert PipelineStep.OBSERVATION

    def test_pipeline_step_task_key_covers_all_steps(self):
        for step in PipelineStep:
            assert step in PIPELINE_STEP_TASK_KEY, f"{step} missing from PIPELINE_STEP_TASK_KEY"

    def test_task_keys_are_valid_format(self):
        for step, key in PIPELINE_STEP_TASK_KEY.items():
            assert "." in key, f"{step} → '{key}' is not in 'operation.subtask' format"


class TestBudgetProfile:
    def test_with_override_returns_new_instance(self):
        original = MID_BUDGET
        modified = original.with_override(PipelineStep.REFLECT, ModelTier.LARGE)
        assert modified is not original

    def test_with_override_does_not_mutate_original(self):
        original_tier = MID_BUDGET.get_tier(PipelineStep.REFLECT)
        MID_BUDGET.with_override(PipelineStep.REFLECT, ModelTier.LARGE)
        assert MID_BUDGET.get_tier(PipelineStep.REFLECT) == original_tier

    def test_with_override_applies_new_tier(self):
        profile = LOW_BUDGET.with_override(PipelineStep.REFLECT, ModelTier.LARGE)
        assert profile.get_tier(PipelineStep.REFLECT) == ModelTier.LARGE

    def test_get_tier_returns_medium_for_unknown_step(self):
        empty = BudgetProfile(steps={})
        assert empty.get_tier(PipelineStep.REFLECT) == ModelTier.MEDIUM

    def test_frozen_prevents_reassignment(self):
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            LOW_BUDGET.steps = {}  # type: ignore[misc]

    def test_all_predefined_profiles_cover_all_steps(self):
        for profile, name in [(LOW_BUDGET, "LOW"), (MID_BUDGET, "MID"), (HIGH_BUDGET, "HIGH")]:
            for step in PipelineStep:
                assert step in profile.steps, f"{name}_BUDGET missing step {step}"

    def test_all_predefined_profile_values_are_model_tier(self):
        for profile in (LOW_BUDGET, MID_BUDGET, HIGH_BUDGET):
            for step, tier in profile.steps.items():
                assert isinstance(tier, ModelTier), f"step {step} has non-ModelTier value: {tier}"


class TestResolveLlmConfigWithBudgetProfile:
    def test_low_budget_r0_uses_small_tier(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "sequence_analysis", op, gl, budget_profile=LOW_BUDGET)
        assert result.model == "claude-haiku-4-5-20251001"  # SMALL tier

    def test_high_budget_r0_uses_large_tier(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "sequence_analysis", op, gl, budget_profile=HIGH_BUDGET)
        assert result.model == "claude-opus-4-6"  # LARGE tier

    def test_mid_budget_r1_fact_extraction_uses_small_tier(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "fact_extraction", op, gl, budget_profile=MID_BUDGET)
        assert result.model == "claude-haiku-4-5-20251001"  # SMALL per MID_BUDGET R1

    def test_high_budget_reflect_uses_large_tier(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("reflect", "think", op, gl, budget_profile=HIGH_BUDGET)
        assert result.model == "claude-opus-4-6"  # LARGE tier

    def test_env_var_overrides_budget_profile(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_RETAIN_SEQUENCE_ANALYSIS_LLM_MODEL", "my-custom-model")
        op = _make_llm_config("anthropic", "claude-sonnet-4-6", api_key="key")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("retain", "sequence_analysis", op, gl, budget_profile=HIGH_BUDGET)
        assert result.model == "my-custom-model"  # env var wins over budget profile

    def test_no_profile_backward_compat_uses_task_tier_mapping(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        # Without profile, fact_extraction uses TASK_TIER_MAPPING → LARGE → opus
        result = resolve_llm_config("retain", "fact_extraction", op, gl)
        assert result.model == "claude-opus-4-6"

    def test_budget_profile_with_unknown_provider_falls_back_to_operation_config(self):
        op = _make_llm_config("lmstudio", "my-local-model")
        gl = _make_llm_config("lmstudio", "my-local-model")
        result = resolve_llm_config("retain", "sequence_analysis", op, gl, budget_profile=HIGH_BUDGET)
        assert result is op  # lmstudio not in PROVIDER_TIER_MODELS → unchanged

    def test_budget_profile_with_step_not_in_mapping_falls_back_to_task_tier(self):
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        # thalamus_scoring is in TASK_TIER_MAPPING but not in PIPELINE_STEP_TASK_KEY
        result = resolve_llm_config("retain", "thalamus_scoring", op, gl, budget_profile=MID_BUDGET)
        assert result.model == "claude-haiku-4-5-20251001"  # SMALL from TASK_TIER_MAPPING

    def test_with_override_profile_uses_overridden_tier(self):
        profile = LOW_BUDGET.with_override(PipelineStep.REFLECT, ModelTier.LARGE)
        op = _make_llm_config("anthropic", "claude-sonnet-4-6")
        gl = _make_llm_config("anthropic", "claude-sonnet-4-6")
        result = resolve_llm_config("reflect", "think", op, gl, budget_profile=profile)
        assert result.model == "claude-opus-4-6"  # overridden to LARGE despite LOW_BUDGET base


class TestLLMRegistryResolveWithProfile:
    def test_resolve_with_profile_low_r0_returns_small(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        global_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        result = registry.resolve_with_profile("retain", "sequence_analysis", LOW_BUDGET)
        assert result.model == "claude-haiku-4-5-20251001"  # SMALL

    def test_resolve_with_profile_high_r0_returns_large(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        global_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        result = registry.resolve_with_profile("retain", "sequence_analysis", HIGH_BUDGET)
        assert result.model == "claude-opus-4-6"  # LARGE

    def test_resolve_with_profile_not_cached(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        global_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        registry.resolve_with_profile("retain", "sequence_analysis", LOW_BUDGET)
        # resolve_with_profile must NOT pollute the standard cache
        assert "retain.sequence_analysis" not in registry._cache

    def test_resolve_with_profile_override_beats_profile(self):
        retain_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6", api_key="key")
        global_cfg = _make_llm_config("anthropic", "claude-sonnet-4-6")
        registry = LLMRegistry(global_cfg, {"retain": retain_cfg})
        # Env var should still override even when budget_profile is provided
        import os

        os.environ["HINDSIGHT_API_RETAIN_SEQUENCE_ANALYSIS_LLM_MODEL"] = "custom-override"
        try:
            result = registry.resolve_with_profile("retain", "sequence_analysis", HIGH_BUDGET)
            assert result.model == "custom-override"
        finally:
            del os.environ["HINDSIGHT_API_RETAIN_SEQUENCE_ANALYSIS_LLM_MODEL"]
