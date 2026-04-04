"""
Thalamus Filter — Relevance Scoring Gate.

Evaluates each incoming episode on 4 dimensions before it enters the Retain Pipeline.
Episodes below the mode-dependent threshold are discarded; those above are enriched
with ThalamusScores and passed on.

Bio mapping:
- Novelty       → CA1 Mismatch Detection (low similarity = high novelty)
- Surprise      → Noradrenaline release (unexpected vs. current_expectation)
- Task-Relevance → PFC Top-Down Attention (similarity to task_context)
- Emotional Valence → Amygdala Modulation (LLM-assessed significance)

Concept reference: docs/engram/concept.md — Chapter 5 (Thalamus Filter)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .embeddings import Embeddings
    from .llm_wrapper import LLMConfig
    from .qdrant_client import QdrantEngineClient
    from .response_models import RetrievalMode, Session

from .engram_types import ThalamusScores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var names for threshold overrides
# ---------------------------------------------------------------------------
ENV_THALAMUS_THRESHOLD_PRECISION = "HINDSIGHT_API_THALAMUS_THRESHOLD_PRECISION"
ENV_THALAMUS_THRESHOLD_EXPLORATION = "HINDSIGHT_API_THALAMUS_THRESHOLD_EXPLORATION"
ENV_THALAMUS_THRESHOLD_VALIDATION = "HINDSIGHT_API_THALAMUS_THRESHOLD_VALIDATION"
ENV_THALAMUS_THRESHOLD_ANALOGY = "HINDSIGHT_API_THALAMUS_THRESHOLD_ANALOGY"

# ---------------------------------------------------------------------------
# Default thresholds per mode (T7)
# Exploration: low threshold → let more through (maximise novelty capture)
# Precision:   high threshold → only highly relevant episodes
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_PRECISION: Final[float] = 0.4
DEFAULT_THRESHOLD_EXPLORATION: Final[float] = 0.2
DEFAULT_THRESHOLD_VALIDATION: Final[float] = 0.3
DEFAULT_THRESHOLD_ANALOGY: Final[float] = 0.3

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
        "novelty": 0.1,
        "surprise": 0.2,
        "task_relevance": 0.5,
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

# Fallback emotional valence when LLM call fails
_VALENCE_FALLBACK: Final[float] = 0.3
# Neutral score used when optional session context is absent
_NEUTRAL_SCORE: Final[float] = 0.5


class ThalamusFilter:
    """
    Relevance Scoring Gate that evaluates episodes before Retain Pipeline ingestion.

    Computes ThalamusScores for an incoming text given the current Session context.
    Callers use `threshold_for_mode()` to decide whether to pass or drop the episode.

    Usage::

        filter = ThalamusFilter(qdrant=qdrant_client, embeddings=embed, llm=small_llm)
        scores = await filter.score(text, session)
        if scores.overall >= filter.threshold_for_mode(session.mode):
            # pass to Retain Pipeline
        else:
            logger.info("Thalamus: dropped (score=%.3f)", scores.overall)
    """

    def __init__(
        self,
        qdrant: QdrantEngineClient,
        embeddings: Embeddings,
        llm: LLMConfig,
    ) -> None:
        """
        Args:
            qdrant: Qdrant client used for Novelty similarity search.
            embeddings: Embedding provider for Novelty/Surprise/Task-Relevance scoring.
            llm: Small-tier LLM for Emotional Valence scoring.
        """
        self._qdrant = qdrant
        self._embeddings = embeddings
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def score(self, text: str, session: Session, bank_id: str | None = None) -> ThalamusScores:
        """Compute ThalamusScores for an incoming episode text.

        Args:
            text: The raw episode content to evaluate.
            session: Current Session providing mode, task_context, and current_expectation.
            bank_id: Optional bank identifier. When set, novelty is computed only against
                Engrams within this bank (Multi-Bank isolation, Epic 14).

        Returns:
            ThalamusScores with all 4 dimension scores and weighted overall.
        """
        input_embedding = self._embeddings.encode([text])[0]

        novelty = await self._score_novelty(input_embedding, bank_id=bank_id)
        surprise = await self._score_surprise(input_embedding, session)
        task_relevance = await self._score_task_relevance(input_embedding, session)
        emotional_valence = await self._score_emotional_valence(text)

        overall = self._compute_overall(novelty, surprise, task_relevance, emotional_valence, session.mode)

        return ThalamusScores(
            novelty=novelty,
            surprise=surprise,
            task_relevance=task_relevance,
            emotional_valence=emotional_valence,
            overall=overall,
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
    # Score dimensions (T2–T5)
    # ------------------------------------------------------------------

    async def _score_novelty(self, embedding: list[float], bank_id: str | None = None) -> float:
        """T2 — Novelty: 1.0 - max_similarity vs existing Engrams in Qdrant.

        High similarity to existing memories → low novelty.
        No existing memories → novelty = 1.0 (everything is new).

        Args:
            embedding: The input embedding to compare against.
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
            return 1.0

        if not results:
            return 1.0

        max_similarity = max(r["score"] for r in results)
        # Qdrant cosine scores are in [0, 1] for normalized vectors.
        # Clamp to [0, 1] to guard against floating-point edge cases.
        return float(max(0.0, min(1.0, 1.0 - max_similarity)))

    async def _score_surprise(self, embedding: list[float], session: Session) -> float:
        """T3 — Surprise: deviation from session.current_expectation.

        Low similarity to expectation → high surprise.
        No expectation set → neutral 0.5.
        """
        if not session.current_expectation:
            return _NEUTRAL_SCORE

        try:
            expectation_embedding = self._embeddings.encode([session.current_expectation])[0]
            similarity = _cosine_similarity(embedding, expectation_embedding)
            # High similarity = expected = low surprise; invert.
            return float(max(0.0, min(1.0, 1.0 - similarity)))
        except Exception:
            logger.warning("Thalamus surprise: embedding failed, defaulting to 0.5", exc_info=True)
            return _NEUTRAL_SCORE

    async def _score_task_relevance(self, embedding: list[float], session: Session) -> float:
        """T4 — Task-Relevance: similarity to session.task_context (PFC attention).

        High similarity to task context → high relevance.
        No task context set → neutral 0.5.
        """
        if not session.task_context:
            return _NEUTRAL_SCORE

        try:
            task_embedding = self._embeddings.encode([session.task_context])[0]
            similarity = _cosine_similarity(embedding, task_embedding)
            return float(max(0.0, min(1.0, similarity)))
        except Exception:
            logger.warning("Thalamus task_relevance: embedding failed, defaulting to 0.5", exc_info=True)
            return _NEUTRAL_SCORE

    async def _score_emotional_valence(self, text: str) -> float:
        """T5 — Emotional Valence: LLM Small-Tier assessment of emotional significance.

        Asks the LLM to rate emotional significance 0.0–1.0.
        Falls back to 0.3 on any error (neutral-low, erring on the side of inclusion).

        TODO(epic-14): For bulk imports, consider batching multiple texts into a single
        LLM call to reduce per-item overhead. Current cost: ~$0.0002/call (Haiku).
        """
        prompt = (
            "Rate the emotional significance of the following text on a scale from 0.0 to 1.0. "
            "0.0 means no emotional significance (routine fact), "
            "1.0 means extremely emotionally significant (crisis, major event, strong feeling). "
            "Respond with ONLY a single float number, nothing else.\n\n"
            f"Text: {text}"
        )
        try:
            response: str = await self._llm.call(
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=10,
                temperature=0.0,
                scope="thalamus",
            )
            value = float(str(response).strip())
            return max(0.0, min(1.0, value))
        except Exception:
            logger.warning("Thalamus emotional_valence: LLM call failed, defaulting to %.1f", _VALENCE_FALLBACK)
            return _VALENCE_FALLBACK

    # ------------------------------------------------------------------
    # Overall score (T6)
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
