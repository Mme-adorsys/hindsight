"""
Session Cache — Transient cache layer for in-session ephemeral data.

Holds data that only lives within a single session and is processed (flushed)
at session end. Completely in-memory — never persisted.

Contents at flush time:
- episodic_buffer     → Retain pipeline (becomes Engrams)
- pending_inferences  → confirmed/tentative → WorkingMemory, rejected → discarded
- co_activation_counts → Neo4j CO_ACTIVATED links

Bio mapping:
- SessionCache → Hippocampal short-term buffer (pre-consolidation)
- episodic_buffer → Episodic memory before slow-wave sleep replay
- pending_inferences → Tentative predictions before outcome confirmation
- co_activation_counts → Hebbian co-firing counts before synapse weight update

Concept reference: docs/engram/concept.md — Chapter 9 (Working Context), Cache Layer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from ..response_models import Episode
from .association_window import AssociationWindow
from .co_activation_tracker import CoActivationTracker
from .working_context import Inference


@dataclass
class SessionCache:
    """
    Transient cache holding ephemeral session data.

    Created at session start (empty), flushed and discarded at session end.
    Never persisted to any database.

    The cache is deliberately kept separate from WorkingContext (the persistent
    PFC-workspace) to make the transient/persistent boundary explicit.
    """

    session_id: str
    episodic_buffer: list[Episode] = field(default_factory=list)
    """Append-only buffer of episodes captured during this session."""

    pending_inferences: list[Inference] = field(default_factory=list)
    """Inferences in progress: tentative → confirmed/rejected during the session."""

    co_activation_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    """
    How often each Engram pair co-appeared in recall results this session.
    Key: (engram_id_a, engram_id_b) sorted lexicographically so (a,b)==(b,a).
    Populated by record_co_activation(); used at flush time for Neo4j batch write.
    """

    co_activation_tracker: CoActivationTracker = field(default_factory=CoActivationTracker, repr=False)
    """
    Active co-activation tracker — counts recall co-occurrences and flushes CO_ACTIVATED
    links to Neo4j when the threshold is reached.
    Bio mapping: Hebbian learning — co-firing neurons form stronger synaptic connections.
    """

    association_window: AssociationWindow = field(default_factory=AssociationWindow, repr=False)
    """
    STC-based temporal proximity tracker — detects Focus+Supporting Engram pairs
    co-active within a time window and writes TEMPORAL_PROXIMITY links.
    Bio mapping: Synaptic Tagging & Capture — temporally proximate activations form weak links.
    """

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Episodic Buffer
    # ------------------------------------------------------------------

    def add_episode(self, episode: Episode) -> None:
        """Append an episode to the buffer (append-only, no removal)."""
        self.episodic_buffer.append(episode)

    # ------------------------------------------------------------------
    # Inference Management
    # ------------------------------------------------------------------

    def add_inference(self, inference: Inference) -> None:
        """Add a new inference (typically status='tentative')."""
        self.pending_inferences.append(inference)

    def update_inference_status(
        self,
        inference_id: str,
        status: Literal["confirmed", "rejected"],
    ) -> None:
        """
        Update the status of an existing inference.

        Raises:
            KeyError: If no inference with the given id exists in the cache.
        """
        for inf in self.pending_inferences:
            if inf.id == inference_id:
                inf.status = status
                return
        raise KeyError(f"Inference {inference_id!r} not found in session cache")

    # ------------------------------------------------------------------
    # Co-Activation Tracking
    # ------------------------------------------------------------------

    def record_co_activation(self, engram_id_a: str, engram_id_b: str) -> None:
        """
        Increment the co-activation count for an Engram pair.

        The pair is stored with lexicographically sorted IDs so that
        (a, b) and (b, a) map to the same counter.
        """
        key = (min(engram_id_a, engram_id_b), max(engram_id_a, engram_id_b))
        self.co_activation_counts[key] = self.co_activation_counts.get(key, 0) + 1

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return True if all buffers are empty (nothing to flush)."""
        return not self.episodic_buffer and not self.pending_inferences and not self.co_activation_counts
