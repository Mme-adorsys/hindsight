"""
Lightweight Engram type definitions with no circular dependencies.

Placed at engine level (not in retain/) so both response_models.py and
retain/types.py can import from here without triggering retain/__init__.py.
"""

from dataclasses import dataclass


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
