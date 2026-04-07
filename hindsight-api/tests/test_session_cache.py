"""
Unit tests for SessionCache (Epic 18 Story 01 — Session Cache Layer).

Tests cover:
- Cache construction and default state
- add_episode() append-only semantics
- add_inference() + update_inference_status() status tracking
- record_co_activation() symmetric pair counting
- is_empty() utility
- co_activation_tracker and association_window present
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hindsight_api.engine.response_models import Episode
from hindsight_api.engine.session.session_cache import SessionCache
from hindsight_api.engine.session.working_context import Inference


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cache(session_id: str = "sess-1") -> SessionCache:
    return SessionCache(session_id=session_id)


def make_episode(action: str = "did something") -> Episode:
    return Episode(action=action, context="test", outcome="ok")


def make_inference(inf_id: str = "i1", status: str = "tentative") -> Inference:
    return Inference(
        id=inf_id,
        content="hypothesis",
        confidence=0.6,
        supporting_engram_ids=["e1"],
        created_at=datetime.now(UTC),
        status=status,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestSessionCacheConstruction:
    def test_default_fields_empty(self) -> None:
        sc = make_cache()
        assert sc.session_id == "sess-1"
        assert sc.episodic_buffer == []
        assert sc.pending_inferences == []
        assert sc.co_activation_counts == {}

    def test_created_at_is_set(self) -> None:
        before = datetime.now(UTC)
        sc = make_cache()
        assert sc.created_at >= before

    def test_is_empty_on_new_cache(self) -> None:
        assert make_cache().is_empty()

    def test_co_activation_tracker_present(self) -> None:
        sc = make_cache()
        assert sc.co_activation_tracker is not None

    def test_association_window_present(self) -> None:
        sc = make_cache()
        assert sc.association_window is not None


# ---------------------------------------------------------------------------
# Episodic Buffer
# ---------------------------------------------------------------------------


class TestEpisodicBuffer:
    def test_add_episode_appends(self) -> None:
        sc = make_cache()
        ep = make_episode("event A")
        sc.add_episode(ep)
        assert len(sc.episodic_buffer) == 1
        assert sc.episodic_buffer[0].action == "event A"

    def test_add_multiple_preserves_order(self) -> None:
        sc = make_cache()
        sc.add_episode(make_episode("first"))
        sc.add_episode(make_episode("second"))
        sc.add_episode(make_episode("third"))
        assert [e.action for e in sc.episodic_buffer] == ["first", "second", "third"]

    def test_not_empty_after_add_episode(self) -> None:
        sc = make_cache()
        sc.add_episode(make_episode())
        assert not sc.is_empty()


# ---------------------------------------------------------------------------
# Inference Management
# ---------------------------------------------------------------------------


class TestInferenceManagement:
    def test_add_inference_appends(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i1"))
        assert len(sc.pending_inferences) == 1
        assert sc.pending_inferences[0].id == "i1"

    def test_update_status_confirmed(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i1", status="tentative"))
        sc.update_inference_status("i1", "confirmed")
        assert sc.pending_inferences[0].status == "confirmed"

    def test_update_status_rejected(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i1", status="tentative"))
        sc.update_inference_status("i1", "rejected")
        assert sc.pending_inferences[0].status == "rejected"

    def test_update_status_unknown_id_raises(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference("i1"))
        with pytest.raises(KeyError):
            sc.update_inference_status("nonexistent", "confirmed")

    def test_not_empty_after_add_inference(self) -> None:
        sc = make_cache()
        sc.add_inference(make_inference())
        assert not sc.is_empty()


# ---------------------------------------------------------------------------
# Co-Activation Counting
# ---------------------------------------------------------------------------


class TestCoActivationCounting:
    def test_record_increments_count(self) -> None:
        sc = make_cache()
        sc.record_co_activation("e1", "e2")
        key = ("e1", "e2")
        assert sc.co_activation_counts[key] == 1

    def test_record_twice_increments_to_two(self) -> None:
        sc = make_cache()
        sc.record_co_activation("e1", "e2")
        sc.record_co_activation("e1", "e2")
        assert sc.co_activation_counts[("e1", "e2")] == 2

    def test_symmetric_key_ordering(self) -> None:
        # (a, b) and (b, a) must map to the same counter.
        sc = make_cache()
        sc.record_co_activation("z_engram", "a_engram")
        sc.record_co_activation("a_engram", "z_engram")
        assert sc.co_activation_counts[("a_engram", "z_engram")] == 2
        assert ("z_engram", "a_engram") not in sc.co_activation_counts

    def test_not_empty_after_co_activation(self) -> None:
        sc = make_cache()
        sc.record_co_activation("e1", "e2")
        assert not sc.is_empty()


# ---------------------------------------------------------------------------
# Cache Lifecycle
# ---------------------------------------------------------------------------


class TestCacheLifecycle:
    def test_separate_sessions_independent(self) -> None:
        sc1 = make_cache("sess-1")
        sc2 = make_cache("sess-2")
        sc1.add_episode(make_episode("event for sess-1 only"))
        assert sc2.episodic_buffer == []

    def test_is_empty_with_all_fields_filled(self) -> None:
        sc = make_cache()
        sc.add_episode(make_episode())
        sc.add_inference(make_inference())
        sc.record_co_activation("e1", "e2")
        assert not sc.is_empty()
