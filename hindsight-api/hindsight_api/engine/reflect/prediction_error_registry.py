"""
Prediction Error Registry — per-session tracker for Engrams that caused prediction errors.

A prediction error occurs when a retrieved or constructed answer deviates from the
agent's current_expectation (stored in SessionState). The involved Engrams are flagged
so the reconsolidation pipeline (RF1) can prioritise them in the next cycle.

This registry is populated by Constructive Memory (Epic 11). The data structure is
established here so the reconsolidation queue (RF1) can consume it from day one.

Bio mapping:
- Prediction error       → Dopaminergic mismatch signal (positive or negative PE)
- Flagged Engrams        → Synapses marked for plasticity update
- Per-session scope      → PFC working context tracks active prediction state
- Priority in recon.     → PE signal drives LTP/LTD at flagged synapses first

Concept reference: docs/engram/concept.md — Chapter 10 (RF1), Chapter 11 (Prediction Error)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class PredictionErrorEntry:
    """Single prediction-error event tied to one Engram."""

    engram_id: str
    error_context: str
    flagged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PredictionErrorRegistry:
    """
    In-memory registry (per session) of Engrams that caused prediction errors.

    Lifecycle:
    - Created once per SessionState (or per reflect_async call when no session exists)
    - Populated by Constructive Memory (Epic 11) via flag_prediction_error()
    - Consumed by build_reconsolidation_queue() to prioritise affected Engrams
    - Cleared via clear() at session end or after each reconsolidation cycle

    Thread safety: not required — sessions are single-threaded async contexts.
    """

    _entries: dict[str, PredictionErrorEntry] = field(default_factory=dict)

    def flag_prediction_error(self, engram_id: str, error_context: str) -> None:
        """
        Flag an Engram as having caused a prediction error.

        If the same Engram is flagged multiple times within a session, the most
        recent error_context overwrites the previous one (last-write wins).

        Args:
            engram_id:     ID of the Engram involved in the prediction error.
            error_context: Human-readable description of what went wrong
                           (e.g. "answer contradicted current_expectation: …").
        """
        entry = PredictionErrorEntry(engram_id=engram_id, error_context=error_context)
        self._entries[engram_id] = entry
        logger.debug("PE flagged: engram=%s context=%r", engram_id, error_context[:80])

    def is_flagged(self, engram_id: str) -> bool:
        """Return True if the Engram has an active prediction-error flag."""
        return engram_id in self._entries

    def get_flagged_ids(self) -> set[str]:
        """Return the set of all currently flagged Engram IDs."""
        return set(self._entries.keys())

    def get_entry(self, engram_id: str) -> PredictionErrorEntry | None:
        """Return the full entry for an Engram, or None if not flagged."""
        return self._entries.get(engram_id)

    def clear(self) -> None:
        """Remove all flags — call after a reconsolidation cycle completes."""
        count = len(self._entries)
        self._entries.clear()
        if count:
            logger.debug("PE registry cleared (%d entries)", count)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)
