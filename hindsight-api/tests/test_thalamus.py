"""
Unit tests for ThalamusFilter (Epic 16 — Objective Thalamus).

Tests cover:
- Score computation with known inputs (embedding-based, deterministic)
- Mode-dependent weighting
- Threshold values per mode
- Fallback behaviour when session context is absent
- Session fallback hierarchy (item-level > session-level > neutral default)
- Determinism: same inputs → same outputs (no LLM variance)
- _cosine_similarity helper
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.response_models import RetrievalMode, Session
from hindsight_api.engine.thalamus import (
    DEFAULT_THRESHOLD_ANALOGY,
    DEFAULT_THRESHOLD_EXPLORATION,
    DEFAULT_THRESHOLD_PRECISION,
    DEFAULT_THRESHOLD_VALIDATION,
    MODE_WEIGHTS,
    VALENCE_AMPLIFICATION,
    ThalamusFilter,
    _cosine_similarity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_filter(
    qdrant_results: list[dict] | None = None,
    embed_return: list[list[float]] | None = None,
) -> tuple[ThalamusFilter, MagicMock, MagicMock]:
    """Build a ThalamusFilter with fully mocked dependencies."""
    qdrant = AsyncMock()
    qdrant.search_similar = AsyncMock(return_value=qdrant_results or [])

    embeddings = MagicMock()
    vecs = embed_return if embed_return is not None else [[1.0, 0.0, 0.0]]
    embeddings.encode = MagicMock(return_value=vecs)

    f = ThalamusFilter(qdrant=qdrant, embeddings=embeddings)
    return f, qdrant, embeddings


def _session(
    mode: RetrievalMode = RetrievalMode.PRECISION,
    expectation: str | None = None,
    task_context: str | None = None,
) -> Session:
    return Session(mode=mode, current_expectation=expectation, task_context=task_context)


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_antiparallel_vectors(self):
        # cos(180°) = -1, but embedding models produce non-negative cosine
        sim = _cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        assert sim == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Novelty score
# ---------------------------------------------------------------------------


class TestNoveltyScore:
    @pytest.mark.asyncio
    async def test_no_existing_memories_returns_one(self):
        f, qdrant, _ = _make_filter(qdrant_results=[])
        session = _session()
        scores = await f.score("hello world", session)
        assert scores.novelty == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_high_similarity_returns_low_novelty(self):
        # similarity = 0.95 → novelty = 0.05
        f, qdrant, _ = _make_filter(qdrant_results=[{"score": 0.95, "engram_id": "x", "payload": {}}])
        session = _session()
        scores = await f.score("hello world", session)
        assert scores.novelty == pytest.approx(0.05, abs=1e-6)

    @pytest.mark.asyncio
    async def test_qdrant_failure_defaults_to_one(self):
        f, qdrant, _ = _make_filter()
        qdrant.search_similar = AsyncMock(side_effect=RuntimeError("connection refused"))
        session = _session()
        scores = await f.score("hello world", session)
        assert scores.novelty == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_max_similarity_used_not_average(self):
        # Multiple results — only max matters
        results = [
            {"score": 0.4, "engram_id": "a", "payload": {}},
            {"score": 0.9, "engram_id": "b", "payload": {}},
            {"score": 0.6, "engram_id": "c", "payload": {}},
        ]
        f, _, _ = _make_filter(qdrant_results=results)
        session = _session()
        scores = await f.score("text", session)
        assert scores.novelty == pytest.approx(0.1, abs=1e-6)

    @pytest.mark.asyncio
    async def test_novelty_passes_bank_id_filter_to_qdrant(self):
        f, qdrant, _ = _make_filter(qdrant_results=[])
        session = _session()
        await f.score("hello", session, bank_id="test-bank")
        call_args = qdrant.search_similar.call_args
        assert call_args is not None
        filters = call_args.kwargs.get("filters") or (call_args.args[2] if len(call_args.args) > 2 else None)
        assert filters is not None
        assert "must" in filters

    @pytest.mark.asyncio
    async def test_novelty_no_bank_id_searches_without_filter(self):
        f, qdrant, _ = _make_filter(qdrant_results=[])
        session = _session()
        await f.score("hello", session, bank_id=None)
        call_args = qdrant.search_similar.call_args
        filters = call_args.kwargs.get("filters")
        assert filters is None


# ---------------------------------------------------------------------------
# Surprise score — expectation↔outcome Prediction Error
# ---------------------------------------------------------------------------


class TestSurpriseScore:
    @pytest.mark.asyncio
    async def test_no_expectation_no_outcome_returns_neutral(self):
        f, _, _ = _make_filter()
        session = _session(expectation=None)
        scores = await f.score("anything", session)
        assert scores.surprise == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_identical_expectation_and_outcome_returns_low_surprise(self):
        # expectation and outcome have the same embedding → cosine=1 → surprise=0
        vec = [1.0, 0.0, 0.0]
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[vec, vec, vec])
        session = _session()
        scores = await f.score("content", session, expectation="same", outcome="same")
        assert scores.surprise == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_orthogonal_expectation_and_outcome_returns_high_surprise(self):
        # expectation=[1,0], outcome=[0,1] → cosine=0 → surprise=1
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        session = _session()
        scores = await f.score("content", session, expectation="expected", outcome="different")
        assert scores.surprise == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_expectation_without_outcome_returns_neutral(self):
        # expectation set but no outcome → fallback to 0.5
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [0.5, 0.5]])
        session = _session(expectation="session_expectation")
        scores = await f.score("content", session)  # no outcome
        assert scores.surprise == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Task-Relevance score
# ---------------------------------------------------------------------------


class TestTaskRelevanceScore:
    @pytest.mark.asyncio
    async def test_no_task_context_returns_neutral(self):
        f, _, _ = _make_filter()
        session = _session(task_context=None)
        scores = await f.score("text", session)
        assert scores.task_relevance == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_identical_task_context_returns_high_relevance(self):
        vec = [1.0, 0.0, 0.0]
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[vec, vec])
        session = _session()
        scores = await f.score("same task", session, context="same task")
        assert scores.task_relevance == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_orthogonal_task_context_returns_low_relevance(self):
        # content=[1,0], context=[0,1] → cosine=0 → relevance=0
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
        session = _session()
        scores = await f.score("input", session, context="unrelated task")
        assert scores.task_relevance == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Emotional Valence score — embedding-based, deterministic
# ---------------------------------------------------------------------------


class TestEmotionalValenceScore:
    @pytest.mark.asyncio
    async def test_high_prediction_error_gives_high_valence(self):
        # expectation=[1,0], outcome=[0,1] → cosine=0 → PE=1.0
        # valence = min(1.0, 1.0 * VALENCE_AMPLIFICATION) = 1.0
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        session = _session()
        scores = await f.score("content", session, expectation="expected", outcome="different")
        assert scores.emotional_valence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_zero_prediction_error_gives_zero_valence(self):
        # expectation = outcome → cosine=1 → PE=0 → valence=0
        vec = [1.0, 0.0, 0.0]
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[vec, vec, vec])
        session = _session()
        scores = await f.score("content", session, expectation="same", outcome="same")
        assert scores.emotional_valence == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_amplification_factor_applied(self):
        # cosine(a, b) = 0.5 → PE = 0.5 → valence = min(1.0, 0.5 * VALENCE_AMPLIFICATION)
        a = [1.0, 0.0]
        b = [0.5, math.sqrt(0.75)]  # cos([1,0], b) = 0.5
        f, _, embeddings = _make_filter()
        # encode([content, expectation, outcome]) → [a, a, b]
        embeddings.encode = MagicMock(return_value=[a, a, b])
        session = _session()
        scores = await f.score("content", session, expectation="exp", outcome="out")
        expected = min(1.0, 0.5 * VALENCE_AMPLIFICATION)
        assert scores.emotional_valence == pytest.approx(expected, abs=1e-3)

    @pytest.mark.asyncio
    async def test_no_expectation_no_outcome_returns_fallback(self):
        f, _, _ = _make_filter()
        session = _session()
        scores = await f.score("text", session)
        assert scores.emotional_valence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_expectation_without_outcome_returns_fallback(self):
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [1.0, 0.0]])
        session = _session(expectation="expected")
        scores = await f.score("text", session)  # no outcome
        assert scores.emotional_valence == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Mode-dependent weighting
# ---------------------------------------------------------------------------


class TestModeWeights:
    def test_all_four_modes_defined(self):
        for mode in (
            RetrievalMode.EXPLORATION,
            RetrievalMode.PRECISION,
            RetrievalMode.VALIDATION,
            RetrievalMode.ANALOGY,
        ):
            assert mode in MODE_WEIGHTS

    def test_weights_sum_to_one(self):
        for mode, weights in MODE_WEIGHTS.items():
            total = sum(weights.values())
            assert total == pytest.approx(1.0), f"Mode {mode} weights sum to {total}"

    @pytest.mark.asyncio
    async def test_exploration_novelty_has_highest_weight(self):
        weights = MODE_WEIGHTS[RetrievalMode.EXPLORATION]
        assert weights["novelty"] == max(weights.values())

    @pytest.mark.asyncio
    async def test_precision_relevance_has_highest_weight(self):
        weights = MODE_WEIGHTS[RetrievalMode.PRECISION]
        assert weights["task_relevance"] == max(weights.values())

    @pytest.mark.asyncio
    async def test_validation_surprise_has_highest_weight(self):
        weights = MODE_WEIGHTS[RetrievalMode.VALIDATION]
        assert weights["surprise"] == max(weights.values())

    @pytest.mark.asyncio
    async def test_overall_score_computed_from_weights(self):
        # Fix all component scores to known values, verify overall formula
        f, _, _ = _make_filter()

        # Patch private methods — note: _score_surprise etc. are now sync
        f._score_novelty = AsyncMock(return_value=1.0)
        f._score_surprise = MagicMock(return_value=0.0)
        f._score_task_relevance = MagicMock(return_value=0.0)
        f._score_emotional_valence = MagicMock(return_value=0.0)

        session = _session(mode=RetrievalMode.EXPLORATION)
        scores = await f.score("text", session)

        expected_overall = MODE_WEIGHTS[RetrievalMode.EXPLORATION]["novelty"] * 1.0
        assert scores.overall == pytest.approx(expected_overall)


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_default_thresholds(self):
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION) == pytest.approx(DEFAULT_THRESHOLD_PRECISION)
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.EXPLORATION) == pytest.approx(
            DEFAULT_THRESHOLD_EXPLORATION
        )
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.VALIDATION) == pytest.approx(
            DEFAULT_THRESHOLD_VALIDATION
        )
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.ANALOGY) == pytest.approx(DEFAULT_THRESHOLD_ANALOGY)

    def test_exploration_threshold_lower_than_precision(self):
        assert DEFAULT_THRESHOLD_EXPLORATION < DEFAULT_THRESHOLD_PRECISION

    def test_env_var_override(self, monkeypatch):
        # Patch the module-level dict directly to simulate an env-var override.
        import hindsight_api.engine.thalamus as thalamus_mod

        monkeypatch.setitem(thalamus_mod.MODE_THRESHOLDS, RetrievalMode.PRECISION, 0.99)
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION) == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# Session fallback hierarchy
# ---------------------------------------------------------------------------


class TestSessionFallback:
    @pytest.mark.asyncio
    async def test_item_expectation_overrides_session_expectation(self):
        """Item-level expectation takes precedence over session.current_expectation."""
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        session = _session(expectation="session_expectation")
        await f.score("content", session, expectation="item_exp", outcome="out")
        call_texts = embeddings.encode.call_args[0][0]
        assert "item_exp" in call_texts
        assert "session_expectation" not in call_texts

    @pytest.mark.asyncio
    async def test_session_expectation_used_as_fallback(self):
        """session.current_expectation used when no item-level expectation."""
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [1.0, 0.0]])
        session = _session(expectation="session_exp")
        await f.score("content", session)  # no item expectation, no outcome
        call_texts = embeddings.encode.call_args[0][0]
        assert "session_exp" in call_texts

    @pytest.mark.asyncio
    async def test_item_context_overrides_session_context(self):
        """Item-level context takes precedence over session.task_context."""
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
        session = _session(task_context="session_context")
        await f.score("content", session, context="item_context")
        call_texts = embeddings.encode.call_args[0][0]
        assert "item_context" in call_texts
        assert "session_context" not in call_texts

    @pytest.mark.asyncio
    async def test_session_context_used_as_fallback(self):
        """session.task_context used when no item-level context."""
        f, _, embeddings = _make_filter()
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
        session = _session(task_context="session_ctx")
        await f.score("content", session)  # no item context
        call_texts = embeddings.encode.call_args[0][0]
        assert "session_ctx" in call_texts

    @pytest.mark.asyncio
    async def test_outcome_has_no_session_fallback(self):
        """outcome is never sourced from session — always item-specific or None."""
        f, _, embeddings = _make_filter()
        # Only content in encode (no context, no expectation, no outcome)
        embeddings.encode = MagicMock(return_value=[[1.0, 0.0]])
        session = _session()  # no expectation, no task_context
        scores = await f.score("content", session)
        # No outcome → valence and surprise fall back to neutral/fallback values
        assert scores.surprise == pytest.approx(0.5)
        assert scores.emotional_valence == pytest.approx(0.3)
        call_texts = embeddings.encode.call_args[0][0]
        assert len(call_texts) == 1  # only content embedded


# ---------------------------------------------------------------------------
# Fallback: no session context
# ---------------------------------------------------------------------------


class TestFallbackBehaviour:
    @pytest.mark.asyncio
    async def test_session_without_context_produces_valid_scores(self):
        f, _, _ = _make_filter()
        session = _session()  # no expectation, no task_context
        scores = await f.score("some episode", session)

        assert isinstance(scores, ThalamusScores)
        assert 0.0 <= scores.novelty <= 1.0
        assert 0.0 <= scores.surprise <= 1.0
        assert 0.0 <= scores.task_relevance <= 1.0
        assert 0.0 <= scores.emotional_valence <= 1.0
        assert 0.0 <= scores.overall <= 1.0

    @pytest.mark.asyncio
    async def test_missing_context_gives_neutral_surprise_and_relevance(self):
        f, _, _ = _make_filter()
        session = _session(expectation=None, task_context=None)
        scores = await f.score("episode", session)
        assert scores.surprise == pytest.approx(0.5)
        assert scores.task_relevance == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Gate behaviour — threshold-based pass/drop logic
# ---------------------------------------------------------------------------


class TestGateBehaviour:
    @pytest.mark.asyncio
    async def test_high_overall_score_passes_threshold(self):
        """Episode with overall score above threshold should not be dropped."""
        f, _, _ = _make_filter()
        f._score_novelty = AsyncMock(return_value=1.0)
        f._score_surprise = MagicMock(return_value=1.0)
        f._score_task_relevance = MagicMock(return_value=1.0)
        f._score_emotional_valence = MagicMock(return_value=0.9)

        session = _session(mode=RetrievalMode.PRECISION)
        scores = await f.score("highly novel content", session)
        threshold = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)

        assert scores.overall >= threshold, f"Expected score {scores.overall:.3f} >= threshold {threshold:.3f}"

    @pytest.mark.asyncio
    async def test_low_overall_score_fails_threshold(self):
        """Episode with very low scores should fall below threshold."""
        f, _, _ = _make_filter()
        f._score_novelty = AsyncMock(return_value=0.0)
        f._score_surprise = MagicMock(return_value=0.0)
        f._score_task_relevance = MagicMock(return_value=0.0)
        f._score_emotional_valence = MagicMock(return_value=0.0)

        session = _session(mode=RetrievalMode.PRECISION)
        scores = await f.score("completely irrelevant duplicate", session)
        threshold = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)

        assert scores.overall < threshold, f"Expected score {scores.overall:.3f} < threshold {threshold:.3f}"

    def test_exploration_threshold_lower_than_precision_threshold(self):
        """Exploration mode has lower threshold → lets more through."""
        t_exploration = ThalamusFilter.threshold_for_mode(RetrievalMode.EXPLORATION)
        t_precision = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)
        assert t_exploration < t_precision

    @pytest.mark.asyncio
    async def test_scores_returned_for_passed_content(self):
        """Scores from ThalamusFilter should be fully populated for passed content."""
        f, _, _ = _make_filter()
        # Main scoring path now calls `_score_novelty_with_source` which
        # returns (novelty, max_similar_id, max_similarity). The
        # backwards-compatible `_score_novelty` delegates to it.
        f._score_novelty_with_source = AsyncMock(return_value=(0.8, "sim-engram-1", 0.2))
        f._score_surprise = MagicMock(return_value=0.5)
        f._score_task_relevance = MagicMock(return_value=0.6)
        f._score_emotional_valence = MagicMock(return_value=0.7)

        session = _session(mode=RetrievalMode.EXPLORATION)
        scores = await f.score("interesting content", session)

        assert isinstance(scores, ThalamusScores)
        assert scores.novelty == pytest.approx(0.8)
        assert scores.surprise == pytest.approx(0.5)
        assert scores.task_relevance == pytest.approx(0.6)
        assert scores.emotional_valence == pytest.approx(0.7)
        assert 0.0 <= scores.overall <= 1.0
        # Rationale should be attached and reflect the mocked novelty source.
        assert scores.rationale is not None
        assert scores.rationale.novelty_max_similar_id == "sim-engram-1"
        assert scores.rationale.novelty_max_similarity == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_mode_switch_changes_overall_score(self):
        """Same raw scores produce different overall score under different modes."""
        f_exp, _, _ = _make_filter()
        f_prec, _, _ = _make_filter()

        # novelty=1.0, all others=0.0 → exploration weights novelty heavily
        for f in (f_exp, f_prec):
            f._score_novelty = AsyncMock(return_value=1.0)
            f._score_surprise = MagicMock(return_value=0.0)
            f._score_task_relevance = MagicMock(return_value=0.0)
            f._score_emotional_valence = MagicMock(return_value=0.0)

        scores_exploration = await f_exp.score("text", _session(mode=RetrievalMode.EXPLORATION))
        scores_precision = await f_prec.score("text", _session(mode=RetrievalMode.PRECISION))

        # Exploration weights novelty at 0.4, Precision at 0.1
        assert scores_exploration.overall > scores_precision.overall


# ---------------------------------------------------------------------------
# Determinism — no LLM variance
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_inputs_produce_same_outputs(self):
        """Embedding-based scoring is fully deterministic — no LLM variance."""
        vec_content = [1.0, 0.0, 0.0]
        vec_exp = [0.7, 0.7, 0.0]
        vec_out = [0.0, 0.0, 1.0]

        results = []
        for _ in range(3):
            f, _, embeddings = _make_filter()
            embeddings.encode = MagicMock(return_value=[vec_content, vec_exp, vec_out])
            session = _session()
            scores = await f.score("content", session, expectation="exp", outcome="out")
            results.append(scores)

        for r in results[1:]:
            assert r.surprise == pytest.approx(results[0].surprise)
            assert r.task_relevance == pytest.approx(results[0].task_relevance)
            assert r.emotional_valence == pytest.approx(results[0].emotional_valence)
            assert r.overall == pytest.approx(results[0].overall)

    @pytest.mark.asyncio
    async def test_valence_amplification_constant_is_positive(self):
        """VALENCE_AMPLIFICATION must be > 0 for the formula to be meaningful."""
        assert VALENCE_AMPLIFICATION > 0.0
