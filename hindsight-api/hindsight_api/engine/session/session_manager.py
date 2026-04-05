"""
Session Manager — Lifecycle and Dual Control for the Session Layer.

Creates, tracks, and terminates transient sessions. Implements Dual Control:
- Explicit: Agent/User sets mode directly via set_mode()
- Automatic: System signals shift mode via process_signal() when no explicit mode is set

Bio mapping:
- Explicit control   → PFC top-down goal-directed attention
- Automatic shifts   → Subcortical salience signals (noradrenaline/surprise) overriding default strategy
- Episode buffer     → Hippocampal short-term episodic binding (pre-consolidation)
- Mode history       → Meta-cognitive monitoring

Concept reference: docs/engram/concept.md — Chapter 7 (Session Layer, Dual Control)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from uuid import UUID

from ..response_models import Episode, RetrievalMode, Session
from .working_context import WorkingContext

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TTL = timedelta(hours=24)


# ---------------------------------------------------------------------------
# ModeSignal — T3
# System-generated signals that can trigger automatic mode shifts.
# ---------------------------------------------------------------------------
class ModeSignal(str, Enum):
    """
    System signals that may trigger an automatic session mode shift.

    Bio mapping: subcortical salience signals (noradrenaline, dopamine) that
    modulate PFC strategy selection without conscious deliberation.
    """

    HIGH_SURPRISE = "high_surprise"
    """Thalamus surprise score exceeded threshold — contradiction likely."""

    WEAK_MATCHES = "weak_matches"
    """Retrieval returned only weak/few matches — broaden search strategy."""

    CONTRADICTION = "contradiction"
    """Retrieved content contradicts current_expectation."""

    PREDICTION_ERROR = "prediction_error"
    """Explicit prediction error detected between expectation and outcome."""

    STRONG_MATCHES = "strong_matches"
    """Retrieval returned strong confident matches — focus and be precise."""


# Signal → target mode mapping (concept.md §7 Dual Control)
_SIGNAL_MODE_MAP: dict[ModeSignal, RetrievalMode] = {
    ModeSignal.HIGH_SURPRISE: RetrievalMode.VALIDATION,
    ModeSignal.WEAK_MATCHES: RetrievalMode.EXPLORATION,
    ModeSignal.CONTRADICTION: RetrievalMode.VALIDATION,
    ModeSignal.PREDICTION_ERROR: RetrievalMode.VALIDATION,
    ModeSignal.STRONG_MATCHES: RetrievalMode.PRECISION,
}


# ---------------------------------------------------------------------------
# ModeTransition — T5
# Logged entry in the mode history for debugging / metrics.
# ---------------------------------------------------------------------------
@dataclass
class ModeTransition:
    """
    Record of a single mode change within a session.

    Not persisted — lives only in the SessionState for the duration of the session.
    """

    from_mode: RetrievalMode
    to_mode: RetrievalMode
    trigger: str
    """Human-readable trigger description: 'explicit', 'signal:high_surprise', etc."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# SessionState — internal state wrapper (T1 + T2 + T4 + T5 + T6)
# Extends the API-facing Session model with internal tracking fields.
# Kept separate so the API model (response_models.Session) stays clean.
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    """
    Internal session state managed by SessionManager.

    Wraps the public Session API model with additional transient fields:
    explicit_mode flag, episode buffer, mode history, and asyncio lock.
    """

    session: Session
    explicit_mode: bool = False
    """True when the mode was set explicitly by Agent/User — blocks automatic shifts."""

    working_context: WorkingContext | None = None
    """Transient PFC-workspace for this session. Created in create_session(), discarded at end."""

    episodes: list[Episode] = field(default_factory=list)
    """In-memory episode buffer. Discarded when session ends."""

    mode_history: list[ModeTransition] = field(default_factory=list)
    """Ordered log of mode transitions for this session."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Per-session lock for thread-safe concurrent operations."""


