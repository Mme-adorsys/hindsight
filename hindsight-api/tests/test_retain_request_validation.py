"""
Pure unit tests for the mandatory-fields pydantic validators on RetainRequest
and MemoryItem (no live DB/API/LLM required).

Covers the strict-retain rules introduced after Phase B verification:
- RetainRequest.mode is mandatory and must be one of the 4 session modes
- RetainRequest.task_context is mandatory and must be at least 3 chars
- MemoryItem.expectation and .outcome must both be set or both be empty
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hindsight_api.api.http import MemoryItem, RetainRequest

# ---------------------------------------------------------------------------
# RetainRequest — mode / task_context mandatory
# ---------------------------------------------------------------------------


def _valid_item(**overrides) -> dict:
    base = {"content": "The MM-Dev-Stack runs on port 8889 with Anthropic Haiku 4.5."}
    base.update(overrides)
    return base


def test_retain_request_accepts_valid_minimal():
    req = RetainRequest(
        items=[MemoryItem(**_valid_item())],
        mode="precision",
        task_context="Dev-Stack bring-up",
    )
    assert req.mode == "precision"
    assert req.task_context == "Dev-Stack bring-up"
    assert len(req.items) == 1


def test_retain_request_rejects_missing_mode():
    with pytest.raises(ValidationError) as exc:
        RetainRequest(
            items=[MemoryItem(**_valid_item())],
            task_context="Dev-Stack bring-up",
        )
    assert "mode" in str(exc.value).lower()


def test_retain_request_rejects_missing_task_context():
    with pytest.raises(ValidationError) as exc:
        RetainRequest(
            items=[MemoryItem(**_valid_item())],
            mode="precision",
        )
    assert "task_context" in str(exc.value).lower()


def test_retain_request_rejects_empty_task_context():
    # min_length=3 on the field rejects both empty string and too-short values.
    with pytest.raises(ValidationError):
        RetainRequest(
            items=[MemoryItem(**_valid_item())],
            mode="precision",
            task_context="",
        )


def test_retain_request_rejects_short_task_context():
    with pytest.raises(ValidationError):
        RetainRequest(
            items=[MemoryItem(**_valid_item())],
            mode="precision",
            task_context="xy",  # 2 chars → below min_length
        )


def test_retain_request_rejects_invalid_mode():
    with pytest.raises(ValidationError) as exc:
        RetainRequest(
            items=[MemoryItem(**_valid_item())],
            mode="casual",  # not in the allowed Literal
            task_context="Dev-Stack bring-up",
        )
    # pydantic emits a literal error
    assert "literal_error" in str(exc.value) or "casual" in str(exc.value)


@pytest.mark.parametrize("mode", ["precision", "exploration", "analogy", "validation"])
def test_retain_request_accepts_all_valid_modes(mode):
    req = RetainRequest(
        items=[MemoryItem(**_valid_item())],
        mode=mode,
        task_context="Dev-Stack bring-up",
    )
    assert req.mode == mode


# ---------------------------------------------------------------------------
# MemoryItem — expectation / outcome pair validator
# ---------------------------------------------------------------------------


def test_memory_item_accepts_neither_expectation_nor_outcome():
    item = MemoryItem(content="Plain fact without an experience structure.")
    assert item.expectation is None
    assert item.outcome is None


def test_memory_item_accepts_both_expectation_and_outcome():
    item = MemoryItem(
        content="The first retain after API restart should land a trace.",
        expectation="Eight steps from r0 through r9",
        outcome="Only eight steps because REPLACE path skipped r8_schema_fit",
    )
    assert item.expectation is not None
    assert item.outcome is not None


def test_memory_item_rejects_expectation_without_outcome():
    with pytest.raises(ValidationError) as exc:
        MemoryItem(
            content="Half an experience.",
            expectation="Something should happen",
        )
    assert "expectation" in str(exc.value).lower() or "outcome" in str(exc.value).lower()


def test_memory_item_rejects_outcome_without_expectation():
    with pytest.raises(ValidationError) as exc:
        MemoryItem(
            content="Half an experience.",
            outcome="Something happened",
        )
    assert "expectation" in str(exc.value).lower() or "outcome" in str(exc.value).lower()


def test_memory_item_rejects_outcome_when_expectation_is_empty_string():
    """An empty string should count as 'not set' for the pair validator."""
    with pytest.raises(ValidationError):
        MemoryItem(
            content="Half an experience.",
            expectation="",
            outcome="Something happened",
        )
