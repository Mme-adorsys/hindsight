"""
Session Layer — transient steering context for all memory operations.

Bio mapping: PFC working memory — situational control of hippocampal access.
"""

from .association_window import AssociationWindow
from .co_activation_tracker import CoActivationTracker
from .mode_config import MODE_PROFILES, ModeConfig, ScoringWeights, get_mode_config
from .session_manager import ModeSignal, ModeTransition, SessionManager, SessionState
from .working_context import (
    MAX_FOCUS,
    MAX_PERIPHERAL,
    MAX_SUPPORTING,
    ActiveEngrams,
    EngramRef,
    Goal,
    Inference,
    WorkingContext,
)

__all__ = [
    "AssociationWindow",
    "CoActivationTracker",
    "ModeConfig",
    "ScoringWeights",
    "MODE_PROFILES",
    "get_mode_config",
    "SessionManager",
    "SessionState",
    "ModeSignal",
    "ModeTransition",
    "WorkingContext",
    "Goal",
    "EngramRef",
    "ActiveEngrams",
    "Inference",
    "MAX_FOCUS",
    "MAX_SUPPORTING",
    "MAX_PERIPHERAL",
]
