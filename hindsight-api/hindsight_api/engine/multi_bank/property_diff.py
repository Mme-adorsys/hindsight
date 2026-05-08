"""Property-conflict detection for Multi-Bank schema convergence (Epic 25 Story 25).

When ``reinforce_shared_schema`` (Story 24) merges an incoming agent-local
schema into an existing Shared schema, this module decides whether the
two are *compatible* (same idea, slight variation) or *conflicting*
(different idea, surface-level cosine similarity hides the divergence).

Conflict definitions:

  Categorical (set / list of strings):
    Jaccard overlap < ``CATEGORICAL_DISJOINT_THRESHOLD`` (= 0.2) →
    "the two banks describe disjoint membership for this slot"

  Numeric ({min, max, mean} dicts):
    |mean_a - mean_b| > ``NUMERIC_DIVERGENCE_FACTOR`` (= 0.5) × max-range →
    "the central tendencies differ by half the total spread"

  Scalar values (str / int / float without aggregation envelope):
    A simple equality check — different scalars are a conflict.

Tags-bag fallback (the ``_keywords`` Counter from C2 property_aggregator)
is treated as a categorical set keyed by the most-frequent tags.

Bio mapping: when two cortical assemblies disagree about the same slot
(the brain's analogue of conflicting beliefs about a category), the
conflict surfaces as a competing assembly rather than an averaged blob.
Concept §15 Multi-Bank, §13 Schema Emergence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# How much of the keys may *miss* before two categorical sets count as conflicting.
CATEGORICAL_DISJOINT_THRESHOLD: float = 0.2
"""Jaccard ≥ this → compatible; below → categorical conflict."""

NUMERIC_DIVERGENCE_FACTOR: float = 0.5
"""|mean_diff| > factor × max_range → numeric conflict."""


@dataclass(frozen=True)
class ConflictReport:
    """One per conflicting key. ``kind`` identifies which branch fired."""

    key: str
    kind: str  # "categorical" | "numeric" | "scalar" | "type_mismatch"
    value_a: Any
    value_b: Any


# Properties stamped by the promoter itself — never compared. They're
# bookkeeping fields, not domain knowledge.
_BOOKKEEPING_KEYS: frozenset[str] = frozenset(
    {
        "source_bank_id",
        "source_bank_ids",
        "promoted_from_schema_id",
        "cross_agent_count",
        "confidence_tier",
    }
)


def _is_categorical(value: Any) -> bool:
    return isinstance(value, list | tuple | set | frozenset)


def _is_numeric_envelope(value: Any) -> bool:
    return isinstance(value, dict) and "mean" in value


def _categorical_conflict(a: Any, b: Any) -> bool:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return False
    union = set_a | set_b
    if not union:
        return False
    jaccard = len(set_a & set_b) / len(union)
    return jaccard < CATEGORICAL_DISJOINT_THRESHOLD


def _numeric_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        mean_a = float(a.get("mean", 0.0))
        mean_b = float(b.get("mean", 0.0))
    except (TypeError, ValueError):
        return False
    range_a = abs(float(a.get("max", mean_a)) - float(a.get("min", mean_a)))
    range_b = abs(float(b.get("max", mean_b)) - float(b.get("min", mean_b)))
    max_range = max(range_a, range_b)
    if max_range == 0.0:
        return mean_a != mean_b
    return abs(mean_a - mean_b) > NUMERIC_DIVERGENCE_FACTOR * max_range


def detect_conflicts(props_a: dict[str, Any], props_b: dict[str, Any]) -> list[ConflictReport]:
    """Return one :class:`ConflictReport` per conflicting key.

    Keys present in only one side are *not* conflicts — they're additive
    knowledge from one agent that the other simply hasn't seen yet.
    Bookkeeping properties (source_bank_id et al.) are skipped.
    """
    out: list[ConflictReport] = []
    shared_keys = (set(props_a) & set(props_b)) - _BOOKKEEPING_KEYS
    for key in sorted(shared_keys):
        a, b = props_a[key], props_b[key]

        if _is_categorical(a) and _is_categorical(b):
            if _categorical_conflict(a, b):
                out.append(ConflictReport(key=key, kind="categorical", value_a=a, value_b=b))
            continue

        if _is_numeric_envelope(a) and _is_numeric_envelope(b):
            if _numeric_conflict(a, b):
                out.append(ConflictReport(key=key, kind="numeric", value_a=a, value_b=b))
            continue

        if isinstance(a, str | int | float | bool) and isinstance(b, str | int | float | bool):
            if a != b:
                out.append(ConflictReport(key=key, kind="scalar", value_a=a, value_b=b))
            continue

        # Mixed types (categorical vs numeric, etc.) — flag for visibility.
        if type(a) is not type(b):
            out.append(ConflictReport(key=key, kind="type_mismatch", value_a=a, value_b=b))
    return out


def has_conflicts(props_a: dict[str, Any], props_b: dict[str, Any]) -> bool:
    """Convenience wrapper — returns True if any conflict surfaces."""
    return bool(detect_conflicts(props_a, props_b))
