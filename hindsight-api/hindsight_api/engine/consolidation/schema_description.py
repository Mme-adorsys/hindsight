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
    "Du bekommst statistisch aggregierte Properties eines wiederkehrenden Musters aus "
    "einem episodischen Memory-System. Schreibe EINEN präzisen Satz auf Deutsch, der "
    "die STRUKTUR des Musters beschreibt — kein Vorwort, keine Aufzählung, kein JSON.\n\n"
    "Lesart der Properties:\n"
    "- 'cluster': Setting-Etikett (z.B. 'coffee_morning' = Termine, die morgens BEI Kaffee "
    "stattfinden — nicht Termine ÜBER das Thema Kaffee).\n"
    "- 'format': Termin-Art ('1on1' = Zwei-Personen-Termin, 'group' = Gruppen-Meeting).\n"
    "- 'duration': Dauer in MINUTEN (30 = 30 Minuten, nicht Stunden).\n"
    "- 'time': Zeit-Etikett (morning/afternoon/friday/…).\n"
    "- 'mood': Charakter des Treffens (productive/casual/reflective/…).\n"
    "- 'domain': Sachgebiet bei deklarativen Faktum-Clustern.\n"
    "- 'shape': 'declarative' = wiederkehrende Fakt-Aussage, kein Ereignis.\n"
    "- Properties mit type='numeric' tragen min/max/mean — beschreibe sie als Richtwerte.\n"
    "- Properties mit type='categorical' und confidence=1.0 sind invariant für das Muster.\n\n"
    "Was die Properties NICHT enthalten: konkrete Inhalte einzelner Episoden. Beschreibe "
    "nur was wiederkehrt: das SETTING, den FORMAT-Rahmen, das Sachgebiet — NICHT was "
    "Beteiligte konkret besprochen haben.\n\n"
    "Properties (JSON):\n{properties_json}\n\n"
    "Evidence-Count (Anzahl Episoden im Cluster): {evidence_count}\n\n"
    "Antwort (genau 1 Satz, max. 200 Zeichen):"
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
