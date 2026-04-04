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
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Task-to-Tier Mapping
# ---------------------------------------------------------------------------
# Keys use "<operation>.<subtask>" format.
# Future subtasks (Engram architecture) are included as forward-declarations
# so their tier is explicit before implementation, not decided ad-hoc.
# ---------------------------------------------------------------------------

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
    # --- Reflect pipeline ---
    # Think / answer construction: multi-hop reasoning over retrieved facts.
    # Deepest reasoning step in the system → LARGE.
    "reflect.think": ModelTier.LARGE,
    # Opinion extraction: identify opinions + confidence from text.
    # Structured but simpler than full fact extraction → MEDIUM.
    "reflect.opinion_extraction": ModelTier.MEDIUM,
    # Constructive memory inference (future): derive inferences from fact combinations.
    # Requires cross-fact reasoning → LARGE.
    "reflect.constructive_memory_inference": ModelTier.LARGE,
}


def resolve_llm_config(
    operation: str,
    subtask: str,
    operation_config: LLMConfig,
    global_config: LLMConfig,  # noqa: ARG001 — reserved for future multi-provider fallback
) -> LLMConfig:
    """Resolve LLMConfig for a subtask using a 3-level fallback chain.

    Priority: subtask env var → operation config → global config.
    The operation_config is already built with the global fallback in memory_engine,
    so returning it covers both the operation and global levels.

    Args:
        operation: Pipeline name, e.g. "retain" or "reflect".
        subtask: Subtask name, e.g. "fact_extraction" (underscores, no dots).
        operation_config: Operation-level LLMConfig (already falls back to global).
        global_config: Global LLMConfig (used to fill gaps when subtask overrides partial fields).

    Returns:
        Resolved LLMConfig for this subtask.
    """
    from ..config import get_subtask_llm_model, get_subtask_llm_provider

    subtask_provider = get_subtask_llm_provider(operation, subtask)
    subtask_model = get_subtask_llm_model(operation, subtask)

    if subtask_provider or subtask_model:
        # Build from subtask env vars, inherit remaining fields from operation config
        from .llm_wrapper import LLMConfig as _LLMConfig

        return _LLMConfig(
            provider=subtask_provider or operation_config.provider,
            model=subtask_model or operation_config.model,
            api_key=operation_config.api_key,
            base_url=operation_config.base_url,
        )

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
