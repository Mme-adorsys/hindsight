"""Unit tests for llm_routing — ModelTier enum and TASK_TIER_MAPPING."""

import pytest

from hindsight_api.engine.llm_routing import ModelTier, TASK_TIER_MAPPING, get_tier


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
