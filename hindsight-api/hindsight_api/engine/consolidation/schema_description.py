"""Schema description generation for C2 (Epic 25 Story 08).

Renders a one-sentence human-readable summary of a schema's aggregated
properties (concept §4.2 — three Schema representations: Centroid for
search, Properties for structured query, Description for humans/LLM).

This is the **only** LLM-driven step in C2. Pure data-to-text, no
reasoning — pipeline step ``consolidation.schema_description`` runs at
``ModelTier.SMALL`` (concept §16). When the LLM call fails or no caller is
provided, a deterministic template fallback keeps C2 unblocked: an empty
or sparse description is better than a half-written schema.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

DescriptionLLMCaller = Callable[[str], Awaitable[str]]
"""Async callable: prompt → completion text. Caller wires this from LLMRegistry."""

PROMPT_TEMPLATE: str = (
    "You are summarizing the recurring STRUCTURE of an episodic memory pattern. "
    "Write exactly ONE precise sentence in German describing what kind of episode "
    "this is — no preface, no list, no JSON in the output.\n\n"
    "How to read the properties dict:\n"
    "- 'cluster': a SETTING label. E.g. 'coffee_morning' means meetings that happen "
    "  in the morning while having coffee — NOT meetings whose topic is coffee.\n"
    "- 'drink': the beverage present (coffee, tea, water, ...) — the meeting is held "
    "  WHILE drinking it, the drink itself is not the topic.\n"
    "- 'format': the meeting shape ('1on1' = two-person meeting, 'group' = group meeting).\n"
    "- 'duration': length in MINUTES (so 30 = 30 minutes, not 1 hour).\n"
    "- 'time': time-of-day or day-of-week label.\n"
    "- 'mood': the meeting's character (productive, casual, reflective, ...).\n"
    "- 'domain': topic area for declarative fact clusters.\n"
    "- 'shape': 'declarative' = recurring factual statement, not an event.\n"
    "- numeric properties carry min/max/mean — describe them as approximate values.\n"
    "- categorical properties with confidence=1.0 are invariant for the pattern.\n\n"
    "What the properties do NOT contain: the actual content of individual episodes. "
    "Describe only what RECURS: the setting, the format, the domain — NOT what the "
    "participants talked about.\n\n"
    "Concrete examples:\n"
    "- properties=[cluster=coffee_morning, drink=coffee, format=1on1, time=morning, "
    "  duration=30min, mood=productive] → 'Morgendliche 30-minütige Einzelgespräche "
    "  bei einem Kaffee, mit produktivem Charakter.'\n"
    "- properties=[cluster=sprint_retro, format=group, participants=6, time=friday, "
    "  duration=60min] → 'Freitägliche einstündige Gruppen-Retrospektiven mit sechs "
    "  Teilnehmern.'\n\n"
    "Properties (JSON):\n{properties_json}\n\n"
    "Evidence-Count (number of episodes): {evidence_count}\n\n"
    "Answer (exactly 1 German sentence, max 200 chars):"
)

# Soft cap so a runaway LLM doesn't smuggle paragraphs into the schema doc field.
MAX_DESCRIPTION_CHARS: int = 240


async def generate_schema_description(
    properties: dict[str, Any],
    evidence_count: int,
    llm_caller: DescriptionLLMCaller | None = None,
) -> str:
    """Render a one-sentence description from aggregated schema properties.

    Args:
        properties: aggregator output from
            ``engine.consolidation.property_aggregator.aggregate_properties``.
        evidence_count: number of engrams behind this schema (audit hint
            already included in ``properties['evidence_count']`` — passed
            again so the prompt sees it explicitly).
        llm_caller: optional async callable. ``None`` → template-only path
            (used in tests and when the route is misconfigured). Any
            exception from the caller falls back to the template.

    Returns:
        A description string. Empty when there's nothing to describe.
    """
    if not properties or evidence_count <= 0:
        return ""

    if llm_caller is None:
        return _template_description(properties, evidence_count)

    import json

    prompt = PROMPT_TEMPLATE.format(
        properties_json=json.dumps(_strip_internals(properties), ensure_ascii=False, sort_keys=True),
        evidence_count=evidence_count,
    )
    try:
        raw = await llm_caller(prompt)
    except Exception as exc:
        logger.warning(
            "schema_description LLM failed (%s); falling back to template",
            exc.__class__.__name__,
        )
        return _template_description(properties, evidence_count)

    text = (raw or "").strip()
    if not text:
        logger.debug("schema_description LLM returned empty; falling back to template")
        return _template_description(properties, evidence_count)
    return text[:MAX_DESCRIPTION_CHARS]


def _template_description(properties: dict[str, Any], evidence_count: int) -> str:
    """Deterministic fallback — works for any property shape.

    Format: ``"Muster über N Engrams: key1=value1, key2~mean2, key3=min3..max3"``.
    Per-type rendering keeps the line interpretable to humans and to test
    asserts; the LLM path produces a much nicer sentence when available.
    """
    parts: list[str] = []
    for key, info in properties.items():
        if key == "evidence_count" or not isinstance(info, dict):
            continue
        kind = info.get("type")
        if kind == "categorical":
            parts.append(f"{key}={info.get('value')}")
        elif kind == "numeric":
            parts.append(f"{key}~{info.get('mean')}")
        elif kind == "temporal":
            parts.append(f"{key}={info.get('min_iso')}..{info.get('max_iso')}")

    if not parts:
        return f"Muster über {evidence_count} Engrams (keine Properties)"
    body = ", ".join(parts)
    return f"Muster über {evidence_count} Engrams: {body}"[:MAX_DESCRIPTION_CHARS]


def _strip_internals(properties: dict[str, Any]) -> dict[str, Any]:
    """Drop bookkeeping keys (``evidence_count``) from the prompt JSON."""
    return {k: v for k, v in properties.items() if k != "evidence_count"}
