"""
Working Memory — Persistent PFC-equivalent state that survives session boundaries.

Holds the durable portion of the Working Context: goals, active engram references,
confirmed inferences, and session history. Persisted in PostgreSQL as JSONB so the
next session can resume with warm context instead of starting from scratch.

Split model (Epic 18):
- WorkingMemory (this file)  → persisted to PostgreSQL, loaded at session start
- WorkingContext             → in-memory runtime workspace, populated from WorkingMemory
- SessionCache               → transient per-session buffer, discarded at session end

Bio mapping:
- WorkingMemory  → Long-term PFC representations that persist across task boundaries
- Priming Effect → Next session starts with active goals/engrams from the last session
- session_history → Episodic self-model: "what have I been working on recently?"

Concept reference: docs/engram/concept.md — Chapter 9 (Working Context, Persistence)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .working_context import ActiveEngrams, EngramRef, Goal, Inference

# Maximum number of session IDs kept in session_history (FIFO eviction)
SESSION_HISTORY_MAX = 20

# Current schema version — increment when the serialization format changes
CURRENT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# SessionRef
# ---------------------------------------------------------------------------


@dataclass
class SessionRef:
    """
    Lightweight reference to a past session stored in Working Memory.

    Bio mapping: Episodic self-awareness — the PFC maintains a recent history
    of what tasks/contexts have been active, enabling continuity across sessions.
    """

    session_id: str
    started_at: datetime


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _serialize_goal(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id,
        "description": g.description,
        "priority": g.priority,
        "status": g.status,
        "created_at": _dt_to_str(g.created_at),
        "parent_goal_id": g.parent_goal_id,
    }


def _deserialize_goal(d: dict[str, Any]) -> Goal:
    return Goal(
        id=d["id"],
        description=d["description"],
        priority=d["priority"],
        status=d["status"],
        created_at=_str_to_dt(d["created_at"]),
        parent_goal_id=d.get("parent_goal_id"),
    )


def _serialize_engram_ref(r: EngramRef) -> dict[str, Any]:
    return {
        "engram_id": r.engram_id,
        "strength": r.strength,
        "relevance_score": r.relevance_score,
        "activated_at": _dt_to_str(r.activated_at),
    }


def _deserialize_engram_ref(d: dict[str, Any]) -> EngramRef:
    return EngramRef(
        engram_id=d["engram_id"],
        strength=d["strength"],
        relevance_score=d["relevance_score"],
        activated_at=_str_to_dt(d["activated_at"]),
    )


def _serialize_active_engrams(ae: ActiveEngrams) -> dict[str, Any]:
    return {
        "focus": [_serialize_engram_ref(r) for r in ae.focus],
        "supporting": [_serialize_engram_ref(r) for r in ae.supporting],
        "peripheral": [_serialize_engram_ref(r) for r in ae.peripheral],
    }


def _deserialize_active_engrams(d: dict[str, Any]) -> ActiveEngrams:
    return ActiveEngrams(
        focus=[_deserialize_engram_ref(r) for r in d.get("focus", [])],
        supporting=[_deserialize_engram_ref(r) for r in d.get("supporting", [])],
        peripheral=[_deserialize_engram_ref(r) for r in d.get("peripheral", [])],
    )


def _serialize_inference(inf: Inference) -> dict[str, Any]:
    return {
        "id": inf.id,
        "content": inf.content,
        "confidence": inf.confidence,
        "supporting_engram_ids": inf.supporting_engram_ids,
        "created_at": _dt_to_str(inf.created_at),
        "status": inf.status,
    }


def _deserialize_inference(d: dict[str, Any]) -> Inference:
    return Inference(
        id=d["id"],
        content=d["content"],
        confidence=d["confidence"],
        supporting_engram_ids=d.get("supporting_engram_ids", []),
        created_at=_str_to_dt(d["created_at"]),
        status=d["status"],
    )


def _serialize_session_ref(sr: SessionRef) -> dict[str, Any]:
    return {"session_id": sr.session_id, "started_at": _dt_to_str(sr.started_at)}


def _deserialize_session_ref(d: dict[str, Any]) -> SessionRef:
    return SessionRef(session_id=d["session_id"], started_at=_str_to_dt(d["started_at"]))


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------


@dataclass
class WorkingMemory:
    """
    Persistent Working Memory — the durable PFC state stored between sessions.

    Loaded at session start (priming effect) and saved at session end.
    Serialized as JSONB in PostgreSQL (one row per bank_id).

    Schema evolution: if the schema_version in a stored row doesn't match
    CURRENT_SCHEMA_VERSION, from_dict() returns a fresh default to avoid
    crashes from incompatible formats.
    """

    bank_id: str
    goal_stack: list[Goal] = field(default_factory=list)
    active_engrams: ActiveEngrams = field(default_factory=ActiveEngrams)
    confirmed_inferences: list[Inference] = field(default_factory=list)
    session_history: list[SessionRef] = field(default_factory=list)
    """Last SESSION_HISTORY_MAX sessions (FIFO). Newest at end."""
    schema_version: int = CURRENT_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for JSONB storage."""
        return {
            "bank_id": self.bank_id,
            "goal_stack": [_serialize_goal(g) for g in self.goal_stack],
            "active_engrams": _serialize_active_engrams(self.active_engrams),
            "confirmed_inferences": [_serialize_inference(i) for i in self.confirmed_inferences],
            "session_history": [_serialize_session_ref(sr) for sr in self.session_history],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkingMemory:
        """
        Deserialize from a JSONB dict.

        Returns WorkingMemory.default(bank_id) if schema_version mismatches
        or if required keys are missing (forward-compatibility safety net).
        """
        bank_id = data.get("bank_id", "")
        schema_version = data.get("schema_version", 0)
        if schema_version != CURRENT_SCHEMA_VERSION:
            return cls.default(bank_id)
        try:
            return cls(
                bank_id=bank_id,
                goal_stack=[_deserialize_goal(g) for g in data.get("goal_stack", [])],
                active_engrams=_deserialize_active_engrams(data.get("active_engrams", {})),
                confirmed_inferences=[_deserialize_inference(i) for i in data.get("confirmed_inferences", [])],
                session_history=[_deserialize_session_ref(sr) for sr in data.get("session_history", [])],
                schema_version=schema_version,
            )
        except (KeyError, ValueError, TypeError):
            return cls.default(bank_id)

    @staticmethod
    def default(bank_id: str) -> WorkingMemory:
        """Return a fresh empty WorkingMemory for a new or unknown bank."""
        return WorkingMemory(bank_id=bank_id)

    # ------------------------------------------------------------------
    # Session History
    # ------------------------------------------------------------------

    def add_session_ref(self, session_ref: SessionRef) -> None:
        """
        Append a session reference and evict oldest if over capacity.

        FIFO: oldest entries are at index 0, newest at the end.
        """
        self.session_history.append(session_ref)
        if len(self.session_history) > SESSION_HISTORY_MAX:
            self.session_history.pop(0)
