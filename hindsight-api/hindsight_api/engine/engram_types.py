"""
Lightweight Engram type definitions with no circular dependencies.

Placed at engine level (not in retain/) so both response_models.py and
retain/types.py can import from here without triggering retain/__init__.py.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class BankTier(str, Enum):
    """
    3-Tier Bank Model (Epic 14 B1).

    SESSION    — Tier 1: Agent Session Bank (PostgreSQL). Transient, per-agent,
                 fast writes. Raw memories before consolidation.
    DICTIONARY — Tier 2: Agent Engram Dictionary (Neo4j + Qdrant). Filtered,
                 scored, consolidated per-agent knowledge.
    SHARED     — Tier 3: Shared Memory Bank (Neo4j + Qdrant). Cross-agent
                 knowledge promoted from strong neocortex Engrams.

    Bio mapping: SESSION ≈ Hippocampal buffer, DICTIONARY ≈ Engram store,
    SHARED ≈ Neocortex / semantic long-term memory.
    """

    SESSION = "session"
    DICTIONARY = "dictionary"
    SHARED = "shared"


@dataclass
class ThalamusRationale:
    """
    Human-readable explanation of how the 4 Thalamus scores were computed.

    All Thalamus dimensions are deterministic embedding formulas (Epic 16), so
    the "why" is always reconstructible from the inputs. This dataclass captures
    the inputs that actually drove the score — which gets rendered in the CP
    memory detail panel as a tooltip/expand per score.

    Fields default to "score was computed with default inputs, no context
    available" so the dataclass can always be attached without optionality
    elsewhere in the pipeline.
    """

    # Novelty: closest existing engram in Qdrant and its similarity value.
    # max_similar_id is None when the bank has no existing engrams.
    novelty_max_similar_id: str | None = None
    novelty_max_similarity: float = 0.0

    # Surprise / Emotional Valence share inputs. expectation_provided and
    # outcome_provided tell the UI whether the score is real or the neutral
    # fallback (0.5 / 0.3 respectively).
    surprise_expectation_provided: bool = False
    surprise_outcome_provided: bool = False

    # Task-Relevance: where did the context come from (none → neutral 0.5).
    task_relevance_context_source: Literal["none", "item", "session"] = "none"

    # Raw prediction-error magnitude before VALENCE_AMPLIFICATION.
    # 0.0 when expectation/outcome not provided (fallback path).
    valence_prediction_error: float = 0.0


@dataclass
class ThalamusScores:
    """
    4-dimensional relevance scores computed by the Thalamus Filter.

    All scores are in range 0.0–1.0.

    Bio mapping:
    - novelty       → CA1 Mismatch Detection
    - surprise      → Noradrenaline release (unexpected vs. current_expectation)
    - task_relevance → PFC Top-Down Attention
    - emotional_valence → Amygdala Modulation

    The optional `rationale` field carries a ThalamusRationale describing
    *why* each score came out the way it did. Call-sites that only read the
    score floats remain compatible — `rationale` defaults to None for
    backwards compatibility with any code paths that construct ThalamusScores
    without going through ThalamusFilter.
    """

    novelty: float = 0.0
    surprise: float = 0.0
    task_relevance: float = 0.0
    emotional_valence: float = 0.0
    overall: float = 0.0
    rationale: ThalamusRationale | None = field(default=None)

    def __post_init__(self) -> None:
        for field_name in ("novelty", "surprise", "task_relevance", "emotional_valence", "overall"):
            value = getattr(self, field_name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"ThalamusScores.{field_name} must be in range 0.0–1.0, got {value}")
