"""
R0 Sequence Analysis — pre-pipeline step for rich content decomposition.

Neurscience basis: The hippocampus does not store conversation sequences.
It extracts episodes from the stream of experience and links them multidimensionally.
R0 maps this extraction process: budget-dependent depth = hippocampal encoding activation level.

Budget tiers (Epic 15, Story 03):
  LOW   → SMALL LLM   — atomic facts only
  MID   → MEDIUM LLM  — facts + Action→Effect + explicit Expectation/Outcome
  HIGH  → LARGE LLM   — like MID + implicit expectations, hidden causalities, schema hints
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..response_models import TokenUsage
from ..utils import Budget

if TYPE_CHECKING:
    from ..llm_wrapper import LLMConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class UnitType(str, Enum):
    FACT = "fact"
    ACTION_EFFECT = "action_effect"
    EXPERIENCE = "experience"


@dataclass
class StructuredUnit:
    """A single structured unit extracted by R0 from rich content."""

    content: str
    unit_type: UnitType
    context: str = ""
    expectation: str | None = None
    outcome: str | None = None
    related_unit_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Prompt templates per budget tier
# ---------------------------------------------------------------------------

_SYSTEM_BASE = (
    "You are a memory extraction system. "
    "Extract structured memory units from the given text. "
    "Return ONLY a valid JSON array — no explanation, no markdown."
)

_SCHEMA_NOTE = """
Each element in the array must have:
  "content": string — the main fact or statement
  "unit_type": "fact" | "action_effect" | "experience"
  "context": string — 1–5 words of context (topic, domain, or situation)
  "expectation": string or null — only for experience/action_effect
  "outcome": string or null — only for experience/action_effect
  "confidence": float 0.0–1.0
"""

# Low: atomic facts only (SMALL tier)
_PROMPT_LOW = f"""{_SYSTEM_BASE}

Extract only clear, concrete, atomic facts. Do not infer. Do not guess.
Use unit_type "fact" for all entries. Set expectation and outcome to null.
{_SCHEMA_NOTE}"""

# Mid: facts + Action→Effect + Expectation/Outcome (MEDIUM tier)
_PROMPT_MID = f"""{_SYSTEM_BASE}

Extract three types of units:
1. "fact" — atomic, directly stated facts
2. "action_effect" — something someone did and what resulted (outcome required)
3. "experience" — an event where an expectation was set and an outcome occurred (both fields required)

Be specific. Only capture units with clear evidence in the text.
{_SCHEMA_NOTE}"""

# High: like Mid + implicit expectations and hidden causalities (LARGE tier)
_PROMPT_HIGH = f"""{_SYSTEM_BASE}

Extract all unit types including:
1. "fact" — atomic facts, directly and indirectly stated
2. "action_effect" — actions and their effects, including implied effects
3. "experience" — stated OR implied expectation→outcome sequences

Also identify:
- Unspoken assumptions and implicit expectations
- Hidden causal relationships not explicitly stated
- Patterns that suggest schema-level knowledge

