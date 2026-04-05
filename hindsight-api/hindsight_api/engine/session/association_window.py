"""
Association Window — Synaptic Tagging & Capture (STC) for the Working Context.

Tracks which Engrams in the Focus and Supporting tiers were active within a
short time window. When two Engrams co-occur within the window, a
TEMPORAL_PROXIMITY link is written to Neo4j.

Distinct from Co-Activation (co_activation_tracker.py):
- Co-Activation: based on repeated co-occurrence in *Recall results*
- Association Window: based on temporal proximity in *Working Context* (activated_at)

Bio mapping:
- Association Window        → Synaptic Tagging & Capture (Frey & Morris, 1997)
- temporal_proximity links  → "Tagged" synapses that can later be captured together
- Window (5 min default)    → STC time window for synaptic tag persistence
- Focus + Supporting only   → Only well-activated representations receive tags
- Weight cap (0.5)          → STC links stay weak; consolidation requires NCR

Concept reference: docs/engram/concept.md — Chapter 14 (Weak Connections & Synaptic Tagging)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from .working_context import ActiveEngrams

logger = logging.getLogger(__name__)

# Default time window: Engrams activated within this many minutes are associated
DEFAULT_WINDOW_MINUTES: float = 5.0

# Initial weight for a new TEMPORAL_PROXIMITY link
INITIAL_LINK_WEIGHT: float = 0.1

# Weight increment when an existing link is strengthened by re-association
WEIGHT_INCREMENT: float = 0.05

# Maximum weight for TEMPORAL_PROXIMITY links (keeps them "weak")
MAX_LINK_WEIGHT: float = 0.5

# Default: check associations every N recalls
DEFAULT_CHECK_EVERY_N: int = 5


@dataclass
class AssociationWindow:
    """
    STC-inspired tracker: finds EngramRef pairs co-active within a time window
    and writes TEMPORAL_PROXIMITY links to Neo4j.

    Symmetry: pair (A, B) is always stored as (min(A,B), max(A,B)).

    Usage:
        aw.check_and_record(active_engrams)  # record current window state
        if aw.should_check():
            pairs = aw.check_associations(active_engrams)
        if aw.should_flush():
            await aw.flush_to_neo4j(neo4j_client)
    """

    window_minutes: float = DEFAULT_WINDOW_MINUTES
    check_every_n: int = DEFAULT_CHECK_EVERY_N

    # Count how many times each pair has co-occurred in the window
    _pair_counts: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)

    # Pairs already written to Neo4j — tracked to avoid duplicate initial writes
    # (strengthening is handled by the MERGE query itself)
    _written_pairs: set[tuple[str, str]] = field(default_factory=set, repr=False)

    # Total recall count for periodic check scheduling
    _recall_count: int = field(default=0, repr=False)

    _MAX_LINK_WEIGHT: ClassVar[float] = MAX_LINK_WEIGHT
    _INITIAL_LINK_WEIGHT: ClassVar[float] = INITIAL_LINK_WEIGHT
    _WEIGHT_INCREMENT: ClassVar[float] = WEIGHT_INCREMENT

    def check_associations(self, active_engrams: ActiveEngrams) -> list[tuple[str, str]]:
        """
        Find all Focus + Supporting Engram pairs whose activated_at timestamps
        are within the window, record them, and return new pairs found.

        Increments the recall count for periodic scheduling.

        Args:
            active_engrams: Current ActiveEngrams from the WorkingContext.

        Returns:
            List of (engram_id_a, engram_id_b) pairs (symmetric, min/max normalised).
        """
        self._recall_count += 1

        # Fix 18: Deduplicate refs by engram_id before pair generation to prevent
        # count inflation when the same engram appears in both focus and supporting.
        seen_ids: set[str] = set()
        unique_refs: list = []
        for ref in list(active_engrams.focus) + list(active_engrams.supporting):
            if ref.engram_id not in seen_ids:
                seen_ids.add(ref.engram_id)
                unique_refs.append(ref)
        refs = unique_refs

        if len(refs) < 2:
            return []

        # Fix 11: Use seen set within the pair loop to prevent double-counting
        # pairs within a single call (e.g. A↔B counted once regardless of path).
        seen_pairs: set[tuple[str, str]] = set()
        new_pairs: list[tuple[str, str]] = []
        for i, ref_a in enumerate(refs):
            for ref_b in refs[i + 1 :]:
                delta_minutes = _time_delta_minutes(ref_a.activated_at, ref_b.activated_at)
                if delta_minutes <= self.window_minutes:
                    key = (min(ref_a.engram_id, ref_b.engram_id), max(ref_a.engram_id, ref_b.engram_id))
                    if key not in seen_pairs:
                        self._pair_counts[key] = self._pair_counts.get(key, 0) + 1
                        new_pairs.append(key)
                        seen_pairs.add(key)

        return new_pairs

    def should_flush(self) -> bool:
        """Return True when the periodic flush interval has been reached."""
        return self._recall_count > 0 and self._recall_count % self.check_every_n == 0

    async def flush_to_neo4j(self, neo4j_client) -> int:
        """
        Write all recorded association pairs to Neo4j as TEMPORAL_PROXIMITY links.

        Uses MERGE to strengthen existing links up to MAX_LINK_WEIGHT.
        No-op if neo4j_client is None (graceful degradation for unit tests).

        Returns:
            Number of pairs written (new or strengthened).
        """
        if neo4j_client is None:
            return 0
        if not self._pair_counts:
            return 0

        from ..retain.link_creation import create_association_window_link

        written = 0
        errors = 0
        for pair, count in list(self._pair_counts.items()):
            weight = min(self._INITIAL_LINK_WEIGHT + (count - 1) * self._WEIGHT_INCREMENT, self._MAX_LINK_WEIGHT)
            from_id, to_id = pair
            try:
                await create_association_window_link(neo4j_client, from_id, to_id, weight)
                self._written_pairs.add(pair)
                written += 1
                logger.debug(
                    "[AssociationWindow] TEMPORAL_PROXIMITY link: %s ↔ %s (count=%d, weight=%.2f)",
                    from_id,
                    to_id,
                    count,
                    weight,
                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    "[AssociationWindow] Failed to write link %s ↔ %s: %s",
                    from_id,
                    to_id,
                    exc,
                )

        if errors:
            logger.error(
                "[AssociationWindow] Flush: %d/%d pairs failed",
                errors,
                written + errors,
            )
        return written

    def reset(self) -> None:
        """Clear all state (call at session end after flush)."""
        self._pair_counts.clear()
        self._written_pairs.clear()
        self._recall_count = 0


def _time_delta_minutes(a: datetime, b: datetime) -> float:
    """Return the absolute time difference between two datetimes in minutes."""
    delta = abs((a - b).total_seconds())
    return delta / 60.0
