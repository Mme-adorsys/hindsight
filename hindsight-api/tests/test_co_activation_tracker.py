"""
Unit tests for CoActivationTracker — Epic 09 Story 01 T5.

Covers:
- track_recall: symmetric pair counting
- pairs_above_threshold: filtering
- compute_weight: normalisation
- should_flush: periodic trigger
- flush_to_neo4j: no-op when neo4j_client is None
- flush_to_neo4j: writes eligible pairs and marks them as written
- reset: clears all state
- WorkingContext.co_activation_tracker: field initialised correctly
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.session.co_activation_tracker import (
    DEFAULT_CO_ACTIVATION_THRESHOLD,
    DEFAULT_FLUSH_EVERY_N,
    CoActivationTracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tracker(**kwargs) -> CoActivationTracker:
    return CoActivationTracker(**kwargs)


# ---------------------------------------------------------------------------
# track_recall
# ---------------------------------------------------------------------------


class TestTrackRecall:
    def test_single_pair_incremented(self) -> None:
        t = make_tracker()
        t.track_recall(["a", "b"])
        assert t._counter[("a", "b")] == 1

    def test_symmetry_ab_same_as_ba(self) -> None:
        t = make_tracker()
        t.track_recall(["b", "a"])
        # min/max normalisation → stored as ("a", "b")
        assert t._counter[("a", "b")] == 1

    def test_multiple_recalls_accumulate(self) -> None:
        t = make_tracker()
        for _ in range(4):
            t.track_recall(["x", "y"])
        assert t._counter[("x", "y")] == 4

    def test_three_ids_produce_three_pairs(self) -> None:
        t = make_tracker()
        t.track_recall(["a", "b", "c"])
        assert ("a", "b") in t._counter
        assert ("a", "c") in t._counter
        assert ("b", "c") in t._counter
        assert len(t._counter) == 3

    def test_single_id_produces_no_pairs(self) -> None:
        t = make_tracker()
        t.track_recall(["solo"])
        assert t._counter == {}

    def test_empty_list_is_noop(self) -> None:
        t = make_tracker()
        t.track_recall([])
        assert t._counter == {}
        assert t._recall_count == 1  # count still incremented

    def test_recall_count_incremented(self) -> None:
        t = make_tracker()
        t.track_recall(["a", "b"])
        t.track_recall(["c", "d"])
        assert t._recall_count == 2


# ---------------------------------------------------------------------------
# pairs_above_threshold
# ---------------------------------------------------------------------------


class TestPairsAboveThreshold:
    def test_below_threshold_not_returned(self) -> None:
        t = make_tracker(threshold=3)
        t.track_recall(["a", "b"])
        t.track_recall(["a", "b"])
        assert t.pairs_above_threshold() == []

    def test_at_threshold_returned(self) -> None:
        t = make_tracker(threshold=3)
        for _ in range(3):
            t.track_recall(["a", "b"])
        results = t.pairs_above_threshold()
        assert len(results) == 1
        pair, count = results[0]
        assert pair == ("a", "b")
        assert count == 3

    def test_already_written_excluded(self) -> None:
        t = make_tracker(threshold=2)
        for _ in range(3):
            t.track_recall(["a", "b"])
        t._written_pairs.add(("a", "b"))
        assert t.pairs_above_threshold() == []


# ---------------------------------------------------------------------------
# compute_weight
# ---------------------------------------------------------------------------


class TestComputeWeight:
    def test_low_count_proportional(self) -> None:
        t = make_tracker()
        assert t.compute_weight(5) == pytest.approx(0.5)

    def test_count_at_10_gives_max(self) -> None:
        t = make_tracker()
        assert t.compute_weight(10) == pytest.approx(1.0)

    def test_count_above_10_capped_at_1(self) -> None:
        t = make_tracker()
        assert t.compute_weight(50) == pytest.approx(1.0)

    def test_count_1_gives_0_1(self) -> None:
        t = make_tracker()
        assert t.compute_weight(1) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# should_flush
# ---------------------------------------------------------------------------


class TestShouldFlush:
    def test_no_recalls_no_flush(self) -> None:
        t = make_tracker(flush_every_n=5)
        assert t.should_flush() is False

    def test_flush_at_interval(self) -> None:
        t = make_tracker(flush_every_n=5)
        for _ in range(5):
            t.track_recall(["a", "b"])
        assert t.should_flush() is True

    def test_not_flush_before_interval(self) -> None:
        t = make_tracker(flush_every_n=5)
        for _ in range(4):
            t.track_recall(["a", "b"])
        assert t.should_flush() is False

    def test_flush_at_multiple_of_interval(self) -> None:
        t = make_tracker(flush_every_n=3)
        for _ in range(6):
            t.track_recall(["a", "b"])
        assert t.should_flush() is True

    def test_default_flush_every_n(self) -> None:
        assert DEFAULT_FLUSH_EVERY_N == 5


# ---------------------------------------------------------------------------
# flush_to_neo4j
# ---------------------------------------------------------------------------


class TestFlushToNeo4j:
    @pytest.mark.asyncio
    async def test_noop_when_neo4j_is_none(self) -> None:
        t = make_tracker(threshold=1)
        t.track_recall(["a", "b"])
        written = await t.flush_to_neo4j(None)
        assert written == 0
        assert t._written_pairs == set()

    @pytest.mark.asyncio
    async def test_writes_eligible_pairs(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_co_activation_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_co_activation_link = fake_link
        try:
            t = make_tracker(threshold=2)
            for _ in range(3):
                t.track_recall(["a", "b"])
            written = await t.flush_to_neo4j(FakeNeo4j())
            assert written == 1
            assert len(calls) == 1
            assert calls[0][0] == "a"
            assert calls[0][1] == "b"
            assert calls[0][2] == pytest.approx(0.3)
        finally:
            lc.create_co_activation_link = original

    @pytest.mark.asyncio
    async def test_already_written_pairs_skipped(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_co_activation_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_co_activation_link = fake_link
        try:
            t = make_tracker(threshold=2)
            for _ in range(3):
                t.track_recall(["a", "b"])
            # First flush writes the pair
            await t.flush_to_neo4j(FakeNeo4j())
            # Second flush — pair already written, skip
            written2 = await t.flush_to_neo4j(FakeNeo4j())
            assert written2 == 0
            assert len(calls) == 1
        finally:
            lc.create_co_activation_link = original

    @pytest.mark.asyncio
    async def test_threshold_override(self) -> None:
        calls: list[tuple] = []

        class FakeNeo4j:
            pass

        import hindsight_api.engine.retain.link_creation as lc

        original = lc.create_co_activation_link

        async def fake_link(client, from_id, to_id, weight):
            calls.append((from_id, to_id, weight))

        lc.create_co_activation_link = fake_link
        try:
            t = make_tracker(threshold=5)
            for _ in range(2):
                t.track_recall(["a", "b"])
            # Default threshold=5 → no flush; but override threshold=1 → writes
            written = await t.flush_to_neo4j(FakeNeo4j(), threshold=1)
            assert written == 1
        finally:
            lc.create_co_activation_link = original


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_counter(self) -> None:
        t = make_tracker()
        t.track_recall(["a", "b"])
        t.reset()
        assert t._counter == {}

    def test_reset_clears_written_pairs(self) -> None:
        t = make_tracker()
        t._written_pairs.add(("a", "b"))
        t.reset()
        assert t._written_pairs == set()

    def test_reset_clears_recall_count(self) -> None:
        t = make_tracker()
        t.track_recall(["a", "b"])
        t.reset()
        assert t._recall_count == 0


# ---------------------------------------------------------------------------
# WorkingContext integration — co_activation_tracker field
# ---------------------------------------------------------------------------


class TestWorkingContextIntegration:
    def test_working_context_has_tracker(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc = WorkingContext(session_id="test-session")
        assert isinstance(wc.co_activation_tracker, CoActivationTracker)

    def test_tracker_uses_default_threshold(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc = WorkingContext(session_id="test-session")
        assert wc.co_activation_tracker.threshold == DEFAULT_CO_ACTIVATION_THRESHOLD

    def test_tracker_isolated_per_instance(self) -> None:
        from hindsight_api.engine.session.working_context import WorkingContext

        wc1 = WorkingContext(session_id="s1")
        wc2 = WorkingContext(session_id="s2")
        wc1.co_activation_tracker.track_recall(["a", "b"])
        assert wc2.co_activation_tracker._counter == {}