# ---------------------------------------------------------------------------
# SessionManager — T1, T2, T3, T4, T5, T6
# ---------------------------------------------------------------------------
class SessionManager:
    """
    Creates, tracks, and terminates transient sessions.

    Sessions are stored in an in-memory dict — they are never persisted.
    All mutating operations acquire a per-session asyncio.Lock (T6).

    Usage:
        manager = SessionManager()
        session = await manager.create_session(mode=RetrievalMode.EXPLORATION)
        await manager.add_episode(session.session_id, episode)
        await manager.process_signal(session.session_id, ModeSignal.HIGH_SURPRISE)
        await manager.end_session(session.session_id)
    """

    def __init__(self, session_ttl: timedelta = DEFAULT_SESSION_TTL) -> None:
        self._sessions: dict[UUID, SessionState] = {}
        self._session_ttl = session_ttl

    # ------------------------------------------------------------------
    # Lifecycle (T1)
    # ------------------------------------------------------------------

    def _cleanup_expired(self) -> int:
        """Remove sessions that have exceeded their TTL. Returns count of removed sessions."""
        now = datetime.now(UTC)
        expired = [sid for sid, state in self._sessions.items() if (now - state.session.started_at) > self._session_ttl]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("SessionManager: cleaned up %d expired sessions", len(expired))
        return len(expired)

    async def create_session(
        self,
        mode: RetrievalMode = RetrievalMode.PRECISION,
        task_context: str | None = None,
        expectation: str | None = None,
    ) -> Session:
        """
        Create a new session and return the public Session object.

        Args:
            mode: Initial mode (default: PRECISION).
            task_context: Optional description of the current task.
            expectation: Optional initial prediction for Prediction Error Detection.

        Returns:
            The newly created Session.
        """
        self._cleanup_expired()
        session = Session(
            mode=mode,
            task_context=task_context,
            current_expectation=expectation,
        )
        state = SessionState(
            session=session,
            working_context=WorkingContext(session_id=str(session.session_id)),
        )
        self._sessions[session.session_id] = state
        logger.debug("Session %s created with mode=%s", session.session_id, mode.value)
        return session

    def get_session(self, session_id: UUID) -> Session:
        """
        Return the public Session for the given ID.

        Raises:
            KeyError: If session_id is not found.
        """
        return self._sessions[session_id].session

    def get_working_context(self, session_id: UUID) -> WorkingContext | None:
        """
        Return the WorkingContext for the given session, or None if not found.

        Does not raise — callers can safely check for None when session may not exist.
        """
        state = self._sessions.get(session_id)
        return state.working_context if state else None

    async def end_session(self, session_id: UUID) -> list:
        """
        Terminate a session and return flush contents for optional retention.

        Calls working_context.flush() to collect confirmed inferences, completed
        goals, and focus-tier access notes. The caller is responsible for passing
        the returned list to retain_batch_async() if persistence is desired.
        Working Context and all other transient state are discarded.

        Args:
            session_id: ID of the session to end.

        Returns:
            List of RetainContentDict items from the WorkingContext flush.
            Empty list if no session or no working context.

        Raises:
            KeyError: If session_id is not found.
        """
        state = self._sessions.pop(session_id)
        flush_items: list = []
        if state.working_context is not None:
            flush_items = state.working_context.flush()
        logger.debug(
            "Session %s ended. Episodes buffered: %d. Mode transitions: %d. Flush items: %d.",
            session_id,
            len(state.episodes),
            len(state.mode_history),
            len(flush_items),
        )
        return flush_items

    # ------------------------------------------------------------------
    # Explicit Mode Control (T2)
    # ------------------------------------------------------------------

    async def set_mode(self, session_id: UUID, mode: RetrievalMode) -> None:
        """
        Explicitly set the session mode (Agent/User control).

        Sets _explicit_mode = True — subsequent automatic shift signals will be
        logged but NOT applied as long as explicit mode is active.

        Args:
            session_id: Target session.
            mode: The mode to switch to.
        """
        state = self._sessions[session_id]
        async with state.lock:
            old_mode = state.session.mode
            state.session.mode = mode
            state.explicit_mode = True
            transition = ModeTransition(from_mode=old_mode, to_mode=mode, trigger="explicit")
            state.mode_history.append(transition)
            logger.debug(
                "Session %s explicit mode set: %s → %s",
                session_id,
                old_mode.value,
                mode.value,
            )

    # ------------------------------------------------------------------
    # Automatic Mode Shift Engine (T3)
    # ------------------------------------------------------------------

    async def process_signal(self, session_id: UUID, signal: ModeSignal) -> bool:
        """
        Process a system signal and apply an automatic mode shift if allowed.

        Automatic shifts are blocked when explicit_mode is True.

        Args:
            session_id: Target session.
            signal: The system signal to process.

        Returns:
            True if the mode was shifted, False if blocked by explicit mode.
        """
        state = self._sessions[session_id]
        target_mode = _SIGNAL_MODE_MAP[signal]

        async with state.lock:
            if state.explicit_mode:
                logger.debug(
                    "Session %s signal %s blocked — explicit mode active (%s)",
                    session_id,
                    signal.value,
                    state.session.mode.value,
                )
                return False

            old_mode = state.session.mode
            state.session.mode = target_mode
            transition = ModeTransition(
                from_mode=old_mode,
                to_mode=target_mode,
                trigger=f"signal:{signal.value}",
            )
            state.mode_history.append(transition)
            logger.debug(
                "Session %s automatic shift: %s → %s (signal: %s)",
                session_id,
                old_mode.value,
                target_mode.value,
                signal.value,
            )
            return True

    # ------------------------------------------------------------------
    # Episode Buffer (T4)
    # ------------------------------------------------------------------

    async def add_episode(self, session_id: UUID, episode: Episode) -> None:
        """
        Add an episode to the session's in-memory buffer.

        Episodes are discarded when the session ends. The caller is responsible
        for retaining relevant episodes to Engrams before ending the session.

        Args:
            session_id: Target session.
            episode: The episode to buffer.
        """
        state = self._sessions[session_id]
        async with state.lock:
            state.episodes.append(episode)

    def get_episodes(self, session_id: UUID) -> list[Episode]:
        """Return the current episode buffer (read-only snapshot)."""
        return list(self._sessions[session_id].episodes)

    # ------------------------------------------------------------------
    # Mode History (T5)
    # ------------------------------------------------------------------

    def get_mode_history(self, session_id: UUID) -> list[ModeTransition]:
        """Return the mode transition history for debugging (read-only snapshot)."""
        return list(self._sessions[session_id].mode_history)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: UUID) -> bool:
        return session_id in self._sessions
