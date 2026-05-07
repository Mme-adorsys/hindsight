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
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

import numpy as np

from ..engram_dictionary import filter_entries
from ..schema.centroid import compute_centroid
from .c2_schema_match import match_existing_schema
from .cluster_fingerprint_repository import match_or_create
from .property_aggregator import aggregate_properties

if TYPE_CHECKING:
    import asyncpg

    from ..qdrant_client import QdrantEngineClient
    from ..schema.models import SchemaModel

logger = logging.getLogger(__name__)

MIN_CLUSTER_SIZE: int = 3
COHESION_THRESHOLD: float = 0.75
# concept §13 R2 — a cluster must survive two C2 cycles before it can become
# a schema. Tightening this without also touching the concept doc would
# silently invert "filter noise" semantics.
MATURATION_MIN_CYCLES: int = 2
# Top-N tag rollup for the cluster fingerprint. Mirrors the Top-N=5 evidence
# array on Schema nodes (concept §4.2) — keeps the fingerprint comparable
# in size to the eventual schema row.
DOMINANT_TAGS_TOP_N: int = 5
# Fetch limit when scrolling buffer engrams. Banks beyond this size get a
# warning; the caller can split work into multiple C2 runs by sub-bank.
DEFAULT_BANK_SCAN_LIMIT: int = 10_000


@dataclass(frozen=True)
class ClusterCandidate:
    """Cluster of buffer engrams emitted by R1 detection.

    ``cohesion`` is the mean pairwise cosine similarity over all member
    embeddings — a single number per candidate. ``member_tags`` mirrors
    ``member_embeddings`` index-by-index so the R2 step (Story 05) can
    compute dominant tags without re-querying PostgreSQL.
    """

    engram_ids: tuple[str, ...]
    member_embeddings: tuple[tuple[float, ...], ...]
    cohesion: float
    member_tags: tuple[tuple[str, ...], ...] = ()

    @property
    def size(self) -> int:
        return len(self.engram_ids)


@dataclass(frozen=True)
class MaturedClusterCandidate:
    """R1 candidate enriched with R2 fingerprint outcome (concept §13 R2).

    A candidate becomes a schema seed only once ``cycles_survived >= 2``
    (``MATURATION_MIN_CYCLES``) — see ``filter_matured``. ``member_tags``
    carries the per-engram tag rows from the R1 step so Story 07's
    property aggregator can run without a fresh PG round-trip.
    """

    engram_ids: tuple[str, ...]
    centroid: tuple[float, ...]
    dominant_tags: tuple[str, ...]
    cycles_survived: int
    fingerprint_id: UUID
    matched_existing: bool
    cohesion: float
    member_tags: tuple[tuple[str, ...], ...] = ()

    @property
    def is_mature(self) -> bool:
        return self.cycles_survived >= MATURATION_MIN_CYCLES


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
    tags_by_id: dict[str, tuple[str, ...]] = {str(e["engram_id"]): tuple(e.get("tags") or ()) for e in entries}
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
        member_tags = tuple(tags_by_id.get(eid, ()) for eid in member_ids)
        candidates.append(
            ClusterCandidate(
                engram_ids=member_ids,
                member_embeddings=member_tuple,
                cohesion=cohesion,
                member_tags=member_tags,
            )
        )
        stats.cluster_details.append(
            {"label": label, "size": len(member_indices), "cohesion": round(cohesion, 4), "kept": True}
        )

    stats.candidates = len(candidates)
    _log_stats(stats)
    return candidates


def _dominant_tags(member_tags: tuple[tuple[str, ...], ...], top_n: int = DOMINANT_TAGS_TOP_N) -> tuple[str, ...]:
    """Return the ``top_n`` most-frequent tags across cluster members.

    Ties are broken by Counter's insertion order (CPython detail), which is
    stable enough for fingerprint matching — what matters is the multiset,
    not the precise ordering across runs.
    """
    counts: Counter[str] = Counter()
    for tags in member_tags:
        counts.update(tags)
    return tuple(tag for tag, _ in counts.most_common(top_n))


