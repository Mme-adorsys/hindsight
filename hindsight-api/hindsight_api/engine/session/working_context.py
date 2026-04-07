"""
Working Context — Persistent PFC-equivalent workspace for active agent tasks.

Holds references to active Engrams (not copies), manages goals, and stores
confirmed inferences. This is the PERSISTENT half of the working memory split
introduced in Epic 18: transient session data (episodic buffer, pending
inferences, co-activation counts) lives in SessionCache instead.

Split model (Epic 18):
- WorkingContext  → persistent PFC state (survives session end, stored in PostgreSQL)
- SessionCache   → transient hippocampal buffer (flushed at session end, not persisted)

Bio mapping:
- WorkingContext        → Prefrontal Cortex (PFC) — durable goal/context representation
- Goal Stack            → Goal-directed attention (top-down PFC control)
- Active Engrams        → 3-tier activation gradient (focus/supporting/peripheral)
- Confirmed Inferences → Stable PFC beliefs carried forward across sessions

Concept reference: docs/engram/concept.md — Chapter 9 (Working Context)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Literal

from ..retain.types import RetainContentDict

if TYPE_CHECKING:
    from ..search.types import ScoredResult

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
    confirmed_inferences: list[Inference] = field(default_factory=list)
    """
    Inferences that survived the session and were confirmed.
    Populated at flush time from SessionCache.pending_inferences (confirmed + tentative).
    Persisted in WorkingMemory (Story 02) to survive across sessions.

    Bio mapping: PFC long-term beliefs — hypotheses that were reinforced by evidence
    become stable representations carried forward into the next cognitive context.
    """
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Goal Stack helpers
    # ------------------------------------------------------------------

    def push_goal(self, goal: Goal) -> None:
        """Push a new goal onto the stack and mark last_updated.

        Raises ValueError if the goal creates a cycle in the parent chain.
        """
        # Fix 10: Walk the parent chain at push time to detect cycles.
        if goal.parent_goal_id is not None:
            visited: set[str] = {goal.id}
            current_id: str | None = goal.parent_goal_id
            while current_id is not None:
                if current_id in visited:
                    raise ValueError(f"Goal cycle detected: {goal.id!r} creates cycle via {current_id!r}")
                visited.add(current_id)
                parent = next((g for g in self.goal_stack if g.id == current_id), None)
                current_id = parent.parent_goal_id if parent else None
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

        # Displace weakest to next tier (or discard from peripheral).
        # Fix 7: tiebreak by activated_at so the oldest unrefreshed trace is displaced first.
        # Bio-correct: older representations that haven't been rehearsed fade preferentially.
        weakest_idx = min(
            range(len(tier_list)),
            key=lambda i: (tier_list[i].relevance_score, tier_list[i].activated_at),
        )
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

    # ------------------------------------------------------------------
    # Population from Recall — T1 + T2 + T3 + T4 (Story 02)
    # ------------------------------------------------------------------

    # Thresholds for tier promotion after reinforcement
    _PROMOTE_TO_FOCUS_THRESHOLD: ClassVar[float] = 0.7
    _PROMOTE_TO_SUPPORTING_THRESHOLD: ClassVar[float] = 0.4

    def populate_from_recall(
        self,
        results: list[ScoredResult],
        active_goal: Goal | None = None,
    ) -> None:
        """
        Ingest recall results into the Working Context activation tiers.

        Called after every recall_async() to keep the workspace current.

        Algorithm:
        1. Results arrive pre-sorted by combined_score (descending) from the engine.
        2. For each result:
           - If already in context → Reinforcement: boost relevance_score, maybe promote tier.
           - If new → apply Goal-relevance bonus, then insert via push_engram_ref()
             targeting the tier implied by the result's sorted position.
        3. Capacity enforcement (overflow/displacement) is handled by push_engram_ref().

        Bio mapping: Repeated retrieval of an Engram strengthens its PFC representation
        (rehearsal effect). Novel Engrams enter at a tier matching their retrieval rank.

        Args:
            results: ScoredResult list, pre-sorted descending by combined_score.
            active_goal: If provided, Engrams matching goal keywords receive +0.2 bonus.
                         Falls back to top active goal from goal_stack when None.
        """
        if not results:
            return

        goal = active_goal if active_goal is not None else self._top_active_goal()

        for idx, sr in enumerate(results):
            existing = self._find_existing(sr.id)
            if existing is not None:
                self._reinforce(existing, sr.combined_score)
            else:
                score = sr.combined_score + self._goal_bonus(sr, goal)
                target_tier = self._tier_for_index(idx)
                ref = EngramRef(
                    engram_id=sr.id,
                    strength=sr.engram_strength,
                    relevance_score=score,
                    activated_at=datetime.now(UTC),
                )
                self.push_engram_ref(target_tier, ref)

        self.last_updated = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_existing(
        self, engram_id: str
    ) -> tuple[Literal["focus", "supporting", "peripheral"], EngramRef, int] | None:
        """Search all tiers for an existing EngramRef. Returns (tier, ref, index) or None."""
        for tier_name, tier_list in (
            ("focus", self.active_engrams.focus),
            ("supporting", self.active_engrams.supporting),
            ("peripheral", self.active_engrams.peripheral),
        ):
            for i, ref in enumerate(tier_list):
                if ref.engram_id == engram_id:
                    return tier_name, ref, i  # type: ignore[return-value]
        return None

    def _reinforce(
        self,
        existing: tuple[Literal["focus", "supporting", "peripheral"], EngramRef, int],
        new_score: float,
    ) -> None:
        """
        Boost the relevance_score of an already-active EngramRef and promote if threshold met.

        Reinforcement factor 0.5: each re-retrieval adds half the new score.
        Promotion thresholds: supporting→focus at ≥0.7, peripheral→supporting at ≥0.4.
        """
        tier_name, ref, idx = existing
        ref.relevance_score += new_score * 0.5

        # Check promotion
        if tier_name == "supporting" and ref.relevance_score >= self._PROMOTE_TO_FOCUS_THRESHOLD:
            self.active_engrams.supporting.pop(idx)
            self.push_engram_ref("focus", ref)
        elif tier_name == "peripheral" and ref.relevance_score >= self._PROMOTE_TO_SUPPORTING_THRESHOLD:
            self.active_engrams.peripheral.pop(idx)
            self.push_engram_ref("supporting", ref)

    def _goal_bonus(self, sr: ScoredResult, goal: Goal | None) -> float:
        """
        Return +0.2 if the result matches the active goal via keyword overlap, else 0.0.

        Matching: any word (≥4 chars) from the goal description found in the retrieval
        text or context (case-insensitive). No LLM call — pure string heuristic.
        """
        if goal is None:
            return 0.0
        goal_words = {w.lower() for w in goal.description.split() if len(w) >= 4}
        if not goal_words:
            return 0.0
        haystack = (sr.retrieval.text + " " + (sr.retrieval.context or "")).lower()
        return 0.2 if goal_words & set(haystack.split()) else 0.0

    def _top_active_goal(self) -> Goal | None:
        """Return the topmost active goal from the goal stack, or None."""
        for goal in reversed(self.goal_stack):
            if goal.status == "active":
                return goal
        return None

    @staticmethod
    def _tier_for_index(idx: int) -> Literal["focus", "supporting", "peripheral"]:
        """Map a sorted-result index to the target tier."""
        if idx < MAX_FOCUS:
            return "focus"
        if idx < MAX_FOCUS + MAX_SUPPORTING:
            return "supporting"
        return "peripheral"

    # ------------------------------------------------------------------
    # Lifecycle & Decay — T1 + T2 + T3 (Story 03)
    # ------------------------------------------------------------------

    # Mode-dependent decay factors (fraction of score retained per minute).
    # Precision decays fastest (tight focus), Exploration slowest (broad context).
    _DECAY_FACTOR: ClassVar[dict[str, float]] = {
        "precision": 0.90,
        "exploration": 0.97,
        "analogy": 0.95,
        "validation": 0.95,
    }
    _DEFAULT_DECAY_FACTOR: ClassVar[float] = 0.95

    # Tier-demotion thresholds (relevance_score below → demote to next tier / discard)
    _FOCUS_DEMOTE_THRESHOLD: ClassVar[float] = 0.5
    _SUPPORTING_DEMOTE_THRESHOLD: ClassVar[float] = 0.3
    _PERIPHERAL_DISCARD_THRESHOLD: ClassVar[float] = 0.1

    def apply_decay(self, elapsed_minutes: float, mode: str = "precision") -> None:
        """
        Apply time-based decay to all EngramRefs and demote those that fall below thresholds.

        Each minute, relevance_score is multiplied by decay_factor (mode-dependent).
        After scoring, tiers are rebalanced via _apply_demotion().

        Bio mapping: PFC working-memory maintenance — representations that are not
        actively rehearsed lose activation and eventually fall out of working memory.

        Args:
            elapsed_minutes: Time elapsed since last decay application.
            mode: Session mode string ('precision', 'exploration', 'analogy', 'validation').
        """
        factor = self._DECAY_FACTOR.get(mode, self._DEFAULT_DECAY_FACTOR) ** elapsed_minutes
        for ref in (
            *self.active_engrams.focus,
            *self.active_engrams.supporting,
            *self.active_engrams.peripheral,
        ):
            ref.relevance_score = max(ref.relevance_score * factor, 0.0)  # Fix 12: explicit floor

        self._apply_demotion()
        self.last_updated = datetime.now(UTC)

    def _apply_demotion(self) -> None:
        """
        Demote or discard EngramRefs whose relevance_score has fallen below tier thresholds.

        Demotion order (to avoid cascades within one call):
        1. Focus → Supporting if score < FOCUS_DEMOTE_THRESHOLD
        2. Supporting → Peripheral if score < SUPPORTING_DEMOTE_THRESHOLD
        3. Peripheral → discard if score < PERIPHERAL_DISCARD_THRESHOLD
        """
        # Focus → Supporting
        stay_focus: list[EngramRef] = []
        demote_to_supporting: list[EngramRef] = []
        for ref in self.active_engrams.focus:
            if ref.relevance_score < self._FOCUS_DEMOTE_THRESHOLD:
                demote_to_supporting.append(ref)
            else:
                stay_focus.append(ref)
        self.active_engrams.focus = stay_focus

        # Supporting → Peripheral (including newly demoted from focus)
        stay_supporting: list[EngramRef] = []
        demote_to_peripheral: list[EngramRef] = []
        for ref in [*self.active_engrams.supporting, *demote_to_supporting]:
            if ref.relevance_score < self._SUPPORTING_DEMOTE_THRESHOLD:
                demote_to_peripheral.append(ref)
            else:
                stay_supporting.append(ref)
        self.active_engrams.supporting = stay_supporting

        # Peripheral → discard (including newly demoted from supporting)
        stay_peripheral: list[EngramRef] = []
        for ref in [*self.active_engrams.peripheral, *demote_to_peripheral]:
            if ref.relevance_score >= self._PERIPHERAL_DISCARD_THRESHOLD:
                stay_peripheral.append(ref)
        self.active_engrams.peripheral = stay_peripheral

    def flush(self) -> list[RetainContentDict]:
        """
        Collect Working Context contents worth persisting at session end.

        Returns RetainContentDict items for:
        1. Confirmed Inferences → retained as 'inferred' context facts
        2. Completed Goals → retained as episodic entries
        3. Focus-tier Engrams → access-count bookkeeping note (lightweight)

        The caller is responsible for passing the returned list to retain_batch_async().
        Working Context itself is discarded by the caller after flush().

        Bio mapping: Session end = slow-wave sleep onset — salient activations
        are replayed and transferred to long-term storage (neocortex).
        """
        items: list[RetainContentDict] = []

        # 1. Confirmed inferences → retain as facts
        # (confirmed_inferences are populated at flush time from SessionCache)
        for inf in self.confirmed_inferences:
            item: RetainContentDict = {
                "content": inf.content,
                "context": "inferred",
                "metadata": {"confidence": str(round(inf.confidence, 3)), "inference_id": inf.id},
            }
            items.append(item)

        # 2. Completed goals → retain as episodic entries
        for goal in self.goal_stack:
            if goal.status == "completed":
                item = {
                    "content": f"Completed goal: {goal.description}",
                    "context": "episode",
                    "metadata": {"goal_id": goal.id, "priority": str(round(goal.priority, 3))},
                }
                items.append(item)

        # 3. Focus engrams → lightweight access-count note (no full content needed)
        for ref in self.active_engrams.focus:
            if not ref.engram_id or not ref.engram_id.strip():  # Fix 14: skip blank ids
                continue
            item = {
                "content": f"Engram {ref.engram_id} was active in focus during this session.",
                "context": "access",
                "metadata": {
                    "engram_id": ref.engram_id,
                    "relevance_score": str(round(ref.relevance_score, 3)),
                },
            }
            items.append(item)

        return items


# ---------------------------------------------------------------------------
# Fix 8 — Module-level invariant guard: promote threshold must exceed demote threshold
# per tier, otherwise a ref oscillates (promoted then immediately demoted in same cycle).
# These are class-level constants; catching drift at import time prevents silent bugs.
# ---------------------------------------------------------------------------
assert WorkingContext._PROMOTE_TO_FOCUS_THRESHOLD > WorkingContext._FOCUS_DEMOTE_THRESHOLD, (
    f"Focus promote ({WorkingContext._PROMOTE_TO_FOCUS_THRESHOLD}) must exceed "
    f"demote ({WorkingContext._FOCUS_DEMOTE_THRESHOLD}) threshold"
)
assert WorkingContext._PROMOTE_TO_SUPPORTING_THRESHOLD > WorkingContext._SUPPORTING_DEMOTE_THRESHOLD, (
    f"Supporting promote ({WorkingContext._PROMOTE_TO_SUPPORTING_THRESHOLD}) must exceed "
    f"demote ({WorkingContext._SUPPORTING_DEMOTE_THRESHOLD}) threshold"
)
