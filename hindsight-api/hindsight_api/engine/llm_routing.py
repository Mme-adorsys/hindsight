"""
LLM Routing — Task-to-Model-Tier Mapping.

Philosophy
----------
Not every LLM subtask warrants the same model. Matching task complexity to model
capability reduces cost without sacrificing quality where it matters.

Three tiers:
  SMALL   — Simple yes/no checks, lightweight scoring. Haiku / GPT-4o-mini.
  MEDIUM  — Structured extraction, entity resolution, scoring. Sonnet / GPT-4o.
  LARGE   — Complex reasoning, causal inference, conflict resolution. Opus.

Routing is rule-based, not dynamic. Each task key maps to a fixed tier.
Provider-specific model names are resolved in Story 03 (llm_routing provider mappings).

Task key format: "<operation>.<subtask>"
  e.g. "retain.fact_extraction", "reflect.think"

Budget Profiles (Epic 17)
-------------------------
Higher-level abstraction: Low/Mid/High presets assign a ModelTier to each PipelineStep.
Per-bank and per-request overrides layer on top.  Priority: Request > Bank > Env > Profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .llm_wrapper import LLMConfig


class ModelTier(str, Enum):
    """Model capability tier for LLM routing."""

    SMALL = "small"
    """Simple extraction and yes/no checks. Cheapest, fastest.
    Examples: thalamus scoring (relevance gate), dedup similarity check."""

    MEDIUM = "medium"
    """Structured extraction and scoring. Moderate cost and capability.
    Examples: observation synthesis, opinion extraction, schema-fit check."""

    LARGE = "large"
    """Complex reasoning and causal inference. Most capable, highest cost.
    Examples: fact extraction with causal relations, think/reflect, constructive memory."""


class PipelineStep(str, Enum):
    """Configurable pipeline steps for budget profile assignment.

    Each step can be assigned a ModelTier via a BudgetProfile.
    Steps map back to specific TASK_TIER_MAPPING keys via PIPELINE_STEP_TASK_KEY.
    """

    R0_SEQUENCE_ANALYSIS = "r0_sequence_analysis"
    """R0: Sequence analysis — extract structure from rich content (conversations, narratives)."""

    R1_FACT_EXTRACTION = "r1_fact_extraction"
    """R1: Fact extraction — entity linking, temporal relations, causal reasoning."""

    R4_ENTITY_DISAMBIGUATION = "r4_entity_disambiguation"
    """R4: Entity disambiguation / observation synthesis."""

    REFLECT = "reflect"
    """Reflect: Multi-hop reasoning and answer construction."""

    CONSTRUCTIVE_MEMORY = "constructive_memory"
    """Constructive memory: Cross-fact inference generation."""

    SCHEMA_COMPRESSION = "schema_compression"
    """Schema compression: Pattern extraction and schema-fit evaluation."""

    SCHEMA_DESCRIPTION = "schema_description"
    """C2 schema description: Data-to-text rendering of aggregated properties (Epic 25 Story 08)."""

    OBSERVATION = "observation"
    """Observation synthesis: Entity-level fact summarisation."""


# Canonical mapping from PipelineStep → TASK_TIER_MAPPING key
PIPELINE_STEP_TASK_KEY: Final[dict[PipelineStep, str]] = {
    PipelineStep.R0_SEQUENCE_ANALYSIS: "retain.sequence_analysis",
    PipelineStep.R1_FACT_EXTRACTION: "retain.fact_extraction",
    PipelineStep.R4_ENTITY_DISAMBIGUATION: "retain.observation_synthesis",
    PipelineStep.REFLECT: "reflect.think",
    PipelineStep.CONSTRUCTIVE_MEMORY: "reflect.constructive_memory_inference",
    PipelineStep.SCHEMA_COMPRESSION: "retain.schema_fit_check",
    PipelineStep.SCHEMA_DESCRIPTION: "consolidation.schema_description",
    PipelineStep.OBSERVATION: "retain.observation_synthesis",
}

# Reverse mapping: task key → PipelineStep (first match wins on collision)
_STEP_FOR_TASK_KEY: Final[dict[str, PipelineStep]] = {}
for _step, _key in PIPELINE_STEP_TASK_KEY.items():
    if _key not in _STEP_FOR_TASK_KEY:
        _STEP_FOR_TASK_KEY[_key] = _step


@dataclass(frozen=True)
class BudgetProfile:
    """Immutable mapping of PipelineStep → ModelTier.

    Represents a complete budget configuration for all pipeline steps.
    Use LOW_BUDGET / MID_BUDGET / HIGH_BUDGET as starting points and
    call with_override() to produce custom variants without mutating the original.

    Example::

        profile = MID_BUDGET.with_override(PipelineStep.REFLECT, ModelTier.LARGE)
    """

    steps: dict[PipelineStep, ModelTier]

    def with_override(self, step: PipelineStep, tier: ModelTier) -> BudgetProfile:
        """Return a new BudgetProfile with one step tier replaced.

        Args:
            step: The pipeline step to override.
            tier: The new ModelTier for that step.

        Returns:
            New BudgetProfile instance — original is unchanged (frozen).
        """
        return BudgetProfile(steps={**self.steps, step: tier})

    def get_tier(self, step: PipelineStep) -> ModelTier:
        """Return the ModelTier for a step, defaulting to MEDIUM if not configured.

        Args:
            step: The pipeline step to query.

        Returns:
            Configured ModelTier or MEDIUM as safe default.
        """
        return self.steps.get(step, ModelTier.MEDIUM)


# ---------------------------------------------------------------------------
# Predefined Budget Profiles
# ---------------------------------------------------------------------------
# LOW:  Cost-optimised. Most steps use SMALL; constructive memory uses MEDIUM
#       (minimum viable quality for inference generation).
# MID:  Balanced default. R1 fact extraction stays SMALL (structured, formulaic);
#       everything else scales to MEDIUM.
# HIGH: Quality-optimised. R0, R4, Reflect, Constructive, Schema → LARGE;
#       only R1 and Observation stay MEDIUM (sufficient for their scope).
# ---------------------------------------------------------------------------

LOW_BUDGET: Final[BudgetProfile] = BudgetProfile(
    steps={
        PipelineStep.R0_SEQUENCE_ANALYSIS: ModelTier.SMALL,
        PipelineStep.R1_FACT_EXTRACTION: ModelTier.SMALL,
        PipelineStep.R4_ENTITY_DISAMBIGUATION: ModelTier.SMALL,
        PipelineStep.REFLECT: ModelTier.SMALL,
        PipelineStep.CONSTRUCTIVE_MEMORY: ModelTier.MEDIUM,
        PipelineStep.SCHEMA_COMPRESSION: ModelTier.SMALL,
        PipelineStep.SCHEMA_DESCRIPTION: ModelTier.SMALL,
        PipelineStep.OBSERVATION: ModelTier.SMALL,
    }
)

MID_BUDGET: Final[BudgetProfile] = BudgetProfile(
    steps={
        PipelineStep.R0_SEQUENCE_ANALYSIS: ModelTier.MEDIUM,
        PipelineStep.R1_FACT_EXTRACTION: ModelTier.SMALL,
        PipelineStep.R4_ENTITY_DISAMBIGUATION: ModelTier.MEDIUM,
        PipelineStep.REFLECT: ModelTier.MEDIUM,
        PipelineStep.CONSTRUCTIVE_MEMORY: ModelTier.MEDIUM,
        PipelineStep.SCHEMA_COMPRESSION: ModelTier.MEDIUM,
        # Description is pure data-to-text, not reasoning — stays SMALL even at MID.
        PipelineStep.SCHEMA_DESCRIPTION: ModelTier.SMALL,
        PipelineStep.OBSERVATION: ModelTier.MEDIUM,
    }
)

HIGH_BUDGET: Final[BudgetProfile] = BudgetProfile(
    steps={
        PipelineStep.R0_SEQUENCE_ANALYSIS: ModelTier.LARGE,
        PipelineStep.R1_FACT_EXTRACTION: ModelTier.MEDIUM,
        PipelineStep.R4_ENTITY_DISAMBIGUATION: ModelTier.LARGE,
        PipelineStep.REFLECT: ModelTier.LARGE,
        PipelineStep.CONSTRUCTIVE_MEMORY: ModelTier.LARGE,
        PipelineStep.SCHEMA_COMPRESSION: ModelTier.LARGE,
        # Even HIGH keeps description on SMALL — it's deterministic prose,
        # not analytical work; spending Opus tokens here is wasted budget.
        PipelineStep.SCHEMA_DESCRIPTION: ModelTier.SMALL,
        PipelineStep.OBSERVATION: ModelTier.MEDIUM,
    }
)


# ---------------------------------------------------------------------------
# Task-to-Tier Mapping
# ---------------------------------------------------------------------------
# Keys use "<operation>.<subtask>" format.
# Future subtasks (Engram architecture) are included as forward-declarations
# so their tier is explicit before implementation, not decided ad-hoc.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provider-Tier-Mappings (L3)
# ---------------------------------------------------------------------------
# Maps (provider, tier) → default model name.
# Used by resolve_llm_config when no explicit subtask env var is set:
#   set LLM_PROVIDER=anthropic  →  fact_extraction gets claude-opus-4-6 automatically,
#                                   observation_synthesis gets claude-sonnet-4-6, etc.
#
# Override a single subtask:
#   HINDSIGHT_API_RETAIN_FACT_EXTRACTION_LLM_MODEL=my-custom-model  (always wins)
#
# Extend for new providers: add a key to PROVIDER_TIER_MODELS.
# Providers without an entry fall back to the operation-level model config.
# ---------------------------------------------------------------------------

PROVIDER_TIER_MODELS: Final[dict[str, dict[ModelTier, str]]] = {
    "anthropic": {
        ModelTier.SMALL: "claude-haiku-4-5-20251001",
        ModelTier.MEDIUM: "claude-sonnet-4-6",
        ModelTier.LARGE: "claude-opus-4-6",
    },
    "openai": {
        # No stronger reasoning model on OpenAI available; Opus-equivalent → gpt-4o.
        ModelTier.SMALL: "gpt-4o-mini",
        ModelTier.MEDIUM: "gpt-4o",
        ModelTier.LARGE: "gpt-4o",
    },
    "groq": {
        # No stronger model available on Groq; LARGE falls back to same as MEDIUM.
        ModelTier.SMALL: "llama-3.1-8b-instant",
        ModelTier.MEDIUM: "llama-3.3-70b-versatile",
        ModelTier.LARGE: "llama-3.3-70b-versatile",
    },
    # "ollama": intentionally omitted — local model names are installation-specific.
    # Use subtask env vars to configure per-subtask models for Ollama.
}

TASK_TIER_MAPPING: Final[dict[str, ModelTier]] = {
    # --- Retain pipeline ---
    # Full fact extraction: entity linking, temporal relations, causal reasoning.
    # Requires strong structured output and multi-step inference → LARGE.
    "retain.fact_extraction": ModelTier.LARGE,
    # Observation synthesis: summarise entity-level facts into observations.
    # Structured but not reasoning-heavy → MEDIUM.
    "retain.observation_synthesis": ModelTier.MEDIUM,
    # Thalamus scoring (future): binary relevance gate per incoming memory.
    # Simple yes/no + 0–1 scores, no reasoning required → SMALL.
    "retain.thalamus_scoring": ModelTier.SMALL,
    # Schema-fit check (future): does a new Engram match an existing schema pattern?
    # Moderate structure, pattern comparison → MEDIUM.
    "retain.schema_fit_check": ModelTier.MEDIUM,
    # Conflict resolution (future): detect and resolve contradictions between Engrams.
    # Requires reasoning over multiple facts → LARGE.
    "retain.conflict_resolution": ModelTier.LARGE,
    # R0 Sequence Analysis (Epic 15): budget-dependent tier per input item.
    # Low=atomic facts, Mid=+action/effect/exp/outcome, High=+implicit causalities.
    "retain.sequence_analysis_low": ModelTier.SMALL,
    "retain.sequence_analysis": ModelTier.MEDIUM,
    "retain.sequence_analysis_high": ModelTier.LARGE,
    # --- Consolidation pipeline (Epic 25) ---
    # C2 schema description: data-to-text from already-aggregated properties.
    # No reasoning — just prose rendering → SMALL.
    "consolidation.schema_description": ModelTier.SMALL,
    # --- Reflect pipeline ---
    # Think / answer construction: multi-hop reasoning over retrieved facts.
    # Deepest reasoning step in the system → LARGE.
    "reflect.think": ModelTier.LARGE,
    # Opinion extraction: identify opinions + confidence from text.
    # Structured but simpler than full fact extraction → MEDIUM.
    "reflect.opinion_extraction": ModelTier.MEDIUM,
    # Constructive memory inference: derive inferences from fact combinations.
    # Requires cross-fact reasoning → LARGE.
    "reflect.constructive_memory_inference": ModelTier.LARGE,
    # Reconsolidation evaluation: confirm/modify/contradict a single stored Engram.
    # Structured classification, similar depth to opinion_extraction → MEDIUM.
    "reflect.reconsolidation": ModelTier.MEDIUM,
    # Prediction error detection: compare expectation vs. constructed answer for severity.
    # Simple classification → SMALL.
    "reflect.prediction_error_detection": ModelTier.SMALL,
}


def resolve_llm_config(
    operation: str,
    subtask: str,
    operation_config: LLMConfig,
    global_config: LLMConfig,  # noqa: ARG001 — reserved for future multi-provider fallback
    budget_profile: BudgetProfile | None = None,
) -> LLMConfig:
    """Resolve LLMConfig for a subtask using a 4-level fallback chain.

    Priority (highest → lowest):
      1. Subtask env var    — HINDSIGHT_API_{OP}_{SUBTASK}_LLM_{MODEL,PROVIDER}
      2. Budget profile     — BudgetProfile.get_tier(step) → PROVIDER_TIER_MODELS lookup
                              (only when budget_profile is provided and task key maps to a step)
      3. Tier default       — PROVIDER_TIER_MODELS[provider][tier] via TASK_TIER_MAPPING
                              (backward-compat fallback when no profile is given)
      4. Operation config   — the operation-level model (already falls back to global)

    Tier defaults only apply when the task key is in TASK_TIER_MAPPING and the
    active provider has an entry in PROVIDER_TIER_MODELS. Unknown providers fall
    through to the operation config unchanged.

    Args:
        operation: Pipeline name, e.g. "retain" or "reflect".
        subtask: Subtask name with underscores, e.g. "fact_extraction" (no dots).
        operation_config: Operation-level LLMConfig (already incorporates global fallback).
        global_config: Global LLMConfig (fills gaps when subtask overrides partial fields).
        budget_profile: Optional BudgetProfile. When set, its tier mapping takes precedence
            over TASK_TIER_MAPPING for steps that have a corresponding PipelineStep entry.

    Returns:
        Resolved LLMConfig for this subtask. Never None.
    """
    from ..config import get_subtask_llm_model, get_subtask_llm_provider
    from .llm_wrapper import LLMConfig as _LLMConfig

    # --- Level 1: Explicit subtask env var ---
    subtask_provider = get_subtask_llm_provider(operation, subtask)
    subtask_model = get_subtask_llm_model(operation, subtask)
    if subtask_provider or subtask_model:
        return _LLMConfig(
            provider=subtask_provider or operation_config.provider,
            model=subtask_model or operation_config.model,
            api_key=operation_config.api_key,
            base_url=operation_config.base_url,
        )

    task_key = f"{operation}.{subtask}"

    # --- Level 2: Budget profile tier ---
    if budget_profile is not None:
        step = _STEP_FOR_TASK_KEY.get(task_key)
        if step is not None:
            tier = budget_profile.get_tier(step)
            tier_model = PROVIDER_TIER_MODELS.get(operation_config.provider, {}).get(tier)
            if tier_model:
                return _LLMConfig(
                    provider=operation_config.provider,
                    model=tier_model,
                    api_key=operation_config.api_key,
                    base_url=operation_config.base_url,
                )

    # --- Level 3: Tier default from PROVIDER_TIER_MODELS (backward compat) ---
    tier = TASK_TIER_MAPPING.get(task_key)
    if tier is not None:
        tier_model = PROVIDER_TIER_MODELS.get(operation_config.provider, {}).get(tier)
        if tier_model:
            return _LLMConfig(
                provider=operation_config.provider,
                model=tier_model,
                api_key=operation_config.api_key,
                base_url=operation_config.base_url,
            )

    # --- Level 4: Operation config (already falls back to global) ---
    return operation_config


class LLMRegistry:
    """Per-engine registry that resolves and caches LLMConfig instances per subtask.

    Provides a single `get_llm(operation, subtask)` interface. Internal resolution
    uses the 3-level fallback chain: subtask env var → operation config → global config.
    Instances are cached after first resolution to avoid repeated env-var reads.
    """

    def __init__(
        self,
        global_config: LLMConfig,
        operation_configs: dict[str, LLMConfig],
    ) -> None:
        """
        Args:
            global_config: Fallback LLMConfig used when no operation override exists.
            operation_configs: Map of operation name → LLMConfig, e.g.
                {"retain": retain_cfg, "reflect": reflect_cfg}.
        """
        self._global = global_config
        self._operation = operation_configs
        self._cache: dict[str, LLMConfig] = {}

    def get_llm(self, operation: str, subtask: str) -> LLMConfig:
        """Return the LLMConfig for a subtask, resolving and caching on first call.

        Args:
            operation: Pipeline name, e.g. "retain" or "reflect".
            subtask: Subtask name with underscores, e.g. "fact_extraction".

        Returns:
            Resolved LLMConfig. Never None.
        """
        cache_key = f"{operation}.{subtask}"
        if cache_key not in self._cache:
            op_config = self._operation.get(operation, self._global)
            self._cache[cache_key] = resolve_llm_config(operation, subtask, op_config, self._global)
        return self._cache[cache_key]

    def resolve_with_profile(self, operation: str, subtask: str, budget_profile: BudgetProfile) -> LLMConfig:
        """Resolve LLMConfig for a subtask using a specific BudgetProfile (not cached).

        Unlike get_llm(), this bypasses the cache because profile-based resolution
        is request-scoped and may vary per call.

        Args:
            operation: Pipeline name, e.g. "retain".
            subtask: Subtask name with underscores, e.g. "sequence_analysis".
            budget_profile: The active BudgetProfile (with any per-step overrides applied).

        Returns:
            Resolved LLMConfig. Never None.
        """
        op_config = self._operation.get(operation, self._global)
        return resolve_llm_config(operation, subtask, op_config, self._global, budget_profile=budget_profile)


def get_tier(task_key: str) -> ModelTier:
    """Return the ModelTier assigned to a task key.

    Args:
        task_key: "<operation>.<subtask>" identifier, e.g. "retain.fact_extraction".

    Returns:
        The ModelTier for this task.

    Raises:
        KeyError: If task_key is not registered in TASK_TIER_MAPPING.
    """
    return TASK_TIER_MAPPING[task_key]