async def mature_clusters(
    candidates: list[ClusterCandidate],
    bank_id: str,
    pool: "asyncpg.Pool",
) -> list[MaturedClusterCandidate]:
    """Run R2 maturation: fingerprint-match each candidate, increment cycles.

    Each candidate's centroid is matched against bank-scoped fingerprints in
    PostgreSQL via ``cluster_fingerprint_repository.match_or_create``. The
    return preserves input order; downstream stories get
    ``cycles_survived`` and the originating ``fingerprint_id`` for free.
    """
    matured: list[MaturedClusterCandidate] = []
    for cand in candidates:
        centroid = compute_centroid([list(vec) for vec in cand.member_embeddings])
        tags = _dominant_tags(cand.member_tags)
        outcome = await match_or_create(pool, bank_id, centroid, list(tags))
        matured.append(
            MaturedClusterCandidate(
                engram_ids=cand.engram_ids,
                centroid=tuple(centroid),
                dominant_tags=tags,
                cycles_survived=outcome.cycles_survived,
                fingerprint_id=outcome.fingerprint_id,
                matched_existing=outcome.matched_existing,
                cohesion=cand.cohesion,
                member_tags=cand.member_tags,
            )
        )
    logger.info(
        "C2 R2 mature_clusters bank=%s candidates=%d matched=%d new=%d",
        bank_id,
        len(matured),
        sum(1 for m in matured if m.matched_existing),
        sum(1 for m in matured if not m.matched_existing),
    )
    return matured


def filter_matured(candidates: list[MaturedClusterCandidate]) -> list[MaturedClusterCandidate]:
    """Keep only candidates that survived ≥ ``MATURATION_MIN_CYCLES`` cycles."""
    return [c for c in candidates if c.is_mature]


@dataclass(frozen=True)
class MatchedForReinforcement:
    """Mature cluster + the existing schema it should reinforce (Story 06)."""

    cluster: MaturedClusterCandidate
    schema: "SchemaModel"
    cosine: float


@dataclass(frozen=True)
class UnmatchedForCreation:
    """Mature cluster with no existing-schema match — feeds Story 09 creation."""

    cluster: MaturedClusterCandidate
    best_cosine: float


@dataclass(frozen=True)
class ConsolidationPlan:
    """Two-bucket output of :func:`partition_for_consolidation`.

    Stories 09 and 10 each consume exactly one bucket; the union covers all
    matured candidates from R2 (concept §13 R2 → R4 split).
    """

    reinforcement: tuple[MatchedForReinforcement, ...]
    creation: tuple[UnmatchedForCreation, ...]


async def partition_for_consolidation(
    matured: list[MaturedClusterCandidate],
    bank_id: str,
    qdrant: "QdrantEngineClient",
    schema_lookup,
) -> ConsolidationPlan:
    """Split matured candidates into reinforcement vs creation buckets.

    For each ``is_mature`` candidate, ask Qdrant whether a schema in the same
    bank sits within ``SCHEMA_MATCH_THRESHOLD`` cosine of the centroid. Hits
    feed Story 10 (R4 reinforcement); misses feed Story 09 (schema creation).

    Non-mature candidates (``cycles_survived < MATURATION_MIN_CYCLES``) are
    silently dropped — they ride in the cluster_fingerprint store waiting
    for a future C2 cycle to push them over the threshold.
    """
    reinforcement: list[MatchedForReinforcement] = []
    creation: list[UnmatchedForCreation] = []
    skipped_immature = 0

    for cand in matured:
        if not cand.is_mature:
            skipped_immature += 1
            continue
        schema, score = await match_existing_schema(
            qdrant=qdrant,
            schema_lookup=schema_lookup,
            centroid=list(cand.centroid),
            bank_id=bank_id,
        )
        if schema is not None:
            reinforcement.append(MatchedForReinforcement(cluster=cand, schema=schema, cosine=score))
        else:
            creation.append(UnmatchedForCreation(cluster=cand, best_cosine=score))

    logger.info(
        "C2 partition_for_consolidation bank=%s reinforcement=%d creation=%d skipped_immature=%d",
        bank_id,
        len(reinforcement),
        len(creation),
        skipped_immature,
    )
    return ConsolidationPlan(reinforcement=tuple(reinforcement), creation=tuple(creation))


@dataclass(frozen=True)
class CreationPayload:
    """Schema-creation input bundle (Story 07 → Story 09).

    ``properties`` is the deterministic statistical aggregation of the
    cluster's tags (concept §13 R3 Engram-level extraction). The schema's
    description (Story 08) and the Neo4j/Qdrant write (Story 09) consume
    this bundle alongside the cluster's centroid.
    """

    cluster: MaturedClusterCandidate
    properties: dict[str, Any]


def prepare_creation_payloads(creation: tuple[UnmatchedForCreation, ...]) -> tuple[CreationPayload, ...]:
    """Run :func:`aggregate_properties` per creation-bucket candidate.

    No I/O — purely a tag-rollup over the cluster's already-fetched
    ``member_tags``. Story 09 will pair the resulting properties with the
    centroid to mint a fresh ``:Schema`` node.
    """
    payloads = tuple(
        CreationPayload(
            cluster=item.cluster,
            properties=aggregate_properties([list(t) for t in item.cluster.member_tags]),
        )
        for item in creation
    )
    logger.info(
        "C2 prepare_creation_payloads count=%d",
        len(payloads),
    )
    return payloads


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
