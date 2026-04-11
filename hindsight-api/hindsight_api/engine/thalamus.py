"""
Thalamus Filter — Relevance Scoring Gate.

Evaluates each incoming episode on 4 dimensions before it enters the Retain Pipeline.
Episodes below the mode-dependent threshold are discarded; those above are enriched
with ThalamusScores and passed on.

Bio mapping:
- Novelty        → CA1 Mismatch Detection (low similarity = high novelty)
- Surprise       → Noradrenaline/Prediction Error (outcome deviates from expectation)
- Task-Relevance → PFC Top-Down Attention (similarity to task context)
- Emotional Valence → Amygdala/Dopamine: Prediction-Error-Magnitude × Amplification

Concept reference: docs/engram/concept.md — Chapter 5 (Thalamus Filter)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from .embeddings import Embeddings
    from .qdrant_client import QdrantEngineClient
    from .response_models import RetrievalMode, Session

from .engram_types import ThalamusRationale, ThalamusScores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var names for threshold overrides
# ---------------------------------------------------------------------------
ENV_THALAMUS_THRESHOLD_PRECISION = "HINDSIGHT_API_THALAMUS_THRESHOLD_PRECISION"
ENV_THALAMUS_THRESHOLD_EXPLORATION = "HINDSIGHT_API_THALAMUS_THRESHOLD_EXPLORATION"
ENV_THALAMUS_THRESHOLD_VALIDATION = "HINDSIGHT_API_THALAMUS_THRESHOLD_VALIDATION"
ENV_THALAMUS_THRESHOLD_ANALOGY = "HINDSIGHT_API_THALAMUS_THRESHOLD_ANALOGY"

# Valence amplification factor: scales prediction-error-magnitude to emotional significance.
# Models the amygdala's tendency to magnify salient deviations from expectation.
ENV_VALENCE_AMPLIFICATION = "HINDSIGHT_API_VALENCE_AMPLIFICATION"
DEFAULT_VALENCE_AMPLIFICATION: Final[float] = 1.5

# Read at module load time; override via env var for tuning without code changes.
VALENCE_AMPLIFICATION: Final[float] = float(os.getenv(ENV_VALENCE_AMPLIFICATION, str(DEFAULT_VALENCE_AMPLIFICATION)))

# ---------------------------------------------------------------------------
# Default thresholds per mode (T7)
# Exploration: low threshold → let more through (maximise novelty capture)
# Precision:   high threshold → only highly relevant episodes
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_PRECISION: Final[float] = 0.25
DEFAULT_THRESHOLD_EXPLORATION: Final[float] = 0.15
DEFAULT_THRESHOLD_VALIDATION: Final[float] = 0.20
DEFAULT_THRESHOLD_ANALOGY: Final[float] = 0.20

# ---------------------------------------------------------------------------
# Mode-dependent score weights (T6)
# Each row sums to 1.0.  Mode steers emphasis:
#   Exploration → Novelty-Boost  (discover unknown)
#   Precision   → Relevance-Boost (stay on task)
#   Validation  → Surprise-Boost  (detect contradictions)
#   Analogy     → balanced Novelty + Relevance
# ---------------------------------------------------------------------------
from .response_models import RetrievalMode as _RetrievalMode  # noqa: E402 (after TYPE_CHECKING block)

MODE_WEIGHTS: Final[dict[str, dict[str, float]]] = {
    _RetrievalMode.EXPLORATION: {
        "novelty": 0.4,
        "surprise": 0.2,
        "task_relevance": 0.2,
        "emotional_valence": 0.2,
    },
    _RetrievalMode.PRECISION: {
        "novelty": 0.15,
        "surprise": 0.2,
        "task_relevance": 0.45,
        "emotional_valence": 0.2,
    },
    _RetrievalMode.VALIDATION: {
        "novelty": 0.2,
        "surprise": 0.4,
        "task_relevance": 0.2,
        "emotional_valence": 0.2,
    },
    _RetrievalMode.ANALOGY: {
        "novelty": 0.3,
        "surprise": 0.2,
        "task_relevance": 0.3,
        "emotional_valence": 0.2,
    },
}

MODE_THRESHOLDS: Final[dict[str, float]] = {
    _RetrievalMode.PRECISION: float(os.getenv(ENV_THALAMUS_THRESHOLD_PRECISION, str(DEFAULT_THRESHOLD_PRECISION))),
    _RetrievalMode.EXPLORATION: float(
        os.getenv(ENV_THALAMUS_THRESHOLD_EXPLORATION, str(DEFAULT_THRESHOLD_EXPLORATION))
    ),
    _RetrievalMode.VALIDATION: float(os.getenv(ENV_THALAMUS_THRESHOLD_VALIDATION, str(DEFAULT_THRESHOLD_VALIDATION))),
    _RetrievalMode.ANALOGY: float(os.getenv(ENV_THALAMUS_THRESHOLD_ANALOGY, str(DEFAULT_THRESHOLD_ANALOGY))),
}

# Fallback emotional valence when expectation or outcome embedding is absent
_VALENCE_FALLBACK: Final[float] = 0.3
# Neutral score used when optional context is absent
_NEUTRAL_SCORE: Final[float] = 0.5


class ThalamusFilter:
    """
    Relevance Scoring Gate that evaluates episodes before Retain Pipeline ingestion.

    Computes ThalamusScores for an incoming text given the current Session context.
    Callers use `threshold_for_mode()` to decide whether to pass or drop the episode.

    All scores are embedding-based and deterministic: given the same inputs the same
    scores are always produced — no LLM variance, no per-call costs beyond embeddings.

    Usage::

        filter = ThalamusFilter(qdrant=qdrant_client, embeddings=embed)
        scores = await filter.score(content, session)
        if scores.overall >= filter.threshold_for_mode(session.mode):
            # pass to Retain Pipeline
        else:
            logger.info("Thalamus: dropped (score=%.3f)", scores.overall)
    """

    def __init__(
        self,
        qdrant: QdrantEngineClient,
        embeddings: Embeddings,
    ) -> None:
        """
        Args:
            qdrant: Qdrant client used for Novelty similarity search.
            embeddings: Embedding provider for all 4 scoring dimensions.
        """
        self._qdrant = qdrant
        self._embeddings = embeddings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def score(
        self,
        content: str,
        session: Session,
        bank_id: str | None = None,
        context: str | None = None,
        expectation: str | None = None,
        outcome: str | None = None,
    ) -> ThalamusScores:
        """Compute ThalamusScores for an incoming episode.

        Args:
            content: The raw episode content to evaluate.
            session: Current Session providing mode and fallback context/expectation.
            bank_id: Optional bank identifier. When set, novelty is computed only against
                Engrams within this bank (Multi-Bank isolation, Epic 14).
            context: Item-level task context (overrides session.task_context if set).
            expectation: Item-level expectation (overrides session.current_expectation if set).
            outcome: Item-level outcome. No session fallback — outcomes are always item-specific.

        Returns:
            ThalamusScores with all 4 dimension scores, weighted overall, and a
            ThalamusRationale capturing the inputs that drove each dimension so
            the CP can render a human-readable "why this score" explanation.
        """
        # Fallback hierarchy: item-level > session-level > None. Also track the
        # source so the rationale can distinguish explicit item-level context
        # from fallback session context ("where did task_relevance come from?").
        if context:
            effective_context = context
            context_source: Literal["none", "item", "session"] = "item"
        elif session.task_context:
            effective_context = session.task_context
            context_source = "session"
        else:
            effective_context = None
            context_source = "none"

        effective_expectation = expectation or session.current_expectation
        effective_outcome = outcome  # no session fallback

        # Batch-embed all non-None strings in a single encode call for efficiency
        texts_to_embed: list[str] = [content]
        ctx_idx: int | None = None
        exp_idx: int | None = None
        out_idx: int | None = None

        if effective_context:
            ctx_idx = len(texts_to_embed)
            texts_to_embed.append(effective_context)
        if effective_expectation:
            exp_idx = len(texts_to_embed)
            texts_to_embed.append(effective_expectation)
        if effective_outcome:
            out_idx = len(texts_to_embed)
            texts_to_embed.append(effective_outcome)

        all_embeddings = self._embeddings.encode(texts_to_embed)
        content_embedding: list[float] = all_embeddings[0]
        context_embedding: list[float] | None = all_embeddings[ctx_idx] if ctx_idx is not None else None
        expectation_embedding: list[float] | None = all_embeddings[exp_idx] if exp_idx is not None else None
        outcome_embedding: list[float] | None = all_embeddings[out_idx] if out_idx is not None else None

        novelty, novelty_max_id, novelty_max_sim = await self._score_novelty_with_source(
            content_embedding, bank_id=bank_id
        )
        surprise = self._score_surprise(expectation_embedding, outcome_embedding)
        task_relevance = self._score_task_relevance(content_embedding, context_embedding)
        emotional_valence = self._score_emotional_valence(expectation_embedding, outcome_embedding)

        # Raw prediction-error magnitude — the pre-amplification signal behind
        # both surprise and emotional_valence. None when inputs are missing.
        prediction_error = 0.0
        if expectation_embedding is not None and outcome_embedding is not None:
            sim = _cosine_similarity(expectation_embedding, outcome_embedding)
            prediction_error = max(0.0, min(1.0, 1.0 - sim))

        overall = self._compute_overall(novelty, surprise, task_relevance, emotional_valence, session.mode)

        rationale = ThalamusRationale(
            novelty_max_similar_id=novelty_max_id,
            novelty_max_similarity=novelty_max_sim,
            surprise_expectation_provided=expectation_embedding is not None,
            surprise_outcome_provided=outcome_embedding is not None,
            task_relevance_context_source=context_source,
            valence_prediction_error=prediction_error,
        )

        return ThalamusScores(
            novelty=novelty,
            surprise=surprise,
            task_relevance=task_relevance,
            emotional_valence=emotional_valence,
            overall=overall,
            rationale=rationale,
        )

    @staticmethod
    def threshold_for_mode(mode: RetrievalMode) -> float:
        """Return the configured threshold for a given session mode.

        Args:
            mode: The current RetrievalMode.

        Returns:
            Float threshold in range 0.0–1.0.  Episodes with overall score
            below this value should be dropped.
        """
        return MODE_THRESHOLDS.get(mode, DEFAULT_THRESHOLD_PRECISION)

    # ------------------------------------------------------------------
    # Score dimensions
    # ------------------------------------------------------------------

    async def _score_novelty(self, embedding: list[float], bank_id: str | None = None) -> float:
        """Novelty only (backwards-compatible wrapper).

        Kept for existing callers that just want the float. The main scoring
        path now uses `_score_novelty_with_source` which also returns the
        closest-engram id for the rationale.
        """
        novelty, _, _ = await self._score_novelty_with_source(embedding, bank_id=bank_id)
        return novelty

    async def _score_novelty_with_source(
        self, embedding: list[float], bank_id: str | None = None
    ) -> tuple[float, str | None, float]:
        """Novelty: 1.0 - max_similarity vs existing Engrams in Qdrant.

        High similarity to existing memories → low novelty.
        No existing memories → novelty = 1.0 (everything is new).

        Returns a tuple `(novelty, max_similar_engram_id, max_similarity)`:
        - novelty: the final score in [0, 1]
        - max_similar_engram_id: id of the closest existing engram (None when
          the bank is empty or the search failed)
        - max_similarity: the raw similarity value that produced the novelty
          score (0.0 when no results). Same number that 1 - novelty would give
          for successful searches, but preserved here so the rationale can
          show the unclamped pre-subtraction value.

        Args:
            embedding: The content embedding to compare against.
            bank_id: When set, restricts the similarity search to this bank only.
                Ensures novelty is computed within bank boundaries (Multi-Bank isolation).
        """
        try:
            filters = None
            if bank_id:
                filters = {"must": [{"key": "bank_id", "match": {"value": bank_id}}]}
            results = await self._qdrant.search_similar(embedding, limit=5, filters=filters)
        except Exception:
            logger.warning("Thalamus novelty: Qdrant search failed, defaulting to 1.0", exc_info=True)
            return 1.0, None, 0.0

        if not results:
            return 1.0, None, 0.0

        top = max(results, key=lambda r: r["score"])
        max_similarity = float(top["score"])
        max_id = top.get("id") or top.get("engram_id")
        max_id_str = str(max_id) if max_id is not None else None

        # Qdrant cosine scores are in [0, 1] for normalized vectors.
        # Clamp to [0, 1] to guard against floating-point edge cases.
        novelty = float(max(0.0, min(1.0, 1.0 - max_similarity)))
        return novelty, max_id_str, max_similarity

    def _score_surprise(
        self,
        expectation_embedding: list[float] | None,
        outcome_embedding: list[float] | None,
    ) -> float:
        """Surprise: deviation of outcome from expectation (Prediction Error).

        prediction_error = 1.0 - cosine(expectation_embedding, outcome_embedding)
        Fallback to 0.5 (neutral) when either embedding is absent.

        Bio mapping: Noradrenaline release on unexpected outcomes — the larger
        the delta between what was expected and what occurred, the higher the
        plasticity-boosting surprise signal.
        """
        if expectation_embedding is None or outcome_embedding is None:
            return _NEUTRAL_SCORE
        similarity = _cosine_similarity(expectation_embedding, outcome_embedding)
        return float(max(0.0, min(1.0, 1.0 - similarity)))

    def _score_task_relevance(
        self,
        content_embedding: list[float],
        context_embedding: list[float] | None,
    ) -> float:
        """Task-Relevance: similarity of content to task context (PFC top-down attention).

        Fallback to 0.5 (neutral) when context is absent.
        """
        if context_embedding is None:
            return _NEUTRAL_SCORE
        similarity = _cosine_similarity(content_embedding, context_embedding)
        return float(max(0.0, min(1.0, similarity)))

    def _score_emotional_valence(
        self,
        expectation_embedding: list[float] | None,
        outcome_embedding: list[float] | None,
    ) -> float:
        """Emotional Valence: Prediction-Error-Magnitude × Amplification.

        emotional_valence = min(1.0, prediction_error_magnitude * VALENCE_AMPLIFICATION)
        where prediction_error_magnitude = 1.0 - cosine(expectation, outcome).

        Fallback to 0.3 (low, erring on the side of inclusion) when either embedding is absent.

        Bio mapping: Amplification models the amygdala's role in magnifying prediction errors
        into emotional significance. A large delta between expectation and outcome signals
        high significance regardless of direction (positive or negative surprise).
        """
        if expectation_embedding is None or outcome_embedding is None:
            return _VALENCE_FALLBACK
        prediction_error_magnitude = max(
            0.0, min(1.0, 1.0 - _cosine_similarity(expectation_embedding, outcome_embedding))
        )
        return float(min(1.0, prediction_error_magnitude * VALENCE_AMPLIFICATION))

    # ------------------------------------------------------------------
    # Overall score
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_overall(
        novelty: float,
        surprise: float,
        task_relevance: float,
        emotional_valence: float,
        mode: RetrievalMode,
    ) -> float:
        """Compute mode-weighted overall score from the 4 dimension scores."""
        weights = MODE_WEIGHTS.get(mode, MODE_WEIGHTS[_RetrievalMode.PRECISION])
        overall = (
            weights["novelty"] * novelty
            + weights["surprise"] * surprise
            + weights["task_relevance"] * task_relevance
            + weights["emotional_valence"] * emotional_valence
        )
        return float(max(0.0, min(1.0, overall)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors.

    Returns value in [0, 1] for unit-normalized vectors (as produced by
    SentenceTransformers / TEI embeddings).  Falls back to 0.0 on zero vectors.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