Be thorough. Assign lower confidence (0.5–0.7) to inferred or implicit units.
{_SCHEMA_NOTE}"""

_PROMPT_BY_BUDGET: dict[Budget, str] = {
    Budget.LOW: _PROMPT_LOW,
    Budget.MID: _PROMPT_MID,
    Budget.HIGH: _PROMPT_HIGH,
}

# ---------------------------------------------------------------------------
# Core extraction function
# ---------------------------------------------------------------------------


async def analyze_sequence(
    content: str,
    budget: Budget,
    llm: "LLMConfig",
) -> tuple[list[StructuredUnit], TokenUsage]:
    """
    R0: Decompose rich content into structured memory units.

    Args:
        content: Rich text (conversation, narrative, summary).
        budget: Controls extraction depth and LLM tier.
        llm: Resolved LLMConfig for this budget tier.

    Returns:
        Tuple of (list of StructuredUnit, TokenUsage).
    """
    if not content or not content.strip():
        return [], TokenUsage()

    prompt = _PROMPT_BY_BUDGET.get(budget, _PROMPT_MID)
    user_message = f"Text to analyze:\n\n{content}"

    try:
        raw, usage = await llm.call(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
            scope="r0_sequence_analysis",
            temperature=0.1,
            return_usage=True,
            skip_validation=True,
        )
    except Exception as e:
        logger.warning("R0 LLM call failed (%s) — returning empty unit list", e)
        return [], TokenUsage()

    units = _parse_units(raw, content)
    logger.debug("R0 extracted %d units from content (budget=%s)", len(units), budget.value)
    return units, usage


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_units(raw: Any, fallback_content: str) -> list[StructuredUnit]:
    """Parse LLM output into StructuredUnit list. Lenient — never raises."""
    if isinstance(raw, str):
        raw = _extract_json(raw)

    if not isinstance(raw, list):
        logger.warning("R0: unexpected LLM output type %s — falling back to single fact", type(raw).__name__)
        return [StructuredUnit(content=fallback_content, unit_type=UnitType.FACT, confidence=0.5)]

    units: list[StructuredUnit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "").strip()
        if not content:
            continue

        raw_type = item.get("unit_type", "fact")
        try:
            unit_type = UnitType(raw_type)
        except ValueError:
            unit_type = UnitType.FACT

        units.append(
            StructuredUnit(
                content=content,
                unit_type=unit_type,
                context=item.get("context") or "",
                expectation=item.get("expectation") or None,
                outcome=item.get("outcome") or None,
                confidence=float(item.get("confidence", 1.0)),
            )
        )

    if not units:
        # Fallback: treat whole content as a single fact
        return [StructuredUnit(content=fallback_content, unit_type=UnitType.FACT, confidence=0.5)]

    return units


def _extract_json(text: str) -> Any:
    """Extract JSON from a string that may contain markdown fences."""
    import re

    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---------------------------------------------------------------------------
# Conversion: StructuredUnit → RetainContentDict enrichment
# ---------------------------------------------------------------------------


def units_to_content_dicts(
    units: list[StructuredUnit],
    source_dict: dict,
) -> list[dict]:
    """
    Convert StructuredUnits to RetainContentDict entries, inheriting metadata
    from the source content_dict (context, event_date, metadata, tags, etc.).

    ACTION_EFFECT and EXPERIENCE units produce TWO dicts each (split into paired
    engrams for CAUSAL / PREDICTION_ERROR link creation after Retain):
      - ACTION_EFFECT → (action-dict, effect-dict) linked via _action_pair_key/_effect_pair_key
      - EXPERIENCE    → (expectation-dict, outcome-dict) linked via _experience_pair_key

    Both dicts in a pair carry _confidence for use as link weight.

    Args:
        units: Extracted units from R0.
        source_dict: Original RetainContentDict that was analyzed.

    Returns:
        List of dicts ready to be used as RetainContentDicts.
    """
    inherited_keys = ("context", "event_date", "metadata", "entities", "document_id", "tags")
    base: dict = {k: source_dict[k] for k in inherited_keys if k in source_dict}

    result = []
    for unit in units:
        ctx = unit.context or base.get("context", "")

        if unit.unit_type == UnitType.ACTION_EFFECT and unit.outcome:
            pair_id = id(unit)
            action_dict = {
                **base,
                "content": unit.content,
                "_action_pair_key": pair_id,
                "_confidence": unit.confidence,
            }
            if ctx:
                action_dict["context"] = ctx
            effect_dict = {
                **base,
                "content": unit.outcome,
                "_effect_pair_key": pair_id,
                "_confidence": unit.confidence,
            }
            if ctx:
                effect_dict["context"] = ctx
            result.append(action_dict)
            result.append(effect_dict)

        elif unit.unit_type == UnitType.EXPERIENCE and unit.expectation and unit.outcome:
            pair_id = id(unit)
            # Inherit tags and add "experience" tag to outcome-engram
            base_tags: list = list(base.get("tags") or [])
            expectation_dict = {
                **base,
                "content": unit.expectation,
                "_experience_pair_key": pair_id,
                "_confidence": unit.confidence,
            }
            if ctx:
                expectation_dict["context"] = ctx
            outcome_dict = {
                **base,
                "content": unit.outcome,
                "tags": base_tags + ["experience"],
                "expectation": unit.expectation,
                "outcome": unit.outcome,
                "_experience_pair_key": pair_id,
                "_confidence": unit.confidence,
            }
            if ctx:
                outcome_dict["context"] = ctx
            result.append(expectation_dict)
            result.append(outcome_dict)

        else:
            # FACT or incomplete ACTION_EFFECT/EXPERIENCE — single dict, original behaviour
            d = {**base, "content": unit.content}
            if ctx:
                d["context"] = ctx
            if unit.expectation is not None:
                d["expectation"] = unit.expectation
            if unit.outcome is not None:
                d["outcome"] = unit.outcome
            result.append(d)

    return result
