"""
Unit tests for Epic 05 Story 02 — Embedding Enrichment (R2).

Covers:
- augment_texts_with_context() backward compat (no session → same as augment_texts_with_dates)
- Session task_context added to augmented text
- Thalamus hint added when dominant score > 0.6
- No hint added when all scores <= 0.6
- get_dominant_thalamus_label() — all 4 dimensions, threshold, tie-breaking
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.response_models import RetrievalMode, Session
from hindsight_api.engine.retain.embedding_processing import (
    augment_texts_with_context,
    augment_texts_with_dates,
    get_dominant_thalamus_label,
)
from hindsight_api.engine.retain.types import ExtractedFact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(text: str = "The sky is blue.", thalamus_scores: ThalamusScores | None = None) -> ExtractedFact:
    return ExtractedFact(
        fact_text=text,
        fact_type="world",
        mentioned_at=datetime(2026, 4, 4, 10, 0, 0, tzinfo=UTC),
        thalamus_scores=thalamus_scores,
    )


def _format_date(dt) -> str:
    return "April 2026" if dt else "unknown"


def _session(task_context: str | None = None, mode: RetrievalMode = RetrievalMode.PRECISION) -> Session:
    return Session(mode=mode, task_context=task_context)


def _scores(n=0.0, s=0.0, tr=0.0, ev=0.0, overall=0.0) -> ThalamusScores:
    return ThalamusScores(novelty=n, surprise=s, task_relevance=tr, emotional_valence=ev, overall=overall)


# ---------------------------------------------------------------------------
# get_dominant_thalamus_label
# ---------------------------------------------------------------------------


class TestGetDominantThalamusLabel:
    def test_high_novelty_returns_label(self):
        scores = _scores(n=0.8)
        assert get_dominant_thalamus_label(scores) == "high novelty"

    def test_high_surprise_returns_label(self):
        scores = _scores(s=0.9)
        assert get_dominant_thalamus_label(scores) == "surprising"

    def test_high_task_relevance_returns_label(self):
        scores = _scores(tr=0.75)
        assert get_dominant_thalamus_label(scores) == "task relevant"

    def test_high_emotional_valence_returns_label(self):
        scores = _scores(ev=0.7)
        assert get_dominant_thalamus_label(scores) == "emotionally significant"

    def test_exactly_at_threshold_returns_none(self):
        # 0.6 is NOT above threshold (strictly >)
        scores = _scores(n=0.6)
        assert get_dominant_thalamus_label(scores) == None

    def test_just_above_threshold_returns_label(self):
        scores = _scores(n=0.61)
        assert get_dominant_thalamus_label(scores) == "high novelty"

    def test_all_low_returns_none(self):
        scores = _scores(n=0.3, s=0.2, tr=0.4, ev=0.1)
        assert get_dominant_thalamus_label(scores) == None

    def test_all_zero_returns_none(self):
        scores = _scores()
        assert get_dominant_thalamus_label(scores) == None

    def test_tie_picks_highest_deterministically(self):
        # Two equal scores above threshold — max() picks first key in iteration order
        scores = _scores(n=0.8, s=0.8)
        label = get_dominant_thalamus_label(scores)
        assert label in ("high novelty", "surprising")

    def test_winner_must_be_strictly_highest(self):
        # tr=0.9 is the clear winner
        scores = _scores(n=0.7, s=0.65, tr=0.9, ev=0.5)
        assert get_dominant_thalamus_label(scores) == "task relevant"


# ---------------------------------------------------------------------------
# augment_texts_with_context — backward compat
# ---------------------------------------------------------------------------


class TestAugmentTextsWithContextBackwardCompat:
    def test_no_session_matches_augment_with_dates(self):
        facts = [_fact()]
        result_new = augment_texts_with_context(facts, _format_date, session=None)
        result_old = augment_texts_with_dates(facts, _format_date)
        assert result_new == result_old

    def test_session_without_task_context_matches_augment_with_dates(self):
        facts = [_fact()]
        session = _session(task_context=None)
        result_new = augment_texts_with_context(facts, _format_date, session=session)
        result_old = augment_texts_with_dates(facts, _format_date)
        assert result_new == result_old

    def test_empty_facts_returns_empty_list(self):
        assert augment_texts_with_context([], _format_date) == []

    def test_multiple_facts_same_length(self):
        facts = [_fact("Fact one."), _fact("Fact two."), _fact("Fact three.")]
        result = augment_texts_with_context(facts, _format_date)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# augment_texts_with_context — session context enrichment
# ---------------------------------------------------------------------------


class TestAugmentTextsWithContextSession:
    def test_task_context_appended(self):
        facts = [_fact()]
        session = _session(task_context="debugging API performance")
        result = augment_texts_with_context(facts, _format_date, session=session)
        assert "[context: debugging API performance]" in result[0]

    def test_task_context_comes_after_date(self):
        facts = [_fact("A fact.")]
        session = _session(task_context="my task")
        result = augment_texts_with_context(facts, _format_date, session=session)
        date_pos = result[0].index("(happened in")
        context_pos = result[0].index("[context:")
        assert date_pos < context_pos

    def test_empty_task_context_not_added(self):
        facts = [_fact()]
        session = _session(task_context="")
        result = augment_texts_with_context(facts, _format_date, session=session)
        assert "[context:" not in result[0]


# ---------------------------------------------------------------------------
# augment_texts_with_context — thalamus hint enrichment
# ---------------------------------------------------------------------------


class TestAugmentTextsWithContextThalamusHint:
    def test_high_novelty_adds_hint(self):
        facts = [_fact(thalamus_scores=_scores(n=0.9))]
        result = augment_texts_with_context(facts, _format_date)
        assert "[high novelty]" in result[0]

    def test_low_scores_no_hint(self):
        facts = [_fact(thalamus_scores=_scores(n=0.3, s=0.2, tr=0.4, ev=0.1))]
        result = augment_texts_with_context(facts, _format_date)
        assert "[" not in result[0]

    def test_no_thalamus_scores_no_hint(self):
        facts = [_fact(thalamus_scores=None)]
        result = augment_texts_with_context(facts, _format_date)
        assert "[" not in result[0]

    def test_hint_comes_after_date_and_context(self):
        facts = [_fact(thalamus_scores=_scores(n=0.9))]
        session = _session(task_context="some task")
        result = augment_texts_with_context(facts, _format_date, session=session)
        date_pos = result[0].index("(happened in")
        context_pos = result[0].index("[context:")
        hint_pos = result[0].index("[high novelty]")
        assert date_pos < context_pos < hint_pos

    def test_all_enrichments_present(self):
        facts = [_fact("Important event.", thalamus_scores=_scores(tr=0.8))]
        session = _session(task_context="performance review")
        result = augment_texts_with_context(facts, _format_date, session=session)
        text = result[0]
        assert "Important event." in text
        assert "(happened in April 2026)" in text
        assert "[context: performance review]" in text
        assert "[task relevant]" in text
