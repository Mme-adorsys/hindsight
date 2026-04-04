"""
Embedding processing for retain pipeline.

Handles augmenting fact texts with temporal information and generating embeddings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..response_models import Session

from ..engram_types import ThalamusScores
from . import embedding_utils
from .types import ExtractedFact

logger = logging.getLogger(__name__)

# Threshold above which a Thalamus dimension is considered "dominant"
_DOMINANT_THRESHOLD: float = 0.6

_THALAMUS_LABELS: dict[str, str] = {
    "novelty": "high novelty",
    "surprise": "surprising",
    "task_relevance": "task relevant",
    "emotional_valence": "emotionally significant",
}


def get_dominant_thalamus_label(scores: ThalamusScores) -> str | None:
    """Return a label for the highest-scoring Thalamus dimension.

    Only returns a label when the winning dimension's score exceeds
    _DOMINANT_THRESHOLD (0.6).  Below that, the scores are too balanced
    to pick a single semantic hint without adding noise to the embedding.

    Args:
        scores: ThalamusScores with 4 dimension values in [0, 1].

    Returns:
        Human-readable label (e.g. "high novelty") or None.
    """
    best_key = max(_THALAMUS_LABELS, key=lambda k: getattr(scores, k))
    if getattr(scores, best_key) > _DOMINANT_THRESHOLD:
        return _THALAMUS_LABELS[best_key]
    return None


def augment_texts_with_context(
    facts: list[ExtractedFact],
    format_date_fn,
    session: Session | None = None,
) -> list[str]:
    """Augment fact texts with date, session context, and dominant Thalamus hint.

    Extends :func:`augment_texts_with_dates` with two optional enrichments:
    - **Session context:** ``session.task_context`` anchors the embedding in
      the current task domain, making retrieval more mode-aware.
    - **Thalamus hint:** The dominant Thalamus dimension (if score > 0.6) adds
      a semantic signal that helps distinguish high-novelty from task-relevant
      facts even when their surface text is similar.

    When called without ``session`` (or with all-None fields), the output is
    identical to :func:`augment_texts_with_dates` — no behaviour change for
    existing callers.

    Args:
        facts: List of ExtractedFact objects.
        format_date_fn: Function to format a datetime to a readable string.
        session: Optional Session providing task_context and mode.

    Returns:
        List of augmented text strings (same length as facts).
    """
    result = []
    for fact in facts:
        fact_date = fact.occurred_start or fact.mentioned_at
        parts = [f"{fact.fact_text} (happened in {format_date_fn(fact_date)})"]

        if session and session.task_context:
            parts.append(f"[context: {session.task_context}]")

        if fact.thalamus_scores:
            label = get_dominant_thalamus_label(fact.thalamus_scores)
            if label:
                parts.append(f"[{label}]")

        result.append(" ".join(parts))
    return result


def augment_texts_with_dates(facts: list[ExtractedFact], format_date_fn) -> list[str]:
    """
    Augment fact texts with readable dates for better temporal matching.

    This allows queries like "camping in June" to match facts that happened in June.

    Args:
        facts: List of ExtractedFact objects
        format_date_fn: Function to format datetime to readable string

    Returns:
        List of augmented text strings (same length as facts)
    """
    augmented_texts = []
    for fact in facts:
        # Use occurred_start as the representative date
        fact_date = fact.occurred_start or fact.mentioned_at
        readable_date = format_date_fn(fact_date)
        # Augment text with date for embedding (but store original text in DB)
        augmented_text = f"{fact.fact_text} (happened in {readable_date})"
        augmented_texts.append(augmented_text)
    return augmented_texts


async def generate_embeddings_batch(embeddings_model, texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a batch of texts.

    Args:
        embeddings_model: Embeddings model instance
        texts: List of text strings to embed

    Returns:
        List of embedding vectors (same length as texts)
    """
    if not texts:
        return []

    embeddings = await embedding_utils.generate_embeddings_batch(embeddings_model, texts)

    return embeddings
