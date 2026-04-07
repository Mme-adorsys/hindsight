"""
Lightweight Engram type definitions with no circular dependencies.

Placed at engine level (not in retain/) so both response_models.py and
retain/types.py can import from here without triggering retain/__init__.py.
"""

from dataclasses import dataclass
from enum import Enum


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
class ThalamusScores:
    """
    4-dimensional relevance scores computed by the Thalamus Filter.

    All scores are in range 0.0–1.0.

    Bio mapping:
    - novelty       → CA1 Mismatch Detection
    - surprise      → Noradrenaline release (unexpected vs. current_expectation)
    - task_relevance → PFC Top-Down Attention
    - emotional_valence → Amygdala Modulation
    """

    novelty: float = 0.0
    surprise: float = 0.0
    task_relevance: float = 0.0
    emotional_valence: float = 0.0
    overall: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("novelty", "surprise", "task_relevance", "emotional_valence", "overall"):
            value = getattr(self, field_name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"ThalamusScores.{field_name} must be in range 0.0–1.0, got {value}")
