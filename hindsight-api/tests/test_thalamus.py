"""
Unit tests for ThalamusFilter (Epic 04, Story 01).

Tests cover:
- Score computation with known inputs
- Mode-dependent weighting
- Threshold values per mode
- Fallback behaviour when session context is absent
- _cosine_similarity helper
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.response_models import RetrievalMode, Session
from hindsight_api.engine.thalamus import (
    DEFAULT_THRESHOLD_ANALOGY,
    DEFAULT_THRESHOLD_EXPLORATION,
    DEFAULT_THRESHOLD_PRECISION,
    DEFAULT_THRESHOLD_VALIDATION,
    MODE_WEIGHTS,
    ThalamusFilter,
    _cosine_similarity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_filter(
    qdrant_results: list[dict] | None = None,
    embed_return: list[float] | None = None,
    llm_return: str = "0.5",
) -> tuple[ThalamusFilter, MagicMock, MagicMock, MagicMock]:
    """Build a ThalamusFilter with fully mocked dependencies."""
    qdrant = AsyncMock()
    qdrant.search_similar = AsyncMock(return_value=qdrant_results or [])

    embeddings = MagicMock()
    vec = embed_return or [1.0, 0.0, 0.0]
    embeddings.encode = MagicMock(return_value=[vec])

    llm = AsyncMock()
    llm.call = AsyncMock(return_value=llm_return)

    f = ThalamusFilter(qdrant=qdrant, embeddings=embeddings, llm=llm)
    return f, qdrant, embeddings, llm


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
# Novelty score (T2)
# ---------------------------------------------------------------------------


class TestNoveltyScore:
    @pytest.mark.asyncio
    async def test_no_existing_memories_returns_one(self):
        f, qdrant, _, _ = _make_filter(qdrant_results=[])
        session = _session()
        scores = await f.score("hello world", session)
        assert scores.novelty == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_high_similarity_returns_low_novelty(self):
        # similarity = 0.95 → novelty = 0.05
        f, qdrant, _, _ = _make_filter(qdrant_results=[{"score": 0.95, "engram_id": "x", "payload": {}}])
        session = _session()
        scores = await f.score("hello world", session)
        assert scores.novelty == pytest.approx(0.05, abs=1e-6)

    @pytest.mark.asyncio
    async def test_qdrant_failure_defaults_to_one(self):
        f, qdrant, _, _ = _make_filter()
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
        f, _, _, _ = _make_filter(qdrant_results=results)
        session = _session()
        scores = await f.score("text", session)
        assert scores.novelty == pytest.approx(0.1, abs=1e-6)

    @pytest.mark.asyncio
    async def test_novelty_passes_bank_id_filter_to_qdrant(self):
        f, qdrant, _, _ = _make_filter(qdrant_results=[])
        session = _session()
        await f.score("hello", session, bank_id="test-bank")
        call_args = qdrant.search_similar.call_args
        assert call_args is not None
        filters = call_args.kwargs.get("filters") or (call_args.args[2] if len(call_args.args) > 2 else None)
        assert filters is not None
        assert "must" in filters

    @pytest.mark.asyncio
    async def test_novelty_no_bank_id_searches_without_filter(self):
        f, qdrant, _, _ = _make_filter(qdrant_results=[])
        session = _session()
        await f.score("hello", session, bank_id=None)
        call_args = qdrant.search_similar.call_args
        filters = call_args.kwargs.get("filters")
        assert filters is None


# ---------------------------------------------------------------------------
# Surprise score (T3)
# ---------------------------------------------------------------------------


class TestSurpriseScore:
    @pytest.mark.asyncio
    async def test_no_expectation_returns_neutral(self):
        f, _, _, _ = _make_filter()
        session = _session(expectation=None)
        scores = await f.score("anything", session)
        assert scores.surprise == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_identical_content_returns_low_surprise(self):
        # When text == expectation, similarity ≈ 1.0 → surprise ≈ 0.0
        vec = [1.0, 0.0, 0.0]
        f, _, embeddings, _ = _make_filter(embed_return=vec)
        # Both text and expectation produce the same vector
        embeddings.encode = MagicMock(return_value=[vec])
        session = _session(expectation="same text")
        scores = await f.score("same text", session)
        assert scores.surprise == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_orthogonal_expectation_returns_high_surprise(self):
        # input vector: [1, 0], expectation vector: [0, 1] → sim=0 → surprise=1
        call_count = [0]

        def encode_side_effect(texts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [[1.0, 0.0]]  # input
            return [[0.0, 1.0]]  # expectation

        f, _, embeddings, _ = _make_filter()
        embeddings.encode = MagicMock(side_effect=encode_side_effect)
        session = _session(expectation="orthogonal")
        scores = await f.score("input", session)
        assert scores.surprise == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Task-Relevance score (T4)
# ---------------------------------------------------------------------------


class TestTaskRelevanceScore:
    @pytest.mark.asyncio
    async def test_no_task_context_returns_neutral(self):
        f, _, _, _ = _make_filter()
        session = _session(task_context=None)
        scores = await f.score("text", session)
        assert scores.task_relevance == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_identical_task_context_returns_high_relevance(self):
        vec = [1.0, 0.0, 0.0]
        f, _, embeddings, _ = _make_filter(embed_return=vec)
        embeddings.encode = MagicMock(return_value=[vec])
        session = _session(task_context="same task")
        scores = await f.score("same task", session)
        assert scores.task_relevance == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_orthogonal_task_context_returns_low_relevance(self):
        call_count = [0]

        def encode_side_effect(texts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [[1.0, 0.0]]
            return [[0.0, 1.0]]

        f, _, embeddings, _ = _make_filter()
        embeddings.encode = MagicMock(side_effect=encode_side_effect)
        session = _session(task_context="unrelated task")
        scores = await f.score("input", session)
        assert scores.task_relevance == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Emotional Valence score (T5)
# ---------------------------------------------------------------------------


class TestEmotionalValenceScore:
    @pytest.mark.asyncio
    async def test_llm_returns_float_string(self):
        f, _, _, llm = _make_filter(llm_return="0.8")
        session = _session()
        scores = await f.score("text", session)
        assert scores.emotional_valence == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        f, _, _, llm = _make_filter()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM unreachable"))
        session = _session()
        scores = await f.score("text", session)
        assert scores.emotional_valence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_llm_out_of_range_clamped(self):
        # LLM returns 1.5 → should be clamped to 1.0
        f, _, _, llm = _make_filter(llm_return="1.5")
        session = _session()
        scores = await f.score("text", session)
        assert scores.emotional_valence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Mode-dependent weighting (T6)
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
        f, _, _, _ = _make_filter()

        # Patch each private method to return predictable values
        f._score_novelty = AsyncMock(return_value=1.0)
        f._score_surprise = AsyncMock(return_value=0.0)
        f._score_task_relevance = AsyncMock(return_value=0.0)
        f._score_emotional_valence = AsyncMock(return_value=0.0)

        session = _session(mode=RetrievalMode.EXPLORATION)
        scores = await f.score("text", session)

        expected_overall = MODE_WEIGHTS[RetrievalMode.EXPLORATION]["novelty"] * 1.0
        assert scores.overall == pytest.approx(expected_overall)


# ---------------------------------------------------------------------------
# Threshold configuration (T7)
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_default_thresholds(self):
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION) == pytest.approx(
            DEFAULT_THRESHOLD_PRECISION
        )
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.EXPLORATION) == pytest.approx(
            DEFAULT_THRESHOLD_EXPLORATION
        )
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.VALIDATION) == pytest.approx(
            DEFAULT_THRESHOLD_VALIDATION
        )
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.ANALOGY) == pytest.approx(
            DEFAULT_THRESHOLD_ANALOGY
        )

    def test_exploration_threshold_lower_than_precision(self):
        assert DEFAULT_THRESHOLD_EXPLORATION < DEFAULT_THRESHOLD_PRECISION

    def test_env_var_override(self, monkeypatch):
        # Patch the module-level dict directly to simulate an env-var override.
        # Using importlib.reload() would mutate the shared module object and
        # leak state into other tests running in the same worker process.
        import hindsight_api.engine.thalamus as thalamus_mod

        monkeypatch.setitem(thalamus_mod.MODE_THRESHOLDS, RetrievalMode.PRECISION, 0.99)
        assert ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION) == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# Fallback: no session context
# ---------------------------------------------------------------------------


class TestFallbackBehaviour:
    @pytest.mark.asyncio
    async def test_session_without_context_produces_valid_scores(self):
        f, _, _, _ = _make_filter()
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
        f, _, _, _ = _make_filter()
        session = _session(expectation=None, task_context=None)
        scores = await f.score("episode", session)
        assert scores.surprise == pytest.approx(0.5)
        assert scores.task_relevance == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Gate behaviour (T5) — threshold-based pass/drop logic
# ---------------------------------------------------------------------------


class TestGateBehaviour:
    @pytest.mark.asyncio
    async def test_high_overall_score_passes_threshold(self):
        """Episode with overall score above threshold should not be dropped."""
        f, _, _, _ = _make_filter(llm_return="0.9")
        # Make novelty = 1.0 (no existing memories) and valence = 0.9
        f._score_novelty = AsyncMock(return_value=1.0)
        f._score_surprise = AsyncMock(return_value=1.0)
        f._score_task_relevance = AsyncMock(return_value=1.0)
        f._score_emotional_valence = AsyncMock(return_value=0.9)

        session = _session(mode=RetrievalMode.PRECISION)
        scores = await f.score("highly novel content", session)
        threshold = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)

        assert scores.overall >= threshold, (
            f"Expected score {scores.overall:.3f} >= threshold {threshold:.3f}"
        )

    @pytest.mark.asyncio
    async def test_low_overall_score_fails_threshold(self):
        """Episode with very low scores should fall below threshold."""
        f, _, _, _ = _make_filter(llm_return="0.0")
        # Force all scores to 0 — no novelty, no surprise, no relevance, no valence
        f._score_novelty = AsyncMock(return_value=0.0)
        f._score_surprise = AsyncMock(return_value=0.0)
        f._score_task_relevance = AsyncMock(return_value=0.0)
        f._score_emotional_valence = AsyncMock(return_value=0.0)

        session = _session(mode=RetrievalMode.PRECISION)
        scores = await f.score("completely irrelevant duplicate", session)
        threshold = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)

        assert scores.overall < threshold, (
            f"Expected score {scores.overall:.3f} < threshold {threshold:.3f}"
        )

    def test_exploration_threshold_lower_than_precision_threshold(self):
        """Exploration mode has lower threshold → lets more through."""
        t_exploration = ThalamusFilter.threshold_for_mode(RetrievalMode.EXPLORATION)
        t_precision = ThalamusFilter.threshold_for_mode(RetrievalMode.PRECISION)
        assert t_exploration < t_precision

    @pytest.mark.asyncio
    async def test_scores_returned_for_passed_content(self):
        """Scores from ThalamusFilter should be fully populated for passed content."""
        f, _, _, _ = _make_filter(llm_return="0.7")
        f._score_novelty = AsyncMock(return_value=0.8)
        f._score_surprise = AsyncMock(return_value=0.5)
        f._score_task_relevance = AsyncMock(return_value=0.6)
        f._score_emotional_valence = AsyncMock(return_value=0.7)

        session = _session(mode=RetrievalMode.EXPLORATION)
        scores = await f.score("interesting content", session)

        assert isinstance(scores, ThalamusScores)
        assert scores.novelty == pytest.approx(0.8)
        assert scores.surprise == pytest.approx(0.5)
        assert scores.task_relevance == pytest.approx(0.6)
        assert scores.emotional_valence == pytest.approx(0.7)
        assert 0.0 <= scores.overall <= 1.0

    @pytest.mark.asyncio
    async def test_mode_switch_changes_overall_score(self):
        """Same raw scores produce different overall score under different modes."""
        f_exp, _, _, _ = _make_filter(llm_return="0.5")
        f_prec, _, _, _ = _make_filter(llm_return="0.5")

        # novelty=1.0, all others=0.0 → exploration weights novelty heavily
        for f in (f_exp, f_prec):
            f._score_novelty = AsyncMock(return_value=1.0)
            f._score_surprise = AsyncMock(return_value=0.0)
            f._score_task_relevance = AsyncMock(return_value=0.0)
            f._score_emotional_valence = AsyncMock(return_value=0.0)

        scores_exploration = await f_exp.score("text", _session(mode=RetrievalMode.EXPLORATION))
        scores_precision = await f_prec.score("text", _session(mode=RetrievalMode.PRECISION))

        # Exploration weights novelty at 0.4, Precision at 0.1
        assert scores_exploration.overall > scores_precision.overall
