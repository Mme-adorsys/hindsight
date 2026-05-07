"""C2 Pattern Recognition — HDBSCAN cluster detection (Epic 25 Story 04, R1).

Game-of-Life rule **R1 (Birth)** picks up groups of ≥ 3 buffer engrams whose
embeddings cluster together (pairwise cosine ≥ 0.75). Surviving clusters are
fed to R2 (Maturation, Story 05) as ``ClusterCandidate``s; only candidates
that survive ≥ 2 C2-Zyklen become schemas.

Bio mapping: SWS sharp-wave ripples replay co-active engrams; the
hippocampus surfaces statistically regular patterns to the neocortex
(McClelland/McNaughton/O'Reilly 1995). HDBSCAN on cosine embeddings is the
algorithmic stand-in for that statistical surfacing.

This module deliberately keeps the data-fetch path through PostgreSQL's
hippocampal pointer index (`engram_dictionary.filter_entries`, concept §3) —
PG decides which engrams are eligible (`layer='buffer'`, `status='active'`,
bank-scoped), Qdrant only delivers vectors for those ids.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..engram_dictionary import filter_entries

if TYPE_CHECKING:
    import asyncpg

    from ..qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

MIN_CLUSTER_SIZE: int = 3
COHESION_THRESHOLD: float = 0.75
# Fetch limit when scrolling buffer engrams. Banks beyond this size get a
# warning; the caller can split work into multiple C2 runs by sub-bank.
DEFAULT_BANK_SCAN_LIMIT: int = 10_000


@dataclass(frozen=True)
class ClusterCandidate:
    """Cluster of buffer engrams emitted by R1 detection.

    ``cohesion`` is the mean pairwise cosine similarity over all member
    embeddings — a single number per candidate. Story 05 uses it as a
    fingerprint feature alongside the centroid.
    """

    engram_ids: tuple[str, ...]
    member_embeddings: tuple[tuple[float, ...], ...]
    cohesion: float

    @property
    def size(self) -> int:
        return len(self.engram_ids)


@dataclass
class DetectionStats:
    """Per-run counters surfaced via the logger (Story 04 T4)."""

    bank_id: str
    buffer_engrams: int = 0
    raw_clusters: int = 0
    cohesion_filtered: int = 0
    candidates: int = 0
    skipped_reason: str | None = None
    cluster_details: list[dict] = field(default_factory=list)


def _l2_normalise(rows: np.ndarray) -> np.ndarray:
    """Row-wise L2-normalisation. Returns rows[i] unchanged when ‖rows[i]‖ == 0."""
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    safe = np.where(norms == 0, 1.0, norms)
    return rows / safe


def _mean_pairwise_cosine(embeddings: np.ndarray) -> float:
    """Mean cosine over all unordered pairs — the cluster's cohesion score.

    Assumes rows are already L2-normalised so the dot product equals the
    cosine. Diagonal entries (self-similarity = 1.0) are excluded.
    """
    if embeddings.shape[0] < 2:
        return 1.0
    sims = embeddings @ embeddings.T
    n = sims.shape[0]
    # Sum off-diagonal entries; there are n*(n-1)/2 unordered pairs.
    total = float((sims.sum() - np.trace(sims)) / 2.0)
    return total / (n * (n - 1) / 2.0)


def _run_hdbscan(embeddings: np.ndarray) -> np.ndarray:
    """Wrap the HDBSCAN call so the import is hot-loaded (heavy dep).

    Cosine isn't directly supported by HDBSCAN — we feed L2-normalised
    vectors and use Euclidean, which is monotonic in cosine for unit
    vectors (||u-v||^2 = 2 - 2·cos(u,v)).
    """
    import hdbscan

    # ``min_samples=2`` keeps the algorithm permissive enough to surface the
    # small (size=MIN_CLUSTER_SIZE) clusters concept §13 R1 cares about; the
    # default (``min_samples=min_cluster_size``) requires three mutually
    # reachable neighbours per point and rejects 3-point bundles.
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=MIN_CLUSTER_SIZE,
        min_samples=2,
        metric="euclidean",
        cluster_selection_method="eom",
        allow_single_cluster=False,
    )
    clusterer.fit(embeddings)
    return clusterer.labels_


async def detect_clusters(
    bank_id: str,
    pool: "asyncpg.Pool",
    qdrant: "QdrantEngineClient",
    *,
    limit: int = DEFAULT_BANK_SCAN_LIMIT,
) -> list[ClusterCandidate]:
    """Run R1 cluster detection on a bank's buffer engrams.

    Pipeline:
        1. PostgreSQL — list active buffer engram ids for ``bank_id``
           (hippocampal pointer index).
        2. Qdrant — batch-retrieve their embeddings.
        3. HDBSCAN on L2-normalised vectors with ``min_cluster_size=3``.
        4. R1 filter — keep clusters whose mean pairwise cosine ≥ 0.75.

    Returns an empty list (with a stats log line) when there are too few
    engrams or HDBSCAN raises — schema emergence is best-effort.
    """
    stats = DetectionStats(bank_id=bank_id)
    entries = await filter_entries(
        pool,
        bank_id=bank_id,
        layer="buffer",
        status="active",
        limit=limit,
    )
    stats.buffer_engrams = len(entries)

    if len(entries) < MIN_CLUSTER_SIZE:
        stats.skipped_reason = "too_few_buffer_engrams"
        _log_stats(stats)
        return []

    engram_ids = [str(e["engram_id"]) for e in entries]
    points = await qdrant.retrieve_many(engram_ids)
    by_id = {p["engram_id"]: p["vector"] for p in points if p.get("vector") is not None}

    aligned_ids = [eid for eid in engram_ids if eid in by_id]
    if len(aligned_ids) < MIN_CLUSTER_SIZE:
        stats.skipped_reason = "too_few_embeddings_in_qdrant"
        _log_stats(stats)
        return []

    matrix = _l2_normalise(np.asarray([by_id[eid] for eid in aligned_ids], dtype=np.float64))

    try:
        labels = _run_hdbscan(matrix)
    except Exception as exc:
        logger.warning("HDBSCAN failed on bank=%s: %s", bank_id, exc)
        stats.skipped_reason = f"hdbscan_error:{type(exc).__name__}"
        _log_stats(stats)
        return []

    candidates: list[ClusterCandidate] = []
    for label in sorted({int(lbl) for lbl in labels if lbl != -1}):
        member_indices = [i for i, lbl in enumerate(labels) if int(lbl) == label]
        if len(member_indices) < MIN_CLUSTER_SIZE:
            continue
        stats.raw_clusters += 1
        member_vecs = matrix[member_indices]
        cohesion = _mean_pairwise_cosine(member_vecs)
        if cohesion < COHESION_THRESHOLD:
            stats.cohesion_filtered += 1
            stats.cluster_details.append(
                {"label": label, "size": len(member_indices), "cohesion": round(cohesion, 4), "kept": False}
            )
            continue
        member_ids = tuple(aligned_ids[i] for i in member_indices)
        member_tuple = tuple(tuple(float(x) for x in member_vecs[j]) for j in range(len(member_indices)))
        candidates.append(
            ClusterCandidate(
                engram_ids=member_ids,
                member_embeddings=member_tuple,
                cohesion=cohesion,
            )
        )
        stats.cluster_details.append(
            {"label": label, "size": len(member_indices), "cohesion": round(cohesion, 4), "kept": True}
        )

    stats.candidates = len(candidates)
    _log_stats(stats)
    return candidates


def _log_stats(stats: DetectionStats) -> None:
    if stats.skipped_reason:
        logger.info(
            "C2 R1 detect_clusters bank=%s skipped reason=%s buffer_engrams=%d",
            stats.bank_id,
            stats.skipped_reason,
            stats.buffer_engrams,
        )
        return
    logger.info(
        "C2 R1 detect_clusters bank=%s buffer_engrams=%d raw_clusters=%d cohesion_filtered=%d candidates=%d",
        stats.bank_id,
        stats.buffer_engrams,
        stats.raw_clusters,
        stats.cohesion_filtered,
        stats.candidates,
    )
    if logger.isEnabledFor(logging.DEBUG):
        for detail in stats.cluster_details:
            logger.debug("  cluster %s: size=%d cohesion=%.4f kept=%s", *detail.values())
