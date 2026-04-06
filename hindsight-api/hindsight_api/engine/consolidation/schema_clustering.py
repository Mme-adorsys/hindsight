"""
NCR Phase 3 — Schema Emergence, Rule R1: Clustering / Birth.

Detects clusters of neocortex-layer Engrams that share M+ common neighbours
in the Neo4j graph. Matching clusters are persisted as `ClusterCandidate` nodes
and are handed to the Maturation step (R2) in subsequent NCR cycles.

Biological mapping:
  REM Sleep — whole-graph pattern detection. Repeated co-activation signatures
  in the neocortex gradually surface abstract structure.
  Ref: concept.md ch. 13 — Schema Emergence, R1 Clustering/Birth

Epic 13, Story 01.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hindsight_api.engine.neo4j_client import Neo4jEngineClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_MIN_CLUSTER_SIZE: int = 3
_DEFAULT_MIN_SHARED_NEIGHBORS: int = 2
_MERGE_OVERLAP_THRESHOLD: float = 0.80  # ≥80% member overlap → merge clusters

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

# Find pairs of neocortex Engrams sharing at least one common neighbour.
# Returns (id_a, id_b, shared_count).
_FIND_PAIRS_CYPHER = """
MATCH (a:Engram)-[]->(shared)<-[]-(b:Engram)
WHERE a.engram_id < b.engram_id
  AND a.layer = 'neocortex' AND a.bank_id = $bank_id
  AND b.layer = 'neocortex' AND b.bank_id = $bank_id
  AND a.status = 'active'  AND b.status = 'active'
WITH a.engram_id AS id_a, b.engram_id AS id_b, count(DISTINCT shared) AS shared_count
WHERE shared_count >= $min_shared
RETURN id_a, id_b, shared_count
"""

# MERGE a ClusterCandidate node; only update mutable fields on match.
_MERGE_CANDIDATE_CYPHER = """
MERGE (c:ClusterCandidate {candidate_id: $candidate_id, bank_id: $bank_id})
ON CREATE SET
    c.member_ids         = $member_ids,
    c.cohesion           = $cohesion,
    c.ncr_cycles_survived = 0,
    c.created_at         = $now,
    c.updated_at         = $now
ON MATCH SET
    c.member_ids         = $member_ids,
    c.cohesion           = $cohesion,
    c.updated_at         = $now
"""

# Fetch all existing ClusterCandidates for this bank.
_FETCH_CANDIDATES_CYPHER = """
MATCH (c:ClusterCandidate {bank_id: $bank_id})
RETURN c.candidate_id AS candidate_id, c.member_ids AS member_ids,
       c.cohesion AS cohesion, c.ncr_cycles_survived AS ncr_cycles_survived
"""

# Delete a ClusterCandidate by id.
_DELETE_CANDIDATE_CYPHER = """
MATCH (c:ClusterCandidate {candidate_id: $candidate_id, bank_id: $bank_id})
DETACH DELETE c
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ClusterCandidate:
    """In-memory representation of a detected Engram cluster."""

    candidate_id: str  # Deterministic hash of sorted member IDs
    bank_id: str
    member_ids: list[str]
    cohesion: float  # Average shared-neighbour count (normalised)
    ncr_cycles_survived: int = 0


