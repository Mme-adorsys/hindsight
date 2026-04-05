"""
Unit tests for AssociationWindow (STC) — Epic 09 Story 03 T5.

Covers:
- check_associations: pairs within window detected
- check_associations: pairs outside window ignored
- Symmetry: (A,B) == (B,A) normalization
- _recall_count incremented on each check
- should_flush: periodic trigger
- flush_to_neo4j: no-op when neo4j_client is None
- flush_to_neo4j: writes pairs with correct initial weight
- Weight strengthening on re-association: INITIAL + (count-1)*INCREMENT
- Weight cap at MAX_LINK_WEIGHT (0.5)
- reset: clears all state
- WorkingContext integration: association_window field present
- Distinctness from CoActivationTracker: different mechanism and link type
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hindsight_api.engine.session.association_window import (
    DEFAULT_CHECK_EVERY_N,
    DEFAULT_WINDOW_MINUTES,
    INITIAL_LINK_WEIGHT,
    MAX_LINK_WEIGHT,
    WEIGHT_INCREMENT,
    AssociationWindow,
    _time_delta_minutes,
)
from hindsight_api.engine.session.working_context import (
    ActiveEngrams,
    EngramRef,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ref(engram_id: str, minutes_ago: float = 0.0) -> EngramRef:
    """Create an EngramRef with activated_at = now - minutes_ago."""
    activated_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return EngramRef(
        engram_id=engram_id,
        strength=0.8,
        relevance_score=0.7,
        activated_at=activated_at,
    )


def _engrams(focus: list[EngramRef], supporting: list[EngramRef] | None = None) -> ActiveEngrams:
    ae = ActiveEngrams()
    ae.focus = focus
    ae.supporting = supporting or []
    return ae


# ---------------------------------------------------------------------------
# _time_delta_minutes helper
# ---------------------------------------------------------------------------


class TestTimeDeltaMinutes:
    def test_same_time_is_zero(self) -> None:
        now = datetime.now(UTC)
        assert _time_delta_minutes(now, now) == pytest.approx(0.0)

    def test_one_minute_apart(self) -> None:
        a = datetime.now(UTC)
        b = a + timedelta(minutes=1)
        assert _time_delta_minutes(a, b) == pytest.approx(1.0)

    def test_absolute_value(self) -> None:
        a = datetime.now(UTC)
        b = a - timedelta(minutes=3)
        assert _time_delta_minutes(a, b) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# check_associations
# ---------------------------------------------------------------------------


class TestCheckAssociations:
    def test_pair_within_window_detected(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 2)])  # 2 min apart
        pairs = aw.check_associations(ae)
        assert ("a", "b") in pairs

    def test_pair_outside_window_ignored(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 10)])  # 10 min apart
        pairs = aw.check_associations(ae)
        assert pairs == []

    def test_pair_exactly_at_boundary_included(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 5)])  # exactly 5 min
        pairs = aw.check_associations(ae)
        assert ("a", "b") in pairs

    def test_symmetry_normalised_to_min_max(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        # "b" before "a" alphabetically → stored as ("a", "b") regardless of order
        ae = _engrams(focus=[_ref("b", 0), _ref("a", 1)])
        pairs = aw.check_associations(ae)
        assert ("a", "b") in pairs
        assert ("b", "a") not in pairs

    def test_single_engram_produces_no_pairs(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("solo")])
        pairs = aw.check_associations(ae)
        assert pairs == []

    def test_empty_active_engrams_produces_no_pairs(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[])
        pairs = aw.check_associations(ae)
        assert pairs == []

    def test_focus_and_supporting_combined(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0)], supporting=[_ref("b", 1)])
        pairs = aw.check_associations(ae)
        assert ("a", "b") in pairs

    def test_peripheral_not_included(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0)])
        ae.peripheral = [_ref("p", 0)]  # same time, but peripheral
        pairs = aw.check_associations(ae)
        assert pairs == []

    def test_three_refs_two_within_window(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 2), _ref("c", 10)])
        pairs = aw.check_associations(ae)
        assert ("a", "b") in pairs
        # a and c are 10 min apart → excluded
        assert ("a", "c") not in pairs
        assert ("b", "c") not in pairs

    def test_recall_count_incremented(self) -> None:
        aw = AssociationWindow()
        aw.check_associations(_engrams(focus=[]))
        aw.check_associations(_engrams(focus=[]))
        assert aw._recall_count == 2

    def test_pair_count_accumulated(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
        aw.check_associations(ae)
        aw.check_associations(ae)
        assert aw._pair_counts[("a", "b")] == 2


# ---------------------------------------------------------------------------
# should_flush
# ---------------------------------------------------------------------------


class TestShouldFlush:
    def test_no_checks_no_flush(self) -> None:
        aw = AssociationWindow(check_every_n=5)
        assert aw.should_flush() is False

    def test_flush_at_interval(self) -> None:
        aw = AssociationWindow(check_every_n=5)
        for _ in range(5):
            aw.check_associations(_engrams(focus=[]))
        assert aw.should_flush() is True

    def test_not_flush_before_interval(self) -> None:
        aw = AssociationWindow(check_every_n=5)
        for _ in range(4):
            aw.check_associations(_engrams(focus=[]))
        assert aw.should_flush() is False

    def test_default_check_every_n(self) -> None:
        assert DEFAULT_CHECK_EVERY_N == 5


# ---------------------------------------------------------------------------
# flush_to_neo4j
# ---------------------------------------------------------------------------


class TestFlushToNeo4j:
    @pytest.mark.asyncio
    async def test_noop_when_neo4j_is_none(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
        aw.check_associations(ae)
        written = await aw.flush_to_neo4j(None)
        assert written == 0

    @pytest.mark.asyncio
    async def test_writes_pair_with_initial_weight(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_association_window_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_association_window_link = fake_link
        try:
            aw = AssociationWindow(window_minutes=5.0)
            ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
            aw.check_associations(ae)
            written = await aw.flush_to_neo4j(FakeNeo4j())
            assert written == 1
            assert len(calls) == 1
            assert calls[0][0] == "a"
            assert calls[0][1] == "b"
            assert calls[0][2] == pytest.approx(INITIAL_LINK_WEIGHT)
        finally:
            lc.create_association_window_link = original

    @pytest.mark.asyncio
    async def test_weight_strengthens_with_count(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_association_window_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_association_window_link = fake_link
        try:
            aw = AssociationWindow(window_minutes=5.0)
            ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
            # 3 checks → count=3 → weight = 0.1 + 2*0.05 = 0.2
            for _ in range(3):
                aw.check_associations(ae)
            await aw.flush_to_neo4j(FakeNeo4j())
            expected = INITIAL_LINK_WEIGHT + 2 * WEIGHT_INCREMENT
            assert calls[0][2] == pytest.approx(expected)
        finally:
            lc.create_association_window_link = original

    @pytest.mark.asyncio
    async def test_weight_capped_at_max(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_association_window_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_association_window_link = fake_link
        try:
            aw = AssociationWindow(window_minutes=5.0)
            ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
            # Many checks — weight should cap at MAX_LINK_WEIGHT
            for _ in range(100):
                aw.check_associations(ae)
            await aw.flush_to_neo4j(FakeNeo4j())
            assert calls[0][2] <= MAX_LINK_WEIGHT
            assert calls[0][2] == pytest.approx(MAX_LINK_WEIGHT)
        finally:
            lc.create_association_window_link = original

    @pytest.mark.asyncio
    async def test_no_pairs_no_writes(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_association_window_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_association_window_link = fake_link
        try:
            aw = AssociationWindow(window_minutes=5.0)
            # No checks done → no pairs
            written = await aw.flush_to_neo4j(FakeNeo4j())
            assert written == 0
            assert calls == []
        finally:
            lc.create_association_window_link = original


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_pair_counts(self) -> None:
        aw = AssociationWindow(window_minutes=5.0)
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 1)])
        aw.check_associations(ae)
        aw.reset()
        assert aw._pair_counts == {}

    def test_reset_clears_written_pairs(self) -> None:
        aw = AssociationWindow()
        aw._written_pairs.add(("a", "b"))
        aw.reset()
        assert aw._written_pairs == set()

    def test_reset_clears_recall_count(self) -> None:
        aw = AssociationWindow()
        aw.check_associations(_engrams(focus=[]))
        aw.reset()
        assert aw._recall_count == 0


# ---------------------------------------------------------------------------
# WorkingContext integration
# ---------------------------------------------------------------------------


class TestWorkingContextIntegration:
    def test_working_context_has_association_window(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc = WorkingContext(session_id="test")
        assert isinstance(wc.association_window, AssociationWindow)

    def test_window_isolated_per_instance(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc1 = WorkingContext(session_id="s1")
        wc2 = WorkingContext(session_id="s2")
        wc1.association_window._pair_counts[("a", "b")] = 1
        assert wc2.association_window._pair_counts == {}

    def test_uses_default_window_minutes(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc = WorkingContext(session_id="test")
        assert wc.association_window.window_minutes == DEFAULT_WINDOW_MINUTES


# ---------------------------------------------------------------------------
# Distinctness from CoActivationTracker
# ---------------------------------------------------------------------------


class TestDistinctnessFromCoActivation:
    def test_different_link_types_conceptually(self) -> None:
        """AssociationWindow creates TEMPORAL_PROXIMITY; CoActivationTracker creates CO_ACTIVATED."""
        from hindsight_api.engine.session.co_activation_tracker import CoActivationTracker

        aw = AssociationWindow()
        cat = CoActivationTracker()
        # Both are dataclasses but different types
        assert type(aw) is not type(cat)

    def test_association_window_uses_time_delta(self) -> None:
        """AssociationWindow looks at activated_at timestamps; not recall co-occurrence."""
        aw = AssociationWindow(window_minutes=1.0)
        # Refs activated far apart → no association even if co-present in engrams
        ae = _engrams(focus=[_ref("a", 0), _ref("b", 5)])
        pairs = aw.check_associations(ae)
        assert pairs == []

    def test_co_activation_uses_recall_count(self) -> None:
        """CoActivationTracker increments by track_recall calls, not time."""
        from hindsight_api.engine.session.co_activation_tracker import CoActivationTracker

        cat = CoActivationTracker(threshold=2)
        cat.track_recall(["a", "b"])
        cat.track_recall(["a", "b"])
        assert cat._counter[("a", "b")] == 2
        assert cat.pairs_above_threshold() != []
