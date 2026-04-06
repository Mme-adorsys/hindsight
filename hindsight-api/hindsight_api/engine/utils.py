"""
Utility functions for memory system.

Also contains engine-level infrastructure helpers (fq_table, Budget, etc.)
that were extracted from memory_engine.py to allow orchestrators to import
them without creating circular imports.
"""

from __future__ import annotations

import contextvars
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm_wrapper import LLMConfig
    from .retain.fact_extraction import Fact


async def extract_facts(
    text: str,
    event_date: datetime,
    context: str = "",
    llm_config: "LLMConfig" = None,
    agent_name: str = None,
    extract_opinions: bool = False,
) -> tuple[list["Fact"], list[tuple[str, int]]]:
    """
    Extract semantic facts from text using LLM.

    Uses LLM for intelligent fact extraction that:
    - Filters out social pleasantries and filler words
    - Creates self-contained statements with absolute dates
    - Handles conversational text well
    - Resolves relative time expressions to absolute dates

    Args:
        text: Input text (conversation, article, etc.)
        event_date: Reference date for resolving relative times
        context: Context about the conversation/document
        llm_config: LLM configuration to use
        agent_name: Optional agent name to help identify agent-related facts
        extract_opinions: If True, extract ONLY opinions. If False, extract world and agent facts (no opinions)

    Returns:
        Tuple of (facts, chunks) where:
        - facts: List of Fact model instances
        - chunks: List of tuples (chunk_text, fact_count) for each chunk

    Raises:
        Exception: If LLM fact extraction fails
    """
    if not text or not text.strip():
        return [], []

    from .retain.fact_extraction import extract_facts_from_text  # lazy to avoid circular import

    facts, chunks, _ = await extract_facts_from_text(
        text,
        event_date,
        context=context,
        llm_config=llm_config,
        agent_name=agent_name,
        extract_opinions=extract_opinions,
    )

    if not facts:
        logging.warning(
            "LLM extracted 0 facts from text of length %d. This may indicate the text contains no meaningful information, or the LLM failed to extract facts. Text preview: %.100s...",
            len(text),
            text,
        )
        return [], chunks

    return facts, chunks


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Calculate cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score between 0 and 1
    """
    if len(vec1) != len(vec2):
        raise ValueError("Vectors must have same dimension")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = sum(a * a for a in vec1) ** 0.5
    magnitude2 = sum(b * b for b in vec2) ** 0.5

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)


def calculate_recency_weight(days_since: float, half_life_days: float = 365.0) -> float:
    """
    Calculate recency weight using logarithmic decay.

    This provides much better differentiation over long time periods compared to
    exponential decay. Uses a log-based decay where the half-life parameter controls
    when memories reach 50% weight.

    Examples:
        - Today (0 days): 1.0
        - 1 year (365 days): ~0.5 (with default half_life=365)
        - 2 years (730 days): ~0.33
        - 5 years (1825 days): ~0.17
        - 10 years (3650 days): ~0.09

    This ensures that 2-year-old and 5-year-old memories have meaningfully
    different weights, unlike exponential decay which makes them both ~0.

    Args:
        days_since: Number of days since the memory was created
        half_life_days: Number of days for weight to reach 0.5 (default: 1 year)

    Returns:
        Weight between 0 and 1
    """
    import math

    # Logarithmic decay: 1 / (1 + log(1 + days_since/half_life))
    # This decays much slower than exponential, giving better long-term differentiation
    normalized_age = days_since / half_life_days
    return 1.0 / (1.0 + math.log1p(normalized_age))


def calculate_frequency_weight(access_count: int, max_boost: float = 2.0) -> float:
    """
    Calculate frequency weight based on access count.

    Frequently accessed memories are weighted higher.
    Uses logarithmic scaling to avoid over-weighting.

    Args:
        access_count: Number of times the memory was accessed
        max_boost: Maximum multiplier for frequently accessed memories

    Returns:
        Weight between 1.0 and max_boost
    """
    import math

    if access_count <= 0:
        return 1.0

    # Logarithmic scaling: log(access_count + 1) / log(10)
    # This gives: 0 accesses = 1.0, 9 accesses ~= 1.5, 99 accesses ~= 2.0
    normalized = math.log(access_count + 1) / math.log(10)
    return 1.0 + min(normalized, max_boost - 1.0)


def calculate_temporal_anchor(occurred_start: datetime, occurred_end: datetime) -> datetime:
    """
    Calculate a single temporal anchor point from a temporal range.

    Used for spreading activation - we need a single representative date
    to calculate temporal proximity between facts. This simplifies the
    range-to-range distance problem.

    Strategy: Use midpoint of the range for balanced representation.

    Args:
        occurred_start: Start of temporal range
        occurred_end: End of temporal range

    Returns:
        Single datetime representing the temporal anchor (midpoint)

    Examples:
        - Point event (July 14): start=July 14, end=July 14 → anchor=July 14
        - Month range (February): start=Feb 1, end=Feb 28 → anchor=Feb 14
        - Year range (2023): start=Jan 1, end=Dec 31 → anchor=July 1
    """
    # Calculate midpoint
    time_delta = occurred_end - occurred_start
    midpoint = occurred_start + (time_delta / 2)
    return midpoint


def calculate_temporal_proximity(anchor_a: datetime, anchor_b: datetime, half_life_days: float = 30.0) -> float:
    """
    Calculate temporal proximity between two temporal anchors.

    Used for spreading activation to determine how "close" two facts are
    in time. Uses logarithmic decay so that temporal similarity doesn't
    drop off too quickly.

    Args:
        anchor_a: Temporal anchor of first fact
        anchor_b: Temporal anchor of second fact
        half_life_days: Number of days for proximity to reach 0.5
                       (default: 30 days = 1 month)

    Returns:
        Proximity score in [0, 1] where:
        - 1.0 = same day
        - 0.5 = ~half_life days apart
        - 0.0 = very distant in time

    Examples:
        - Same day: 1.0
        - 1 week apart (half_life=30): ~0.7
        - 1 month apart (half_life=30): ~0.5
        - 1 year apart (half_life=30): ~0.2
    """
    import math

    days_apart = abs((anchor_a - anchor_b).days)

    if days_apart == 0:
        return 1.0

    # Logarithmic decay: 1 / (1 + log(1 + days_apart/half_life))
    # Similar to calculate_recency_weight but for proximity between events
    normalized_distance = days_apart / half_life_days
    proximity = 1.0 / (1.0 + math.log1p(normalized_distance))

    return proximity


# =============================================================================
# Engine-level infrastructure helpers (extracted from memory_engine.py)
# Orchestrators import from here; memory_engine.py re-exports for backward compat.
# =============================================================================

import tiktoken
from pydantic import BaseModel, Field

from .db_utils import acquire_with_retry as acquire_with_retry  # noqa: F401 (re-export)

# ---------------------------------------------------------------------------
# Schema / multi-tenancy helpers
# ---------------------------------------------------------------------------

# Context variable for current schema (async-safe, per-task isolation)
_current_schema: contextvars.ContextVar[str] = contextvars.ContextVar("current_schema", default="public")


def get_current_schema() -> str:
    """Get the current schema from context (default: 'public')."""
    return _current_schema.get()


def fq_table(table_name: str) -> str:
    """
    Get fully-qualified table name with current schema.

    Example:
        fq_table("memory_units") -> "public.memory_units"
        fq_table("memory_units") -> "tenant_xyz.memory_units" (if schema is set)
    """
    return f"{get_current_schema()}.{table_name}"


# Tables that must be schema-qualified (for runtime validation)
_PROTECTED_TABLES = frozenset(
    [
        "memory_units",
        "memory_links",
        "unit_entities",
        "entities",
        "entity_cooccurrences",
        "banks",
        "documents",
        "chunks",
        "async_operations",
    ]
)

# Enable runtime SQL validation (can be disabled in production for performance)
_VALIDATE_SQL_SCHEMAS = True


class UnqualifiedTableError(Exception):
    """Raised when SQL contains unqualified table references."""

    pass


def validate_sql_schema(sql: str) -> None:
    """
    Validate that SQL doesn't contain unqualified table references.

    This is a runtime safety check to prevent cross-tenant data access.
    Raises UnqualifiedTableError if any protected table is referenced
    without a schema prefix.

    Args:
        sql: The SQL query to validate

    Raises:
        UnqualifiedTableError: If unqualified table reference found
    """
    if not _VALIDATE_SQL_SCHEMAS:
        return

    import re

    sql_upper = sql.upper()

    for table in _PROTECTED_TABLES:
        table_upper = table.upper()

        # Pattern: SQL keyword followed by unqualified table name
        # Matches: FROM memory_units, JOIN memory_units, INTO memory_units, UPDATE memory_units
        patterns = [
            rf"FROM\s+{table_upper}(?:\s|$|,|\)|;)",
            rf"JOIN\s+{table_upper}(?:\s|$|,|\)|;)",
            rf"INTO\s+{table_upper}(?:\s|$|\()",
            rf"UPDATE\s+{table_upper}(?:\s|$)",
            rf"DELETE\s+FROM\s+{table_upper}(?:\s|$|;)",
        ]

        for pattern in patterns:
            match = re.search(pattern, sql_upper)
            if match:
                # Check if it's actually qualified (preceded by schema.)
                # Look backwards from match to see if there's a dot
                start = match.start()
                # Find the table name position in the match
                table_pos = sql_upper.find(table_upper, start)
                if table_pos > 0:
                    # Check character before table name (skip whitespace)
                    prefix = sql[:table_pos].rstrip()
                    if not prefix.endswith("."):
                        raise UnqualifiedTableError(
                            f"Unqualified table reference '{table}' in SQL. "
                            f"Use fq_table('{table}') for schema safety. "
                            f"SQL snippet: ...{sql[max(0, start - 10) : start + 50]}..."
                        )


# ---------------------------------------------------------------------------
# Budget enum
# ---------------------------------------------------------------------------


class Budget(str, Enum):
    """Budget levels for recall/reflect operations."""

    LOW = "low"
    MID = "mid"
    HIGH = "high"


# ---------------------------------------------------------------------------
# UTC helper
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """Get current UTC time with timezone info."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Tiktoken cache
# ---------------------------------------------------------------------------

_TIKTOKEN_ENCODING = None


def _get_tiktoken_encoding():
    """Get cached tiktoken encoding (cl100k_base for GPT-4/3.5)."""
    global _TIKTOKEN_ENCODING
    if _TIKTOKEN_ENCODING is None:
        _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
    return _TIKTOKEN_ENCODING


# ---------------------------------------------------------------------------
# Reconsolidation helpers (module-level to avoid re-creating per call)
# ---------------------------------------------------------------------------


class _ReconsolidationEval(BaseModel):
    """LLM structured output for Engram reconsolidation evaluation."""

    outcome: str = Field(
        description="One of: 'confirmed' (still valid), 'modified' (needs update), 'contradiction' (conflicts with known facts)"
    )
    reasoning: str = Field(description="Brief reason for the outcome")


# Disposition → prompt description (module-level constant, not re-created per call)
_DISPOSITION_DESCRIPTIONS: dict[str, str] = {
    "analytical": "weigh evidence carefully and update memories when contradictions are clear",
    "optimistic": "favour confirming existing memories and be tolerant of minor contradictions",
    "conservative": "retain existing memories unless evidence is very strong and direct",
    "neutral": "evaluate objectively without any particular bias",
}
