"""
Reflect & Reconsolidation — priority-based knowledge update pipeline.

Bio mapping: Post-retrieval reconsolidation window — retrieved memories become
labile and can be updated before re-consolidation. Dopaminergic prediction error
signal modulates plasticity strength.

Concept reference: docs/engram/concept.md — Chapter 10 (Reflect & Reconsolidation)
"""

from .prediction_error_registry import PredictionErrorEntry, PredictionErrorRegistry
from .reconsolidation_queue import ReconsolidationOutcome, build_reconsolidation_queue

__all__ = [
    "PredictionErrorRegistry",
    "PredictionErrorEntry",
    "build_reconsolidation_queue",
    "ReconsolidationOutcome",
]
