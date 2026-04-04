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

from enum import Enum
from typing import Final


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
