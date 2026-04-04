"""
Unit tests for Epic 05 Story 03 — Score-aware Deduplication (R3).

Covers:
- resolve_duplicate(): KEEP / DROP / REPLACE decisions
- Strength bonus reduces effective existing score
- No thalamus scores → score treated as 0.0 (new fact wins on tie)
- resolve_duplicates_batch(): mixed batches, empty input
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.retain.deduplication import (
    DuplicateResolution,
    DuplicateResult,
    ReplacementAction,
    resolve_duplicate,
    resolve_duplicates_batch,
)
from hindsight_api.engine.retain.types import ProcessedFact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scores(overall: float = 0.0) -> ThalamusScores:
    return ThalamusScores(
        novelty=0.0,
        surprise=0.0,
        task_relevance=0.0,
        emotional_valence=0.0,
        overall=overall,
    )


_TS = datetime(2026, 4, 4, 10, 0, 0, tzinfo=UTC)


def _fact(overall: float | None = None) -> ProcessedFact:
    ts = _scores(overall) if overall is not None else None
    return ProcessedFact(
        fact_text="The sky is blue.",
        fact_type="world",
        embedding=[0.1] * 384,
        occurred_start=None,
        occurred_end=None,
        mentioned_at=_TS,
        context="",
        metadata={},
        thalamus_scores=ts,
    )


def _dup(
    existing_score: float | None = None,
    existing_strength: float | None = None,
    unit_id: str = "00000000-0000-0000-0000-000000000001",
) -> DuplicateResult:
    return DuplicateResult(
        is_duplicate=True,
        existing_unit_id=unit_id,
        existing_score=existing_score,
        existing_strength=existing_strength,
        similarity=0.97,
    )


def _not_dup() -> DuplicateResult:
    return DuplicateResult(is_duplicate=False, similarity=0.3)


# ---------------------------------------------------------------------------
# TestResolveDuplicate
# ---------------------------------------------------------------------------


class TestResolveDuplicate:
    def test_not_duplicate_always_keep(self):
        assert resolve_duplicate(_fact(0.0), _not_dup()) == DuplicateResolution.KEEP

    def test_not_duplicate_high_score_still_keep(self):
        assert resolve_duplicate(_fact(0.9), _not_dup()) == DuplicateResolution.KEEP

    def test_new_higher_score_replaces(self):
        fact = _fact(0.8)
        dr = _dup(existing_score=0.5, existing_strength=0.0)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.REPLACE

    def test_equal_score_replaces_frischer_wins(self):
        fact = _fact(0.5)
        dr = _dup(existing_score=0.5, existing_strength=0.0)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.REPLACE

    def test_lower_score_drops(self):
        fact = _fact(0.3)
        dr = _dup(existing_score=0.6, existing_strength=0.0)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.DROP

    def test_no_thalamus_scores_treated_as_zero(self):
        fact = _fact(overall=None)  # thalamus_scores=None → new_score=0.0
        dr = _dup(existing_score=0.1, existing_strength=0.0)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.DROP

    def test_no_thalamus_scores_existing_also_zero_replaces(self):
        # 0.0 >= 0.0 → REPLACE (frischer gewinnt)
        fact = _fact(overall=None)
        dr = _dup(existing_score=None, existing_strength=None)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.REPLACE

    def test_strength_bonus_protects_existing(self):
        # existing_score=0.5, strength=1.0 → bonus=0.1 → effective=0.6
        # new_score=0.5 < 0.6 → DROP
        fact = _fact(0.5)
        dr = _dup(existing_score=0.5, existing_strength=1.0)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.DROP

    def test_strength_bonus_just_below_new_score_replaces(self):
        # existing_score=0.5, strength=0.5 → bonus=0.05 → effective=0.55
        # new_score=0.56 > 0.55 → REPLACE
        fact = _fact(0.56)
        dr = _dup(existing_score=0.5, existing_strength=0.5)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.REPLACE

    def test_existing_score_none_treated_as_zero(self):
        # existing_score=None → 0.0, new_score=0.5 → REPLACE
        fact = _fact(0.5)
        dr = _dup(existing_score=None, existing_strength=None)
        assert resolve_duplicate(fact, dr) == DuplicateResolution.REPLACE


# ---------------------------------------------------------------------------
# TestResolvesDuplicatesBatch
# ---------------------------------------------------------------------------


class TestResolvesDuplicatesBatch:
    def test_empty_input(self):
        facts, replacements = resolve_duplicates_batch([], [])
        assert facts == []
        assert replacements == []

    def test_single_keep(self):
        fact = _fact(0.5)
        facts, replacements = resolve_duplicates_batch([fact], [_not_dup()])
        assert facts == [fact]
        assert replacements == []

    def test_single_drop(self):
        fact = _fact(0.3)
        dr = _dup(existing_score=0.9, existing_strength=0.0)
        facts, replacements = resolve_duplicates_batch([fact], [dr])
        assert facts == []
        assert replacements == []

    def test_single_replace(self):
        fact = _fact(0.9)
        dr = _dup(existing_score=0.4, existing_strength=0.0)
        facts, replacements = resolve_duplicates_batch([fact], [dr])
        assert facts == [fact]
        assert len(replacements) == 1
        assert replacements[0].existing_unit_id == dr.existing_unit_id
        assert replacements[0].new_fact is fact

    def test_mixed_batch(self):
        f_keep = _fact(0.5)
        f_drop = _fact(0.2)
        f_replace = _fact(0.9)
        dr_not_dup = _not_dup()
        dr_drop = _dup(existing_score=0.8, existing_strength=0.0, unit_id="00000000-0000-0000-0000-000000000001")
        dr_replace = _dup(existing_score=0.3, existing_strength=0.0, unit_id="00000000-0000-0000-0000-000000000002")

        facts, replacements = resolve_duplicates_batch(
            [f_keep, f_drop, f_replace],
            [dr_not_dup, dr_drop, dr_replace],
        )

        assert facts == [f_keep, f_replace]
        assert len(replacements) == 1
        assert replacements[0].new_fact is f_replace
        assert replacements[0].existing_unit_id == "00000000-0000-0000-0000-000000000002"

    def test_replace_fact_included_in_facts_to_store(self):
        """REPLACE facts must appear in facts_to_store so the pipeline processes them."""
        fact = _fact(0.8)
        dr = _dup(existing_score=0.2, existing_strength=0.0)
        facts, replacements = resolve_duplicates_batch([fact], [dr])
        assert fact in facts
        assert any(ra.new_fact is fact for ra in replacements)

    def test_drop_fact_excluded_from_facts_to_store(self):
        fact = _fact(0.1)
        dr = _dup(existing_score=0.9, existing_strength=0.0)
        facts, _ = resolve_duplicates_batch([fact], [dr])
        assert fact not in facts
