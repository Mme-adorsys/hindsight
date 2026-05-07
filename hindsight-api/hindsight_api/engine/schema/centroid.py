"""Centroid arithmetic for schema embeddings (Epic 25 Story 03).

A schema's centroid is the mean of its evidence engrams' embeddings,
L2-normalised so cosine similarity in Qdrant compares apples to apples
with single-engram queries (whose embeddings are already unit vectors —
sentence-transformers / TEI normalise by default).

Concept §4.2 (Schema-Felder) — `centroid_qdrant_id` points to a Qdrant
point whose vector is exactly this `compute_centroid` output.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def compute_centroid(embeddings: Iterable[list[float]]) -> list[float]:
    """Return the L2-normalised mean of `embeddings`.

    Raises:
        ValueError: when the input is empty or vectors disagree in dimension.
    """
    matrix = np.asarray(list(embeddings), dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("compute_centroid expects a non-empty sequence of equal-length vectors")

    mean = matrix.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        # Degenerate case (e.g. perfectly antiparallel vectors that cancel).
        # A zero vector breaks cosine similarity; surface it explicitly so
        # the caller can decide whether to skip schema creation.
        raise ValueError("compute_centroid produced a zero vector — input embeddings cancel out")
    return (mean / norm).tolist()