@dataclass
class ClusteringResult:
    """Summary returned after one R1 run."""

    candidates_found: int = 0
    candidates_created: int = 0
    candidates_updated: int = 0
    candidates_merged: int = 0
    details: list[ClusterCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_id(member_ids: list[str]) -> str:
    """Deterministic ID from sorted member list."""
    key = ",".join(sorted(member_ids))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    """Overlap coefficient: |A∩B| / min(|A|,|B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _build_clusters_from_pairs(
    pairs: list[dict[str, Any]],
    min_cluster_size: int,
    min_shared_neighbors: int,
) -> list[tuple[frozenset[str], float]]:
    """
    Convert pair-wise shared-neighbour data into clusters via Union-Find.

    Returns a list of (member_frozenset, avg_cohesion) tuples that pass the
    minimum-size and minimum-shared-neighbor filters.
    """
    # Union-Find
    parent: dict[str, str] = {}
    edge_shared: dict[tuple[str, str], int] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), x)
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for row in pairs:
        id_a: str = row["id_a"]
        id_b: str = row["id_b"]
        shared_count: int = int(row["shared_count"])
        if shared_count < min_shared_neighbors:
            continue
        if id_a not in parent:
            parent[id_a] = id_a
        if id_b not in parent:
            parent[id_b] = id_b
        union(id_a, id_b)
        key = (min(id_a, id_b), max(id_a, id_b))
        edge_shared[key] = max(edge_shared.get(key, 0), shared_count)

    # Group members by root
    groups: dict[str, set[str]] = {}
    for node in parent:
        root = find(node)
        groups.setdefault(root, set()).add(node)

    # Compute cohesion per group: mean shared-neighbour count over edges
    results: list[tuple[frozenset[str], float]] = []
    for members in groups.values():
        if len(members) < min_cluster_size:
            continue
        member_list = list(members)
        edge_counts: list[int] = []
        for i in range(len(member_list)):
            for j in range(i + 1, len(member_list)):
                key = (min(member_list[i], member_list[j]), max(member_list[i], member_list[j]))
                if key in edge_shared:
                    edge_counts.append(edge_shared[key])
        avg_cohesion = sum(edge_counts) / len(edge_counts) if edge_counts else 0.0
        results.append((frozenset(members), avg_cohesion))

    return results


def _deduplicate_clusters(
    clusters: list[tuple[frozenset[str], float]],
    overlap_threshold: float = _MERGE_OVERLAP_THRESHOLD,
) -> list[tuple[frozenset[str], float]]:
    """
    Merge clusters whose member overlap exceeds *overlap_threshold*.

    When two clusters are merged the one with higher cohesion × size wins.
    Returns deduplicated list.
    """
    # Sort by score descending so we keep the "best" cluster first
    ranked = sorted(clusters, key=lambda t: t[1] * len(t[0]), reverse=True)
    kept: list[tuple[frozenset[str], float]] = []

    for members, cohesion in ranked:
        dominated = False
        for km, _ in kept:
            if _overlap_ratio(set(members), set(km)) >= overlap_threshold:
                dominated = True
                break
        if not dominated:
            kept.append((members, cohesion))

    return kept


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_clustering(
    bank_id: str,
    neo4j_client: Neo4jEngineClient,
    *,
    min_cluster_size: int = _DEFAULT_MIN_CLUSTER_SIZE,
    min_shared_neighbors: int = _DEFAULT_MIN_SHARED_NEIGHBORS,
    overlap_threshold: float = _MERGE_OVERLAP_THRESHOLD,
) -> ClusteringResult:
    """
    Execute R1 — Clustering/Birth for *bank_id*.

    1. Query Neo4j for Engram pairs with ≥ min_shared_neighbors common neighbours.
    2. Assemble pairs into clusters (Union-Find).
    3. Validate minimum cluster size.
    4. Deduplicate overlapping clusters.
    5. MERGE ClusterCandidate nodes in Neo4j.

    Args:
        bank_id: Memory bank to process.
        neo4j_client: Connected Neo4j client.
        min_cluster_size: Minimum Engrams per cluster (default 3).
        min_shared_neighbors: Minimum shared neighbours per pair (default 2).
        overlap_threshold: Overlap ratio above which two clusters are merged (default 0.8).

    Returns:
        ClusteringResult with counts and candidate details.
    """
    result = ClusteringResult()

    # ── Step 1: Fetch pairs from Neo4j ──────────────────────────────────────
    try:
        pairs = await neo4j_client.run_cypher(
            _FIND_PAIRS_CYPHER,
            {"bank_id": bank_id, "min_shared": min_shared_neighbors},
        )
    except Exception as exc:
        logger.error(f"[R1] Pair query failed for bank {bank_id}: {exc}")
        return result

    if not pairs:
        logger.debug(f"[R1] No qualifying pairs found for bank {bank_id}")
        return result

    # ── Step 2+3: Build and validate clusters ────────────────────────────────
    raw_clusters = _build_clusters_from_pairs(pairs, min_cluster_size, min_shared_neighbors)

    if not raw_clusters:
        logger.debug(f"[R1] No clusters passed min-size filter for bank {bank_id}")
        return result

    # ── Step 4: Deduplication ────────────────────────────────────────────────
    before_dedup = len(raw_clusters)
    deduped = _deduplicate_clusters(raw_clusters, overlap_threshold)
    result.candidates_merged = before_dedup - len(deduped)
    result.candidates_found = len(deduped)

    # ── Step 5: Fetch existing candidates for update detection ───────────────
    try:
        existing_rows = await neo4j_client.run_cypher(_FETCH_CANDIDATES_CYPHER, {"bank_id": bank_id})
    except Exception as exc:
        logger.warning(f"[R1] Could not fetch existing candidates for bank {bank_id}: {exc}")
        existing_rows = []

    existing_ids = {row["candidate_id"] for row in existing_rows}

    # ── Step 6: MERGE candidates ─────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).isoformat()
    for members, cohesion in deduped:
        member_list = sorted(members)
        cid = _candidate_id(member_list)
        candidate = ClusterCandidate(
            candidate_id=cid,
            bank_id=bank_id,
            member_ids=member_list,
            cohesion=cohesion,
        )
        try:
            await neo4j_client.run_cypher(
                _MERGE_CANDIDATE_CYPHER,
                {
                    "candidate_id": cid,
                    "bank_id": bank_id,
                    "member_ids": member_list,
                    "cohesion": round(cohesion, 6),
                    "now": now_str,
                },
            )
            if cid in existing_ids:
                result.candidates_updated += 1
            else:
                result.candidates_created += 1
            result.details.append(candidate)
        except Exception as exc:
            logger.error(f"[R1] MERGE failed for candidate {cid}: {exc}")

    logger.info(
        f"[R1] bank={bank_id} found={result.candidates_found} "
        f"created={result.candidates_created} updated={result.candidates_updated} "
        f"merged_away={result.candidates_merged}"
    )
    return result
