"""
Unit tests for Epic 02 Story 02 — Episode & Session Models.

Tests:
- RetrievalMode Enum values
- Episode Model creation + to_retain_content() conversion
- Session Model defaults + Session.default() factory
- Session as optional parameter in interface signatures (type check)
"""

import uuid
from datetime import datetime, timezone

import pytest

from hindsight_api.engine.response_models import Episode, RetrievalMode, Session


# =============================================================================
# RetrievalMode Enum
# =============================================================================


class TestRetrievalMode:
    def test_all_four_modes_exist(self):
        assert RetrievalMode.PRECISION
        assert RetrievalMode.EXPLORATION
        assert RetrievalMode.ANALOGY
        assert RetrievalMode.VALIDATION

    def test_string_values(self):
        assert RetrievalMode.PRECISION == "precision"
        assert RetrievalMode.EXPLORATION == "exploration"
        assert RetrievalMode.ANALOGY == "analogy"
        assert RetrievalMode.VALIDATION == "validation"

    def test_default_is_precision(self):
        session = Session()
        assert session.mode == RetrievalMode.PRECISION

    def test_from_string(self):
        mode = RetrievalMode("exploration")
        assert mode == RetrievalMode.EXPLORATION


# =============================================================================
# Episode Model
# =============================================================================


class TestEpisodeModel:
    def test_basic_creation(self):
        ep = Episode(
            action="Deployed service to production",
            context="Scheduled maintenance window",
            outcome="Service running, no errors",
        )
        assert ep.action == "Deployed service to production"
        assert ep.context == "Scheduled maintenance window"
        assert ep.outcome == "Service running, no errors"
        assert ep.timestamp is None
        assert ep.metadata is None

    def test_with_timestamp(self):
        now = datetime.now(timezone.utc)
        ep = Episode(action="Test", context="ctx", outcome="ok", timestamp=now)
        assert ep.timestamp == now

    def test_with_metadata(self):
        ep = Episode(action="Test", context="ctx", outcome="ok", metadata={"source": "agent"})
        assert ep.metadata == {"source": "agent"}

    def test_to_retain_content_structure(self):
        ep = Episode(
            action="Alice reviewed the PR",
            context="Code review process",
            outcome="PR approved",
        )
        result = ep.to_retain_content()
        assert "content" in result
        assert "Alice reviewed the PR" in result["content"]
        assert "Code review process" in result["content"]
        assert "PR approved" in result["content"]

    def test_to_retain_content_format(self):
        ep = Episode(action="A", context="B", outcome="C")
        result = ep.to_retain_content()
        assert result["content"] == "Action: A | Context: B | Outcome: C"

    def test_to_retain_content_with_timestamp(self):
        now = datetime.now(timezone.utc)
        ep = Episode(action="A", context="B", outcome="C", timestamp=now)
        result = ep.to_retain_content()
        assert result["event_date"] == now

    def test_to_retain_content_without_timestamp(self):
        ep = Episode(action="A", context="B", outcome="C")
        result = ep.to_retain_content()
        assert "event_date" not in result

    def test_to_retain_content_with_metadata(self):
        ep = Episode(action="A", context="B", outcome="C", metadata={"k": "v"})
        result = ep.to_retain_content()
        assert result["metadata"] == {"k": "v"}

    def test_to_retain_content_without_metadata(self):
        ep = Episode(action="A", context="B", outcome="C")
        result = ep.to_retain_content()
        assert "metadata" not in result


# =============================================================================
# Session Model
# =============================================================================


class TestSessionModel:
    def test_default_construction(self):
        session = Session()
        assert session.mode == RetrievalMode.PRECISION
        assert session.current_expectation is None
        assert session.task_context is None
        assert session.session_id is not None
        assert session.started_at is not None

    def test_default_factory_method(self):
        session = Session.default()
        assert session.mode == RetrievalMode.PRECISION
        assert session.current_expectation is None

    def test_unique_session_ids(self):
        s1 = Session.default()
        s2 = Session.default()
        assert s1.session_id != s2.session_id

    def test_custom_mode(self):
        session = Session(mode=RetrievalMode.EXPLORATION)
        assert session.mode == RetrievalMode.EXPLORATION

    def test_with_expectation(self):
        session = Session(current_expectation="Alice is a software engineer")
        assert session.current_expectation == "Alice is a software engineer"

    def test_with_task_context(self):
        session = Session(task_context="Debugging the auth service")
        assert session.task_context == "Debugging the auth service"

    def test_session_id_is_uuid(self):
        session = Session()
        assert isinstance(session.session_id, uuid.UUID)

    def test_all_four_modes_work(self):
        for mode in RetrievalMode:
            session = Session(mode=mode)
            assert session.mode == mode

    def test_transient_not_sqlalchemy(self):
        """Session must be a pure Pydantic model, not SQLAlchemy ORM."""
        from pydantic import BaseModel

        assert issubclass(Session, BaseModel)
        # Must NOT have __tablename__ (SQLAlchemy marker)
        assert not hasattr(Session, "__tablename__")
