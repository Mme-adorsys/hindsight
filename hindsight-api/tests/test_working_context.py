"""
Unit tests for WorkingContext (Epic 08, Stories 01 + 02).

Story 01 — Data Structure:
- Goal push/pop (LIFO stack semantics)
- ActiveEngrams capacity limits
- Tier-overflow: focus full → weakest displaced to supporting
- Peripheral-overflow: weakest is discarded
- Inference lifecycle: tentative → confirmed status change

Story 02 — Population & Tiering:
- populate_from_recall() tiering by score/index
- Reinforcement on repeated recall
- Tier promotion after reinforcement threshold
- Goal-relevance bonus
- Displacement when tiers are full
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


# ---------------------------------------------------------------------------
# Helpers for Story 02 tests
# ---------------------------------------------------------------------------

from hindsight_api.engine.search.types import MergedCandidate, RetrievalResult, ScoredResult  # noqa: E402


def make_scored_result(eid: str, score: float = 0.5, text: str = "fact", context: str = "") -> ScoredResult:
    retrieval = RetrievalResult(id=eid, text=text, fact_type="world", context=context or None)
    candidate = MergedCandidate(retrieval=retrieval, rrf_score=score)
    sr = ScoredResult(candidate=candidate)
    sr.combined_score = score
    sr.engram_strength = 0.8
    return sr


# ---------------------------------------------------------------------------
# Population & Tiering — Story 02
# ---------------------------------------------------------------------------


class TestPopulateFromRecall:
    def test_empty_list_noop(self) -> None:
        wc = make_wc()
        wc.populate_from_recall([])
        assert wc.active_engrams.focus == []

    def test_tiering_by_score_index(self) -> None:
        wc = make_wc()
        # First MAX_FOCUS → focus, next MAX_SUPPORTING → supporting, rest → peripheral
        results = [make_scored_result(f"e{i}", score=1.0 - i * 0.01) for i in range(MAX_FOCUS + MAX_SUPPORTING + 3)]
        wc.populate_from_recall(results)

        assert len(wc.active_engrams.focus) == MAX_FOCUS
        assert len(wc.active_engrams.supporting) == MAX_SUPPORTING
        assert len(wc.active_engrams.peripheral) == 3

        focus_ids = {r.engram_id for r in wc.active_engrams.focus}
        assert all(f"e{i}" in focus_ids for i in range(MAX_FOCUS))

    def test_reinforcement_increases_score(self) -> None:
        wc = make_wc()
        # First call: place e1 in focus
        wc.populate_from_recall([make_scored_result("e1", score=0.8)])
        initial_score = wc.active_engrams.focus[0].relevance_score

        # Second call: reinforce
        wc.populate_from_recall([make_scored_result("e1", score=0.6)])
        reinforced_score = wc.active_engrams.focus[0].relevance_score

        assert reinforced_score > initial_score
        assert reinforced_score == pytest.approx(initial_score + 0.6 * 0.5)

    def test_reinforcement_promotes_peripheral_to_supporting(self) -> None:
        wc = make_wc()
        # Place e_target in peripheral (index >= MAX_FOCUS + MAX_SUPPORTING)
        results = [make_scored_result(f"e{i}", score=0.9 - i * 0.01) for i in range(MAX_FOCUS + MAX_SUPPORTING)]
        results.append(make_scored_result("e_target", score=0.01))
        wc.populate_from_recall(results)

        assert any(r.engram_id == "e_target" for r in wc.active_engrams.peripheral)

        # Reinforce until score crosses supporting threshold (0.4)
        # Starting relevance ≈ 0.01; need += new * 0.5 >= 0.4 → new >= 0.78
        wc.populate_from_recall([make_scored_result("e_target", score=0.9)])
        # Score: 0.01 + 0.9*0.5 = 0.46 ≥ 0.4 → promoted
        assert any(r.engram_id == "e_target" for r in wc.active_engrams.supporting)
        assert not any(r.engram_id == "e_target" for r in wc.active_engrams.peripheral)

    def test_reinforcement_promotes_supporting_to_focus(self) -> None:
        wc = make_wc()
        # Place e_target in supporting
        results = [make_scored_result(f"f{i}", score=0.9) for i in range(MAX_FOCUS)]
        results.append(make_scored_result("e_target", score=0.5))
        wc.populate_from_recall(results)

        assert any(r.engram_id == "e_target" for r in wc.active_engrams.supporting)

        # Reinforce until score crosses focus threshold (0.7)
        # Starting relevance ≈ 0.5; need += new * 0.5 >= 0.7 → new >= 0.4
        wc.populate_from_recall([make_scored_result("e_target", score=0.8)])
        # Score: 0.5 + 0.8*0.5 = 0.9 ≥ 0.7 → promoted to focus
        assert any(r.engram_id == "e_target" for r in wc.active_engrams.focus)
        assert not any(r.engram_id == "e_target" for r in wc.active_engrams.supporting)

    def test_goal_relevance_bonus(self) -> None:
        wc = make_wc()
        goal = make_goal("g1")
        goal.description = "database migration postgres"
        wc.push_goal(goal)

        # Matching result: contains "database" from goal description
        matching = make_scored_result("e_match", score=0.5, text="database schema updated")
        # Non-matching result
        non_matching = make_scored_result("e_no", score=0.5, text="unrelated topic here today")

        wc.populate_from_recall([matching, non_matching], active_goal=goal)

        match_ref = next(r for r in wc.active_engrams.focus if r.engram_id == "e_match")
        no_ref = next(r for r in wc.active_engrams.focus if r.engram_id == "e_no")

        assert match_ref.relevance_score == pytest.approx(0.5 + 0.2)
        assert no_ref.relevance_score == pytest.approx(0.5)

    def test_goal_bonus_uses_top_active_goal_from_stack(self) -> None:
        wc = make_wc()
        goal = make_goal("g1")
        goal.description = "postgres migration schema"
        wc.push_goal(goal)

        result = make_scored_result("e1", score=0.4, text="postgres database update")
        wc.populate_from_recall([result])  # no active_goal passed → uses stack

        ref = wc.active_engrams.focus[0]
        assert ref.relevance_score == pytest.approx(0.4 + 0.2)

    def test_displacement_on_full_tiers(self) -> None:
        wc = make_wc()
        # Fill all three tiers
        results = [make_scored_result(f"e{i}", score=1.0 - i * 0.01) for i in range(MAX_FOCUS + MAX_SUPPORTING + MAX_PERIPHERAL)]
        wc.populate_from_recall(results)

        assert len(wc.active_engrams.focus) == MAX_FOCUS
        assert len(wc.active_engrams.supporting) == MAX_SUPPORTING
        assert len(wc.active_engrams.peripheral) == MAX_PERIPHERAL

        # Add one more strong result — should displace weakest
        wc.populate_from_recall([make_scored_result("e_new_strong", score=2.0)])
        focus_ids = {r.engram_id for r in wc.active_engrams.focus}
        assert "e_new_strong" in focus_ids


# ---------------------------------------------------------------------------
# Lifecycle & Decay — Story 03
# ---------------------------------------------------------------------------


class TestDecay:
    def test_decay_reduces_score(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("focus", make_ref("e1", relevance=1.0))
        wc.apply_decay(elapsed_minutes=1.0, mode="precision")
        assert wc.active_engrams.focus[0].relevance_score < 1.0

    def test_decay_factor_precision_faster_than_exploration(self) -> None:
        wc_p = make_wc()
        wc_e = make_wc()
        wc_p.push_engram_ref("focus", make_ref("e1", relevance=1.0))
        wc_e.push_engram_ref("focus", make_ref("e1", relevance=1.0))

        wc_p.apply_decay(elapsed_minutes=5.0, mode="precision")
        wc_e.apply_decay(elapsed_minutes=5.0, mode="exploration")

        assert wc_p.active_engrams.focus[0].relevance_score < wc_e.active_engrams.focus[0].relevance_score

    def test_decay_updates_last_updated(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("focus", make_ref("e1", relevance=1.0))
        before = wc.last_updated
        wc.apply_decay(elapsed_minutes=1.0)
        assert wc.last_updated >= before

    def test_decay_unknown_mode_uses_default(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("focus", make_ref("e1", relevance=1.0))
        # Should not raise
        wc.apply_decay(elapsed_minutes=1.0, mode="unknown_mode")
        assert wc.active_engrams.focus[0].relevance_score < 1.0


class TestTierDemotion:
    def test_focus_demoted_to_supporting_below_threshold(self) -> None:
        wc = make_wc()
        # Relevance 0.3 < FOCUS_DEMOTE_THRESHOLD (0.5) → demoted after decay
        wc.push_engram_ref("focus", make_ref("e1", relevance=0.51))
        # Apply enough decay to cross threshold
        wc.apply_decay(elapsed_minutes=20.0, mode="precision")  # 0.51 * 0.9^20 ≈ 0.087

        assert not any(r.engram_id == "e1" for r in wc.active_engrams.focus)

    def test_supporting_demoted_to_peripheral_below_threshold(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("supporting", make_ref("e1", relevance=0.31))
        wc.apply_decay(elapsed_minutes=15.0, mode="precision")  # 0.31 * 0.9^15 ≈ 0.076

        assert not any(r.engram_id == "e1" for r in wc.active_engrams.supporting)

    def test_peripheral_discarded_below_threshold(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("peripheral", make_ref("e1", relevance=0.11))
        wc.apply_decay(elapsed_minutes=30.0, mode="precision")  # 0.11 * 0.9^30 ≈ 0.0052

        all_ids = (
            {r.engram_id for r in wc.active_engrams.focus}
            | {r.engram_id for r in wc.active_engrams.supporting}
            | {r.engram_id for r in wc.active_engrams.peripheral}
        )
        assert "e1" not in all_ids

    def test_high_relevance_stays_in_focus(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("focus", make_ref("e1", relevance=0.9))
        wc.apply_decay(elapsed_minutes=1.0, mode="precision")  # 0.9 * 0.9 = 0.81 ≥ 0.5

        assert any(r.engram_id == "e1" for r in wc.active_engrams.focus)


class TestFlush:
    def test_flush_empty_context_returns_empty(self) -> None:
        wc = make_wc()
        assert wc.flush() == []

    def test_flush_confirmed_inference_included(self) -> None:
        wc = make_wc()
        wc.inference_layer.append(
            Inference(
                id="i1",
                content="The system will retry on failure",
                confidence=0.9,
                supporting_engram_ids=[],
                created_at=datetime.now(UTC),
                status="confirmed",
            )
        )
        items = wc.flush()
        assert any(item["context"] == "inferred" for item in items)
        contents = [item["content"] for item in items]
        assert any("retry on failure" in c for c in contents)

    def test_flush_tentative_inference_excluded(self) -> None:
        wc = make_wc()
        wc.inference_layer.append(
            Inference(
                id="i1",
                content="Maybe this will happen",
                confidence=0.4,
                supporting_engram_ids=[],
                created_at=datetime.now(UTC),
                status="tentative",
            )
        )
        items = wc.flush()
        assert not any(item.get("context") == "inferred" for item in items)

    def test_flush_completed_goal_included(self) -> None:
        wc = make_wc()
        goal = make_goal("g1")
        goal.description = "Migrate database schema"
        goal.status = "completed"
        wc.push_goal(goal)

        items = wc.flush()
        assert any(item["context"] == "episode" for item in items)
        assert any("Migrate database schema" in item["content"] for item in items)

    def test_flush_active_goal_excluded(self) -> None:
        wc = make_wc()
        goal = make_goal("g1")
        goal.status = "active"
        wc.push_goal(goal)

        items = wc.flush()
        assert not any(item.get("context") == "episode" for item in items)

    def test_flush_focus_engrams_included(self) -> None:
        wc = make_wc()
        wc.push_engram_ref("focus", make_ref("e_focus", relevance=0.8))
        wc.push_engram_ref("supporting", make_ref("e_supporting", relevance=0.5))

        items = wc.flush()
        access_items = [i for i in items if i.get("context") == "access"]
        assert len(access_items) == 1
        assert access_items[0]["metadata"]["engram_id"] == "e_focus"

    def test_flush_metadata_confidence(self) -> None:
        wc = make_wc()
        wc.inference_layer.append(
            Inference(
                id="i1",
                content="Confirmed fact",
                confidence=0.85,
                supporting_engram_ids=[],
                created_at=datetime.now(UTC),
                status="confirmed",
            )
        )
        items = wc.flush()
        inferred = [i for i in items if i.get("context") == "inferred"]
        assert inferred[0]["metadata"]["confidence"] == "0.85"
