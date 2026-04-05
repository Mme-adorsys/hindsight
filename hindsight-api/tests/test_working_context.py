"""
Unit tests for WorkingContext Data Structure (Epic 08, Story 01).

Tests cover:
- Goal push/pop (LIFO stack semantics)
- ActiveEngrams capacity limits
- Tier-overflow: focus full → weakest displaced to supporting
- Peripheral-overflow: weakest is discarded
- Inference lifecycle: tentative → confirmed status change
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hindsight_api.engine.session.working_context import (
    MAX_FOCUS,
    MAX_PERIPHERAL,
    MAX_SUPPORTING,
    ActiveEngrams,
    EngramRef,
    Goal,
    Inference,
    WorkingContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_goal(gid: str = "g1", priority: float = 1.0, status: str = "active") -> Goal:
    return Goal(
        id=gid,
        description=f"Goal {gid}",
        priority=priority,
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


def make_ref(eid: str = "e1", relevance: float = 0.5) -> EngramRef:
    return EngramRef(
        engram_id=eid,
        strength=0.8,
        relevance_score=relevance,
        activated_at=datetime.now(UTC),
    )


def make_wc(session_id: str = "s1") -> WorkingContext:
    return WorkingContext(session_id=session_id)


# ---------------------------------------------------------------------------
# Goal Stack — T1
# ---------------------------------------------------------------------------

class TestGoalStack:
    def test_push_goal_appends(self) -> None:
        wc = make_wc()
        g = make_goal("g1")
        wc.push_goal(g)
        assert len(wc.goal_stack) == 1
        assert wc.goal_stack[0].id == "g1"

    def test_pop_goal_lifo(self) -> None:
        wc = make_wc()
        g1 = make_goal("g1")
        g2 = make_goal("g2")
        wc.push_goal(g1)
        wc.push_goal(g2)
        popped = wc.pop_goal()
        assert popped is not None
        assert popped.id == "g2"
        assert len(wc.goal_stack) == 1

    def test_pop_empty_returns_none(self) -> None:
        wc = make_wc()
        assert wc.pop_goal() is None

    def test_push_updates_last_updated(self) -> None:
        wc = make_wc()
        before = wc.last_updated
        wc.push_goal(make_goal())
        assert wc.last_updated >= before

    def test_goal_with_parent(self) -> None:
        parent = make_goal("parent")
        child = Goal(
            id="child",
            description="Sub-goal",
            priority=0.5,
            status="active",
            created_at=datetime.now(UTC),
            parent_goal_id="parent",
        )
        wc = make_wc()
        wc.push_goal(parent)
        wc.push_goal(child)
        popped = wc.pop_goal()
        assert popped is not None
        assert popped.parent_goal_id == "parent"


# ---------------------------------------------------------------------------
# ActiveEngrams Capacity Limits — T5
# ---------------------------------------------------------------------------

class TestCapacityLimits:
    def test_focus_capacity_respected(self) -> None:
        wc = make_wc()
        for i in range(MAX_FOCUS):
            wc.push_engram_ref("focus", make_ref(f"e{i}", relevance=float(i)))
        assert len(wc.active_engrams.focus) == MAX_FOCUS

    def test_supporting_capacity_respected(self) -> None:
        wc = make_wc()
        for i in range(MAX_SUPPORTING):
            wc.push_engram_ref("supporting", make_ref(f"e{i}", relevance=float(i)))
        assert len(wc.active_engrams.supporting) == MAX_SUPPORTING

    def test_peripheral_capacity_respected(self) -> None:
        wc = make_wc()
        for i in range(MAX_PERIPHERAL):
            wc.push_engram_ref("peripheral", make_ref(f"e{i}", relevance=float(i)))
        assert len(wc.active_engrams.peripheral) == MAX_PERIPHERAL


# ---------------------------------------------------------------------------
# Tier-Overflow: Focus full → weakest displaced to Supporting — T5
# ---------------------------------------------------------------------------

class TestTierOverflow:
    def test_focus_overflow_displaces_weakest_to_supporting(self) -> None:
        wc = make_wc()
        # Fill focus with refs having relevance 1.0–5.0
        for i in range(1, MAX_FOCUS + 1):
            wc.push_engram_ref("focus", make_ref(f"e{i}", relevance=float(i)))

        # Add a stronger ref — should push out the weakest (relevance=1.0)
        wc.push_engram_ref("focus", make_ref("strong", relevance=10.0))

        assert len(wc.active_engrams.focus) == MAX_FOCUS
        focus_ids = {r.engram_id for r in wc.active_engrams.focus}
        assert "strong" in focus_ids
        assert "e1" not in focus_ids  # weakest displaced

        # Displaced ref must appear in supporting
        supporting_ids = {r.engram_id for r in wc.active_engrams.supporting}
        assert "e1" in supporting_ids

    def test_supporting_overflow_displaces_to_peripheral(self) -> None:
        wc = make_wc()
        for i in range(1, MAX_SUPPORTING + 1):
            wc.push_engram_ref("supporting", make_ref(f"e{i}", relevance=float(i)))

        wc.push_engram_ref("supporting", make_ref("strong", relevance=99.0))

        assert len(wc.active_engrams.supporting) == MAX_SUPPORTING
        peripheral_ids = {r.engram_id for r in wc.active_engrams.peripheral}
        assert "e1" in peripheral_ids  # weakest from supporting → peripheral

    def test_cascade_overflow_focus_to_peripheral(self) -> None:
        """
        Fill focus AND supporting to capacity, then overflow focus.
        The displaced focus ref goes to supporting, which displaces
        its own weakest to peripheral.
        """
        wc = make_wc()
        for i in range(1, MAX_FOCUS + 1):
            wc.push_engram_ref("focus", make_ref(f"f{i}", relevance=float(i)))
        for i in range(1, MAX_SUPPORTING + 1):
            wc.push_engram_ref("supporting", make_ref(f"s{i}", relevance=float(i)))

        wc.push_engram_ref("focus", make_ref("new_focus", relevance=99.0))

        # Peripheral must have received the cascaded displacement
        assert len(wc.active_engrams.peripheral) >= 1


# ---------------------------------------------------------------------------
# Peripheral-Overflow: weakest discarded — T5
# ---------------------------------------------------------------------------

class TestPeripheralOverflow:
    def test_peripheral_overflow_discards_weakest(self) -> None:
        wc = make_wc()
        for i in range(1, MAX_PERIPHERAL + 1):
            wc.push_engram_ref("peripheral", make_ref(f"e{i}", relevance=float(i)))

        # Total slots = MAX_PERIPHERAL; adding one more must discard weakest (e1)
        wc.push_engram_ref("peripheral", make_ref("new", relevance=99.0))

        assert len(wc.active_engrams.peripheral) == MAX_PERIPHERAL
        ids = {r.engram_id for r in wc.active_engrams.peripheral}
        assert "new" in ids
        assert "e1" not in ids  # discarded


# ---------------------------------------------------------------------------
# Inference Lifecycle — T3
# ---------------------------------------------------------------------------

class TestInferenceLifecycle:
    def test_inference_starts_tentative(self) -> None:
        inf = Inference(
            id="i1",
            content="Agent will retry",
            confidence=0.7,
            supporting_engram_ids=["e1", "e2"],
            created_at=datetime.now(UTC),
            status="tentative",
        )
        assert inf.status == "tentative"

    def test_inference_status_confirmed(self) -> None:
        inf = Inference(
            id="i1",
            content="Agent will retry",
            confidence=0.9,
            supporting_engram_ids=["e1"],
            created_at=datetime.now(UTC),
            status="tentative",
        )
        inf.status = "confirmed"
        assert inf.status == "confirmed"

    def test_inference_status_rejected(self) -> None:
        inf = Inference(
            id="i1",
            content="Agent will retry",
            confidence=0.3,
            supporting_engram_ids=[],
            created_at=datetime.now(UTC),
            status="tentative",
        )
        inf.status = "rejected"
        assert inf.status == "rejected"

    def test_working_context_holds_inferences(self) -> None:
        wc = make_wc()
        inf = Inference(
            id="i1",
            content="hypothesis",
            confidence=0.6,
            supporting_engram_ids=["e1"],
            created_at=datetime.now(UTC),
            status="tentative",
        )
        wc.inference_layer.append(inf)
        assert len(wc.inference_layer) == 1
        assert wc.inference_layer[0].id == "i1"


# ---------------------------------------------------------------------------
# WorkingContext basics — T4
# ---------------------------------------------------------------------------

class TestWorkingContextBasics:
    def test_default_construction(self) -> None:
        wc = WorkingContext(session_id="sess-42")
        assert wc.session_id == "sess-42"
        assert wc.goal_stack == []
        assert wc.active_engrams.focus == []
        assert wc.active_engrams.supporting == []
        assert wc.active_engrams.peripheral == []
        assert wc.episodic_buffer == []
        assert wc.inference_layer == []

    def test_push_engram_ref_updates_last_updated(self) -> None:
        wc = make_wc()
        before = wc.last_updated
        wc.push_engram_ref("focus", make_ref())
        assert wc.last_updated >= before
