"""Unit tests for Epic 25 Story 08 — schema_description.

Pure unit: the LLM caller is an injected async callable, so the test
suite drives both happy path (LLM returns prose) and failure path
(LLM raises → template fallback) without a real network round-trip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api.engine.consolidation.c2_pattern_recognition import (
    CreationPayload,
    attach_descriptions,
)
from hindsight_api.engine.consolidation.schema_description import (
    MAX_DESCRIPTION_CHARS,
    PROMPT_TEMPLATE,
    _strip_internals,
    _template_description,
    generate_schema_description,
)

_PROPS = {
    "evidence_count": 8,
    "activity": {"type": "categorical", "value": "coffee", "count": 8, "confidence": 1.0},
    "mood": {"type": "categorical", "value": "productive", "count": 7, "confidence": 0.875},
    "duration_minutes": {"type": "numeric", "mean": 44, "median": 44, "min": 40, "max": 47, "count": 8},
    "event_time": {
        "type": "temporal",
        "min_iso": "2026-05-01T14:00:00",
        "max_iso": "2026-05-08T14:00:00",
        "count": 8,
    },
}


# ---------------------------------------------------------------------------
# generate_schema_description — happy path + fallbacks
# ---------------------------------------------------------------------------


class TestGenerateSchemaDescription:
    async def test_llm_happy_path_returns_trimmed_string(self):
        llm = AsyncMock(return_value="  Coffee-Meetings am Nachmittag (~44min, produktiv).  ")
        result = await generate_schema_description(_PROPS, evidence_count=8, llm_caller=llm)
        assert result == "Coffee-Meetings am Nachmittag (~44min, produktiv)."
        # Prompt must include the JSON-rendered properties + evidence count.
        prompt = llm.await_args.args[0]
        assert "Evidence-Count (number of episodes): 8" in prompt
        assert "activity" in prompt and "coffee" in prompt
        # evidence_count itself is not duplicated inside the JSON payload.
        assert '"evidence_count"' not in prompt

    async def test_llm_failure_falls_back_to_template(self):
        async def _raise(_prompt):
            raise RuntimeError("synthetic LLM outage")

        result = await generate_schema_description(_PROPS, evidence_count=8, llm_caller=_raise)
        assert result.startswith("Muster über 8 Engrams")
        assert "activity=coffee" in result
        assert "duration_minutes~44" in result

    async def test_llm_returns_empty_falls_back_to_template(self):
        llm = AsyncMock(return_value="   ")
        result = await generate_schema_description(_PROPS, evidence_count=8, llm_caller=llm)
        assert result.startswith("Muster über 8 Engrams")

    async def test_no_llm_caller_uses_template_directly(self):
        result = await generate_schema_description(_PROPS, evidence_count=8, llm_caller=None)
        assert "activity=coffee" in result
        assert "event_time=2026-05-01T14:00:00..2026-05-08T14:00:00" in result

    async def test_empty_properties_returns_empty(self):
        assert await generate_schema_description({}, evidence_count=0, llm_caller=AsyncMock()) == ""

    async def test_zero_evidence_returns_empty(self):
        assert await generate_schema_description(_PROPS, evidence_count=0, llm_caller=AsyncMock()) == ""

    async def test_response_truncated_at_cap(self):
        long_text = "a" * (MAX_DESCRIPTION_CHARS + 50)
        llm = AsyncMock(return_value=long_text)
        result = await generate_schema_description(_PROPS, evidence_count=1, llm_caller=llm)
        assert len(result) == MAX_DESCRIPTION_CHARS


# ---------------------------------------------------------------------------
# _template_description — deterministic fallback shape
# ---------------------------------------------------------------------------


class TestTemplateDescription:
    def test_includes_all_three_property_types(self):
        text = _template_description(_PROPS, 8)
        assert "activity=coffee" in text
        assert "duration_minutes~44" in text
        assert "event_time=2026-05-01T14:00:00..2026-05-08T14:00:00" in text

    def test_evidence_count_in_lead(self):
        text = _template_description(_PROPS, 12)
        assert text.startswith("Muster über 12 Engrams")

    def test_only_evidence_count_yields_no_properties_message(self):
        text = _template_description({"evidence_count": 3}, 3)
        assert text == "Muster über 3 Engrams (keine Properties)"

    def test_skips_non_dict_values(self):
        # Defensive: a stray string value shouldn't crash.
        out = _template_description({"weird": "not a dict"}, 1)
        assert "Muster über 1 Engrams" in out


class TestStripInternals:
    def test_removes_evidence_count_only(self):
        stripped = _strip_internals(_PROPS)
        assert "evidence_count" not in stripped
        assert set(stripped.keys()) == {"activity", "mood", "duration_minutes", "event_time"}


# ---------------------------------------------------------------------------
# attach_descriptions — pipeline integration
# ---------------------------------------------------------------------------


def _payload(*, properties: dict | None = None) -> CreationPayload:
    return CreationPayload(
        cluster=MagicMock(name="MaturedClusterCandidate"),
        properties=properties or _PROPS,
    )


class TestAttachDescriptions:
    async def test_fills_descriptions_with_llm(self):
        llm = AsyncMock(return_value="A short sentence.")
        out = await attach_descriptions((_payload(), _payload()), llm_caller=llm)
        assert len(out) == 2
        assert all(p.description == "A short sentence." for p in out)
        # Sequential, not gathered — the same llm mock is awaited twice.
        assert llm.await_count == 2

    async def test_template_path_when_no_llm(self):
        out = await attach_descriptions((_payload(),), llm_caller=None)
        assert "Muster über" in out[0].description

    async def test_empty_payloads_short_circuits(self):
        llm = AsyncMock()
        out = await attach_descriptions((), llm_caller=llm)
        assert out == ()
        llm.assert_not_called()


# ---------------------------------------------------------------------------
# Prompt-shape sanity (drift guard)
# ---------------------------------------------------------------------------


def test_prompt_template_carries_required_placeholders():
    # If the prompt loses one of these the .format() call breaks at runtime;
    # this is a cheap drift guard so a future docs polish can't silently break C2.
    assert "{properties_json}" in PROMPT_TEMPLATE
    assert "{evidence_count}" in PROMPT_TEMPLATE


@pytest.mark.parametrize("cap", [MAX_DESCRIPTION_CHARS])
def test_max_description_chars_is_sane(cap):
    # Loose upper bound — keeps Schema.description from hosting paragraphs.
    assert 80 <= cap <= 1000
