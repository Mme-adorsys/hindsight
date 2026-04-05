"""
Working Context — Transient PFC-equivalent workspace for active agent tasks.

Holds references to active Engrams (not copies), manages goals, collects running
inferences. Everything is transient: created at session start, discarded at session
end. Relevant contents flow into the Engram system via the Retain pipeline.

Bio mapping:
- WorkingContext   → Prefrontal Cortex (PFC) — short-term workspace, not storage
- Goal Stack       → Goal-directed attention (top-down PFC control)
- Active Engrams   → 3-tier activation gradient (focus/supporting/peripheral)
- Episodic Buffer  → Hippocampal short-term episodic binding
- Inference Layer  → Tentative predictions under construction

Concept reference: docs/engram/concept.md — Chapter 9 (Working Context)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from ..response_models import Episode

# ---------------------------------------------------------------------------
# Capacity limits (bio: PFC working-memory slot counts)
# ---------------------------------------------------------------------------
MAX_FOCUS = 5
MAX_SUPPORTING = 10
MAX_PERIPHERAL = 20


# ---------------------------------------------------------------------------
# Goal — T1
# ---------------------------------------------------------------------------
@dataclass
class Goal:
    """
    A single agent goal with priority, status, and optional parent for hierarchical goals.

    Bio mapping: PFC top-down goal representation — goals are maintained in
    prefrontal working memory and modulate which engrams get activated.
    """

    id: str
    description: str
    priority: float
    status: Literal["active", "completed", "abandoned"]
    created_at: datetime
    parent_goal_id: str | None = None


# ---------------------------------------------------------------------------
# EngramRef — T2
# ---------------------------------------------------------------------------
@dataclass
class EngramRef:
    """
    Lightweight reference to an Engram held in the Working Context.

    Stores only the engram ID and activation metadata — not the full Engram
    object — to keep the workspace lean.

    Bio mapping: PFC holds pointers to hippocampal/neocortical representations,
    not copies of them.
    """

    engram_id: str
    strength: float
    relevance_score: float
    activated_at: datetime


# ---------------------------------------------------------------------------
# ActiveEngrams — T2
# ---------------------------------------------------------------------------
@dataclass
class ActiveEngrams:
    """
    3-tier activation gradient for currently relevant Engrams.

    - focus:      Directly relevant (3–5 slots)  — highest activation
    - supporting: Context-providing (5–10 slots)  — medium activation
    - peripheral: Weakly activated (10–20 slots)  — low activation

    Bio mapping: Graded cortical activation — hotspot (focus) surrounded by
    decreasing activation fields (supporting, peripheral).
    """

    focus: list[EngramRef] = field(default_factory=list)
    supporting: list[EngramRef] = field(default_factory=list)
    peripheral: list[EngramRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Inference — T3
# ---------------------------------------------------------------------------
@dataclass
class Inference:
    """
    A running hypothesis derived from active Engrams.

    Inferences are tentative: they can be confirmed or rejected by new evidence.
    Confirmed inferences flow into the Retain pipeline at session end.

    Bio mapping: PFC predictive coding — the brain continuously generates
    hypotheses that are either confirmed or suppressed by incoming evidence.
    """

    id: str
    content: str
    confidence: float
    supporting_engram_ids: list[str]
    created_at: datetime
    status: Literal["tentative", "confirmed", "rejected"]


# ---------------------------------------------------------------------------
# WorkingContext — T4 + T5
# ---------------------------------------------------------------------------
@dataclass
class WorkingContext:
    """
    PFC-equivalent workspace for a running agent session.

    Aggregates all four components of active context: goals, engram activations,
    episode history, and inferences. Transient by design.

    Capacity is enforced via push_engram_ref(): if a tier is full, the weakest
    EngramRef is displaced to the next lower tier (or discarded from peripheral).

    Bio mapping: PFC working memory — limited slots, active maintenance,
    rehearsal-dependent persistence.
    """

    session_id: str
    goal_stack: list[Goal] = field(default_factory=list)
    active_engrams: ActiveEngrams = field(default_factory=ActiveEngrams)
    episodic_buffer: list[Episode] = field(default_factory=list)
    inference_layer: list[Inference] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Goal Stack helpers
    # ------------------------------------------------------------------

    def push_goal(self, goal: Goal) -> None:
        """Push a new goal onto the stack and mark last_updated."""
        self.goal_stack.append(goal)
        self.last_updated = datetime.now(UTC)

    def pop_goal(self) -> Goal | None:
        """Pop the top goal from the stack (LIFO). Returns None if empty."""
        if not self.goal_stack:
            return None
        goal = self.goal_stack.pop()
        self.last_updated = datetime.now(UTC)
        return goal

    # ------------------------------------------------------------------
    # Active Engram Capacity Enforcement — T5
    # ------------------------------------------------------------------

    def push_engram_ref(self, tier: Literal["focus", "supporting", "peripheral"], ref: EngramRef) -> None:
        """
        Add an EngramRef to the specified tier with capacity enforcement.

        If the target tier is full, the weakest existing EngramRef (lowest
        relevance_score) is displaced:
        - focus      overflow → moves weakest to supporting
        - supporting overflow → moves weakest to peripheral
        - peripheral overflow → weakest is discarded entirely

        Then inserts the new ref into the (now non-full) tier.
        """
        self._insert_with_capacity(tier, ref)
        self.last_updated = datetime.now(UTC)

    def _insert_with_capacity(self, tier: Literal["focus", "supporting", "peripheral"], ref: EngramRef) -> None:
        tier_list, limit, overflow_tier = self._tier_meta(tier)
        if len(tier_list) < limit:
            tier_list.append(ref)
            return

        # Displace weakest to next tier (or discard from peripheral)
        weakest_idx = min(range(len(tier_list)), key=lambda i: tier_list[i].relevance_score)
        displaced = tier_list.pop(weakest_idx)
        tier_list.append(ref)

        if overflow_tier is not None:
            self._insert_with_capacity(overflow_tier, displaced)
        # else: peripheral overflow — displaced is discarded

    def _tier_meta(
        self, tier: Literal["focus", "supporting", "peripheral"]
    ) -> tuple[list[EngramRef], int, Literal["supporting", "peripheral"] | None]:
        """Return (tier_list, capacity_limit, overflow_tier_name)."""
        if tier == "focus":
            return self.active_engrams.focus, MAX_FOCUS, "supporting"
        if tier == "supporting":
            return self.active_engrams.supporting, MAX_SUPPORTING, "peripheral"
        return self.active_engrams.peripheral, MAX_PERIPHERAL, None
