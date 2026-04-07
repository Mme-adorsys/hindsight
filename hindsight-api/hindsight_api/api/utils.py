"""Shared utilities for HTTP and MCP API layers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hindsight_api.engine.response_models import Session


def session_from_mode(mode: str | None) -> "Session | None":
    """Build a transient Session from an optional mode string.

    Returns None when mode is not provided (engine uses Precision default).
    Raises ValueError for unrecognised mode values.
    """
    if mode is None:
        return None
    from hindsight_api.engine.response_models import RetrievalMode, Session

    try:
        return Session(mode=RetrievalMode(mode.lower()))
    except ValueError:
        valid = ", ".join(m.value for m in RetrievalMode)
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: {valid}")


def parse_json_param(value: str | None, param_name: str) -> dict | list | None:
    """Parse a JSON string parameter, returning None if empty/None.

    Raises ValueError with descriptive message on parse failure.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in '{param_name}': {e}")
