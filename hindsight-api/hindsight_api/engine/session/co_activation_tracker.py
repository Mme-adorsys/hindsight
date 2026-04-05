"""
Co-Activation Tracker — Hebb-based weak link formation between co-recalled Engrams.

Tracks which Engrams are repeatedly retrieved together in the same session.
When a pair exceeds the co-activation threshold, a CO_ACTIVATED link is written
to Neo4j via create_co_activation_link().

Bio mapping:
- Co-activation tracking  → Hebbian learning ("neurons that fire together wire together")
- CO_ACTIVATED links       → Weak synaptic connections formed by repeated co-firing
- Threshold enforcement    → Synaptic consolidation requires repetition (not a single event)
- Focus + Supporting only  → Only highly-activated representations form new associations;
                             peripheral activation is too weak for reliable link formation

Concept reference: docs/engram/concept.md — Chapter 14 (Weak Connections & Synaptic Tagging)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    pass  # neo4j_client imported lazily to avoid circular imports

logger = logging.getLogger(__name__)

# Default threshold: a pair must co-occur at least this many times before linking
DEFAULT_CO_ACTIVATION_THRESHOLD: int = 3

# Default: flush to Neo4j every N recalls (to avoid per-recall DB writes)
DEFAULT_FLUSH_EVERY_N: int = 5


@dataclass
class CoActivationTracker:
    """
    In-memory counter for Engram pairs that co-occur in recall results.

    Tracks (Focus + Supporting) tier Engram IDs. When a pair's count reaches
    `threshold`, the pair is eligible for CO_ACTIVATED link creation in Neo4j
    (written on periodic flush or session end).

    Symmetry: pair (A, B) is always stored as (min(A,B), max(A,B)) so that
    (A,B) and (B,A) map to the same counter entry.
    """

    threshold: int = DEFAULT_CO_ACTIVATION_THRESHOLD
    flush_every_n: int = DEFAULT_FLUSH_EVERY_N

    # Internal state — ClassVar-style would require field() to be excluded;
    # using field(default_factory=...) for mutable defaults per @dataclass rules
    _counter: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)
    _recall_count: int = field(default=0, repr=False)

    # Pairs that have already been written to Neo4j (avoid duplicate writes)
    _written_pairs: set[tuple[str, str]] = field(default_factory=set, repr=False)

    # Minimum weight for a new link; incremented proportionally with count
    _MAX_WEIGHT: ClassVar[float] = 1.0

    def track_recall(self, engram_ids: list[str]) -> None:
        """
        Record a co-activation event for all pairs in the given Engram ID list.

        Typically called with IDs from Focus + Supporting tiers only.
        Increments counter for every unique pair; order-independent.

        Args:
            engram_ids: List of Engram IDs active in Focus + Supporting tiers.
        """
        for i, a in enumerate(engram_ids):
            for b in engram_ids[i + 1 :]:
                key = (min(a, b), max(a, b))
                self._counter[key] = self._counter.get(key, 0) + 1
        self._recall_count += 1

    def pairs_above_threshold(self) -> list[tuple[tuple[str, str], int]]:
        """
        Return all (pair, count) entries where count >= threshold and not yet written.
        """
        return [
            (pair, count)
            for pair, count in self._counter.items()
            if count >= self.threshold and pair not in self._written_pairs
        ]

    def compute_weight(self, count: int) -> float:
        """
        Normalise co-activation count to a link weight in (0, 1].

        weight = min(count / 10, 1.0)
        """
        return min(count / 10.0, self._MAX_WEIGHT)

    def should_flush(self) -> bool:
        """Return True when the periodic flush interval has been reached."""
        return self._recall_count > 0 and self._recall_count % self.flush_every_n == 0

    async def flush_to_neo4j(self, neo4j_client, threshold: int | None = None) -> int:
        """
        Write all eligible pairs (count >= threshold) to Neo4j as CO_ACTIVATED links.

        Uses MERGE so existing links are strengthened rather than duplicated.
        No-op if neo4j_client is None (graceful degradation for unit tests / no-neo4j envs).

        Args:
            neo4j_client: Connected Neo4jEngineClient, or None.
            threshold: Override threshold for this flush (default: self.threshold).

        Returns:
            Number of pairs written.
        """
        if neo4j_client is None:
            return 0

        effective_threshold = threshold if threshold is not None else self.threshold
        from ..retain.link_creation import create_co_activation_link

        written = 0
        for pair, count in list(self._counter.items()):
            if count < effective_threshold:
                continue
            if pair in self._written_pairs:
                continue
            from_id, to_id = pair
            weight = self.compute_weight(count)
            await create_co_activation_link(neo4j_client, from_id, to_id, weight)
            self._written_pairs.add(pair)
            written += 1
            logger.debug(
                "[CoActivationTracker] CO_ACTIVATED link: %s ↔ %s (count=%d, weight=%.2f)",
                from_id,
                to_id,
                count,
                weight,
            )

        return written

    def reset(self) -> None:
        """Clear all counters and state (call at session end after flush)."""
        self._counter.clear()
        self._written_pairs.clear()
        self._recall_count = 0
