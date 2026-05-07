"""Deterministic property aggregation for C2 schema creation (Epic 25 Story 07).

When a matured cluster needs to become a schema (concept §13, after R1+R2,
before the LLM description in Story 08), C2 reads the cluster engrams' tags
and folds them into ``Schema.properties`` (concept §4.2). The aggregation
is **deterministic and LLM-free** — same engrams, same properties — so
fingerprint matching across cycles stays stable.

Two tag conventions are supported:

- ``"key:value"`` strings produce a typed property under ``key``. Value type
  is auto-detected: ``int``/``float`` → numeric (mean/median/min/max),
  ISO-8601 timestamp → temporal (min_iso/max_iso), otherwise → categorical
  (mode/count/confidence).
- Plain strings (no ``:``) are pooled under the synthetic key
  ``"_keywords"`` as a categorical bag-of-tags. Useful for the current
  flat-tag world where engrams carry tags like ``["ml", "alice"]``.

Output always includes ``evidence_count`` so a reader can sanity-check the
sample size behind any single property.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

KEYWORDS_BUCKET: str = "_keywords"
EVIDENCE_COUNT_KEY: str = "evidence_count"


def aggregate_properties(member_tags: list[list[str]]) -> dict[str, Any]:
    """Aggregate per-engram tag lists into deterministic schema properties.

    Args:
        member_tags: one ``list[str]`` per cluster member (engram).

    Returns:
        ``dict[str, Any]`` keyed by detected tag-namespace. Always includes
        ``evidence_count``; empty input returns ``{"evidence_count": 0}``.
    """
    evidence_count = len(member_tags)
    if evidence_count == 0:
        return {EVIDENCE_COUNT_KEY: 0}

    by_key: dict[str, list[str]] = defaultdict(list)
    for tags in member_tags:
        for raw in tags:
            key, value = _split_tag(raw)
            by_key[key].append(value)

    result: dict[str, Any] = {EVIDENCE_COUNT_KEY: evidence_count}
    for key, values in by_key.items():
        result[key] = _summarise(values, evidence_count)
    return result


def _split_tag(raw: str) -> tuple[str, str]:
    """Parse a tag into ``(key, value)``.

    A single ``":"`` splits namespace from value. Anything else falls under
    ``KEYWORDS_BUCKET`` so the bag-of-tags path stays usable for callers
    that haven't migrated to namespaced tags yet.
    """
    raw = raw.strip()
    if ":" in raw:
        head, _, tail = raw.partition(":")
        head = head.strip()
        tail = tail.strip()
        if head and tail:
            return head, tail
    return KEYWORDS_BUCKET, raw


def _summarise(values: list[str], evidence_count: int) -> dict[str, Any]:
    """Pick an aggregation rule by inferring the type of the first parseable value."""
    numeric = _try_parse_numeric(values)
    if numeric is not None:
        nums, kind = numeric
        return _summarise_numeric(nums, kind, len(values))

    temporal = _try_parse_temporal(values)
    if temporal is not None:
        return _summarise_temporal(temporal)

    return _summarise_categorical(values, evidence_count)


def _try_parse_numeric(values: list[str]) -> tuple[list[float], str] | None:
    """Return parsed numbers + ``"int"|"float"`` kind when **all** values parse."""
    parsed: list[float] = []
    saw_float = False
    for v in values:
        try:
            f = float(v)
        except ValueError:
            return None
        # ``float("3.0")`` succeeds — we still want to know if the source
        # looked integer-like for output formatting.
        if "." in v or "e" in v.lower():
            saw_float = True
        parsed.append(f)
    return parsed, ("float" if saw_float else "int")


def _try_parse_temporal(values: list[str]) -> list[datetime] | None:
    """Return parsed datetimes when **all** values are ISO-8601 timestamps."""
    parsed: list[datetime] = []
    for v in values:
        try:
            parsed.append(datetime.fromisoformat(v))
        except ValueError:
            return None
    return parsed


def _summarise_numeric(nums: list[float], kind: str, count: int) -> dict[str, Any]:
    cast = (lambda x: int(round(x))) if kind == "int" else float
    return {
        "type": "numeric",
        "mean": cast(statistics.mean(nums)) if nums else 0,
        "median": cast(statistics.median(nums)) if nums else 0,
        "min": cast(min(nums)) if nums else 0,
        "max": cast(max(nums)) if nums else 0,
        "count": count,
    }


def _summarise_temporal(times: list[datetime]) -> dict[str, Any]:
    times_sorted = sorted(times)
    return {
        "type": "temporal",
        "min_iso": times_sorted[0].isoformat(),
        "max_iso": times_sorted[-1].isoformat(),
        "count": len(times_sorted),
    }


def _summarise_categorical(values: list[str], evidence_count: int) -> dict[str, Any]:
    """Mode + count + confidence (mode_count / evidence_count).

    ``confidence`` is intentionally normalised against ``evidence_count``
    (number of engrams), not ``len(values)`` (number of tag occurrences),
    because a property like ``mood: productive`` is "the mood was productive
    in 7 of 8 engrams" not "of the 12 mood-tags 7 said productive".
    """
    counts = Counter(values)
    mode_value, mode_count = counts.most_common(1)[0]
    return {
        "type": "categorical",
        "value": mode_value,
        "count": mode_count,
        "confidence": mode_count / evidence_count if evidence_count > 0 else 0.0,
    }
