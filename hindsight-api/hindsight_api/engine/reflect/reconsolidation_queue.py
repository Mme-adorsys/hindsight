"""
Reconsolidation Queue — priority-sorted Engram selection for the reflect pipeline.

Implements RF1: builds the ordered list of Engrams to reconsolidate in a single
cycle, respecting the mode-dependent budget and three-tier priority ordering.

Priority order (highest → lowest):
  1. Prediction-Error Engrams — flagged by Constructive Memory (Epic 11)
  2. Weak Engrams             — strength < WEAK_STRENGTH_THRESHOLD (fragile, most plastic)
  3. Remaining Engrams        — sorted by last_accessed ascending (stalest first)

Budget per reconsolidation_level (number of Engrams processed per cycle):
  minimal      →  5   (Precision mode — only critical updates)
  moderate     → 15   (Exploration mode — broader refresh)
  schema_update→ 25   (Analogy mode — schema-oriented rewrites)
  aggressive   → 50   (Validation mode — full reconsolidation sweep)

Bio mapping:
- Priority Queue     → post-retrieval lability window targets most fragile synapses first
- Weak-first order   → LTP/LTD is strongest at low-weight synapses (BCM plasticity rule)
- PE priority        → Dopaminergic error signal gates plasticity at specific synapses
- Budget cap         → Energy constraint on reconsolidation (offline process has finite capacity)

Concept reference: docs/engram/concept.md — Chapter 10 (RF1 Priority-basierte Reconsolidation)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..response_models import FullEngram
    from ..session.mode_config import ModeConfig
    from .prediction_error_registry import PredictionErrorRegistry

logger = logging.getLogger(__name__)

# Strength below this value → Engram is considered "weak" and gets elevated priority
WEAK_STRENGTH_THRESHOLD: float = 0.3

# Budget per reconsolidation_level: max Engrams processed in one cycle
LEVEL_BUDGET: dict[str, int] = {
    "minimal": 5,
    "moderate": 15,
    "schema_update": 25,
    "aggressive": 50,
}

# Sentinel datetime for Engrams that have never been accessed (sort them last)
_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


class ReconsolidationOutcome(str, Enum):
    """Result of an LLM reconsolidation call — drives Strength delta (T4)."""

    CONFIRMED = "confirmed"
    """LLM determined the Engram is still correct → Strength +0.1"""

    MODIFIED = "modified"
    """LLM updated the Engram content → Strength unchanged (content changed)"""

    CONTRADICTION = "contradiction"
    """LLM found a contradiction with current knowledge → Strength -0.2"""


# Strength deltas applied after reconsolidation (T4)
STRENGTH_DELTA: dict[ReconsolidationOutcome, float] = {
    ReconsolidationOutcome.CONFIRMED: +0.1,
    ReconsolidationOutcome.MODIFIED: 0.0,
    ReconsolidationOutcome.CONTRADICTION: -0.2,
}


def build_reconsolidation_queue(
    engrams: list[FullEngram],
    prediction_errors: PredictionErrorRegistry | None,
    mode_config: ModeConfig,
) -> list[FullEngram]:
    """
    Build a priority-sorted, budget-capped list of Engrams to reconsolidate.

    Args:
        engrams:           All candidate Engrams for this reconsolidation cycle.
        prediction_errors: Registry of Engrams flagged with a prediction error.
                           Pass None (or an empty registry) when no PE flags exist.
        mode_config:       Current session ModeConfig — supplies reconsolidation_level.

    Returns:
        Ordered list of up to LEVEL_BUDGET[reconsolidation_level] Engrams.
        Order: PE-flagged → weak (strength < 0.3, ascending) → rest (last_accessed ascending).
    """
    budget = LEVEL_BUDGET.get(mode_config.reconsolidation_level, LEVEL_BUDGET["moderate"])
    pe_ids: set[str] = prediction_errors.get_flagged_ids() if prediction_errors else set()

    pe_engrams: list[FullEngram] = []
    weak_engrams: list[FullEngram] = []
    rest_engrams: list[FullEngram] = []

    for engram in engrams:
        eid = str(engram.engram_id)
        strength = engram.metadata.strength if engram.metadata is not None else 0.0
        if eid in pe_ids:
            pe_engrams.append(engram)
        elif strength < WEAK_STRENGTH_THRESHOLD:
            weak_engrams.append(engram)
        else:
            rest_engrams.append(engram)

    # Within each tier: sort by ascending strength (weakest first), then last_accessed ascending
    def _sort_key(e: FullEngram) -> tuple[float, datetime]:
        strength = e.metadata.strength if e.metadata is not None else 0.0
        accessed_raw = e.metadata.last_accessed if e.metadata is not None else None
        accessed = accessed_raw if accessed_raw is not None else _EPOCH
        # Make timezone-aware if naive
        if accessed.tzinfo is None:
            accessed = accessed.replace(tzinfo=timezone.utc)
        return (strength, accessed)

    pe_engrams.sort(key=_sort_key)
    weak_engrams.sort(key=_sort_key)
    rest_engrams.sort(key=_sort_key)

    ordered = pe_engrams + weak_engrams + rest_engrams
    result = ordered[:budget]

    logger.debug(
        "Reconsolidation queue built: pe=%d weak=%d rest=%d budget=%d → selected=%d",
        len(pe_engrams),
        len(weak_engrams),
        len(rest_engrams),
        budget,
        len(result),
    )
    return result


def apply_strength_delta(current_strength: float, outcome: ReconsolidationOutcome) -> float:
    """
    Compute updated Engram strength after reconsolidation.

    Clamps the result to [0.0, 1.0].

    Args:
        current_strength: Strength before reconsolidation (0.0 – 1.0).
        outcome:          Result of the LLM reconsolidation call.

    Returns:
        New strength value, clamped to [0.0, 1.0].
    """
    delta = STRENGTH_DELTA[outcome]
    return max(0.0, min(1.0, current_strength + delta))
