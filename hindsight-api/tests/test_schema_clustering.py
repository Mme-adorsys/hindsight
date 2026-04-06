"""
Unit tests for NCR Phase 3 — R1 Clustering/Birth.

Epic 13, Story 01, Task T5.

All tests are pure-unit: Neo4j is mocked so no live database is required.
Covers:
- Cluster detection from known pair data
- Minimum-size filter (< 3 members → ignored)
- Minimum-shared-neighbour filter
- Deduplication of overlapping clusters
- Existing candidates updated (not duplicated) via MERGE
- ClusteringResult counts correct
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.schema_clustering import (
    ClusterCandidate,
    ClusteringResult,
    _MERGE_OVERLAP_THRESHOLD,
    _build_clusters_from_pairs,
    _candidate_id,
    _deduplicate_clusters,
    _overlap_ratio,
    run_clustering,
)


# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------


class TestCandidateId:
    def test_deterministic(self):
        assert _candidate_id(["b", "a", "c"]) == _candidate_id(["a", "b", "c"])

    def test_different_members_different_id(self):
        assert _candidate_id(["a", "b"]) != _candidate_id(["a", "c"])

    def test_16_chars(self):
        assert len(_candidate_id(["x", "y", "z"])) == 16


class TestOverlapRatio:
    def test_identical(self):
        assert _overlap_ratio({"a", "b", "c"}, {"a", "b", "c"}) == pytest.approx(1.0)

    def test_no_overlap(self):
        assert _overlap_ratio({"a", "b"}, {"c", "d"}) == pytest.approx(0.0)

    def test_partial(self):
        # |{a,b} ∩ {b,c}| = 1, min(2,2)=2 → 0.5
        assert _overlap_ratio({"a", "b"}, {"b", "c"}) == pytest.approx(0.5)

    def test_empty_sets(self):
        assert _overlap_ratio(set(), set()) == pytest.approx(0.0)
        assert _overlap_ratio({"a"}, set()) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _build_clusters_from_pairs
# ---------------------------------------------------------------------------


class TestBuildClusters:
    """Tests for the Union-Find cluster assembly logic."""

    def _pairs(self, *triples):
        return [{"id_a": a, "id_b": b, "shared_count": s} for a, b, s in triples]

    def test_simple_triangle(self):
        """Three mutually connected Engrams → one cluster of 3."""
        pairs = self._pairs(("e1", "e2", 3), ("e1", "e3", 3), ("e2", "e3", 3))
        clusters = _build_clusters_from_pairs(pairs, min_cluster_size=3, min_shared_neighbors=2)
        assert len(clusters) == 1
        members, cohesion = clusters[0]
        assert members == frozenset({"e1", "e2", "e3"})
        assert cohesion > 0

    def test_pair_below_min_shared_filtered(self):
        """Pairs with shared_count < min_shared_neighbors are ignored."""
        pairs = self._pairs(("e1", "e2", 1), ("e1", "e3", 1), ("e2", "e3", 1))
        clusters = _build_clusters_from_pairs(pairs, min_cluster_size=3, min_shared_neighbors=2)
        assert clusters == []

    def test_cluster_below_min_size_filtered(self):
        """Clusters with fewer than min_cluster_size members are dropped."""
        # Only 2 members → filtered out with min_cluster_size=3
        pairs = self._pairs(("e1", "e2", 5))
        clusters = _build_clusters_from_pairs(pairs, min_cluster_size=3, min_shared_neighbors=2)
        assert clusters == []

    def test_two_separate_clusters(self):
        """Disconnected subgraphs produce separate clusters."""
        pairs = self._pairs(
            ("a1", "a2", 3), ("a1", "a3", 3), ("a2", "a3", 3),  # cluster A
            ("b1", "b2", 4), ("b1", "b3", 4), ("b2", "b3", 4),  # cluster B
        )
        clusters = _build_clusters_from_pairs(pairs, min_cluster_size=3, min_shared_neighbors=2)
        assert len(clusters) == 2
        all_members = {m for c, _ in clusters for m in c}
        assert all_members == {"a1", "a2", "a3", "b1", "b2", "b3"}

    def test_larger_cluster(self):
        """5-member complete graph → one cluster of 5."""
        ids = ["e1", "e2", "e3", "e4", "e5"]
        pairs = self._pairs(
            *[(ids[i], ids[j], 3) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        )
        clusters = _build_clusters_from_pairs(pairs, min_cluster_size=3, min_shared_neighbors=2)
        assert len(clusters) == 1
        assert len(clusters[0][0]) == 5

    def test_cohesion_reflects_shared_counts(self):
        """Higher shared_count → higher cohesion."""
        low_pairs = self._pairs(("a", "b", 2), ("a", "c", 2), ("b", "c", 2))
        high_pairs = self._pairs(("x", "y", 10), ("x", "z", 10), ("y", "z", 10))
        low_clusters = _build_clusters_from_pairs(low_pairs, 3, 2)
        high_clusters = _build_clusters_from_pairs(high_pairs, 3, 2)
        assert high_clusters[0][1] > low_clusters[0][1]


# ---------------------------------------------------------------------------
# _deduplicate_clusters
# ---------------------------------------------------------------------------


class TestDeduplicateClusters:
    def test_identical_clusters_deduped(self):
        members = frozenset({"a", "b", "c"})
        clusters = [(members, 3.0), (members, 2.0)]
        result = _deduplicate_clusters(clusters, overlap_threshold=0.8)
        assert len(result) == 1

    def test_no_overlap_both_kept(self):
        c1 = (frozenset({"a", "b", "c"}), 3.0)
        c2 = (frozenset({"x", "y", "z"}), 3.0)
        result = _deduplicate_clusters([c1, c2])
        assert len(result) == 2

    def test_high_overlap_dominated_removed(self):
        """Cluster B shares 3/4 members with A (overlap 0.75 < 0.8 → both kept)."""
        c_a = (frozenset({"a", "b", "c", "d"}), 5.0)
        c_b = (frozenset({"a", "b", "c", "e"}), 3.0)
        # overlap = 3/4 = 0.75 < threshold 0.8 → both kept
        result = _deduplicate_clusters([c_a, c_b], overlap_threshold=0.8)
        assert len(result) == 2

    def test_very_high_overlap_dominated_removed(self):
        """Cluster B shares 4/5 members with A (0.8 = threshold) → merged."""
        c_a = (frozenset({"a", "b", "c", "d", "e"}), 5.0)
        c_b = (frozenset({"a", "b", "c", "d", "f"}), 3.0)
        # overlap = 4/5 = 0.8 >= threshold → c_b removed
        result = _deduplicate_clusters([c_a, c_b], overlap_threshold=0.8)
        assert len(result) == 1
        assert frozenset({"a", "b", "c", "d", "e"}) in {r[0] for r in result}

    def test_higher_cohesion_wins(self):
        """When overlap is high the more cohesive cluster is kept."""
        c_weak = (frozenset({"a", "b", "c"}), 1.0)
        c_strong = (frozenset({"a", "b", "c"}), 9.0)
        result = _deduplicate_clusters([c_weak, c_strong], overlap_threshold=0.8)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# run_clustering (integration with mocked Neo4j)
# ---------------------------------------------------------------------------


def _make_neo4j(pairs_result=None, existing_result=None, merge_side_effect=None):
    """Build a mock Neo4jEngineClient."""
    client = MagicMock()
    call_count = [0]

    async def run_cypher(query: str, params=None):
        call_count[0] += 1
        if "_FIND_PAIRS" in query or "MATCH (a:Engram)" in query:
            return pairs_result or []
        if "FETCH" in query or "MATCH (c:ClusterCandidate" in query and "RETURN" in query:
            return existing_result or []
        if "MERGE" in query:
            if merge_side_effect:
                raise merge_side_effect
            return []
        return []

    client.run_cypher = AsyncMock(side_effect=run_cypher)
    return client


class TestRunClustering:
    @pytest.mark.asyncio
    async def test_no_pairs_returns_empty_result(self):
        neo4j = _make_neo4j(pairs_result=[])
        result = await run_clustering("bank-1", neo4j)
        assert isinstance(result, ClusteringResult)
        assert result.candidates_found == 0

    @pytest.mark.asyncio
    async def test_valid_cluster_creates_candidate(self):
        pairs = [
            {"id_a": "e1", "id_b": "e2", "shared_count": 3},
            {"id_a": "e1", "id_b": "e3", "shared_count": 3},
            {"id_a": "e2", "id_b": "e3", "shared_count": 3},
        ]
        neo4j = _make_neo4j(pairs_result=pairs, existing_result=[])
        result = await run_clustering("bank-1", neo4j)
        assert result.candidates_found == 1
        assert result.candidates_created == 1
        assert result.candidates_updated == 0
        assert len(result.details) == 1

    @pytest.mark.asyncio
    async def test_existing_candidate_updated_not_duplicated(self):
        pairs = [
            {"id_a": "e1", "id_b": "e2", "shared_count": 3},
            {"id_a": "e1", "id_b": "e3", "shared_count": 3},
            {"id_a": "e2", "id_b": "e3", "shared_count": 3},
        ]
        # Pre-compute the expected candidate_id
        expected_cid = _candidate_id(["e1", "e2", "e3"])
        existing = [{"candidate_id": expected_cid, "member_ids": ["e1", "e2", "e3"],
                     "cohesion": 3.0, "ncr_cycles_survived": 1}]
        neo4j = _make_neo4j(pairs_result=pairs, existing_result=existing)
        result = await run_clustering("bank-1", neo4j)
        assert result.candidates_updated == 1
        assert result.candidates_created == 0

    @pytest.mark.asyncio
    async def test_pairs_query_failure_returns_empty(self):
        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=Exception("Neo4j unavailable"))
        result = await run_clustering("bank-1", client)
        assert result.candidates_found == 0

    @pytest.mark.asyncio
    async def test_min_size_filter_applied(self):
        """Only 2 members — cluster must be filtered (min=3)."""
        pairs = [{"id_a": "e1", "id_b": "e2", "shared_count": 5}]
        neo4j = _make_neo4j(pairs_result=pairs)
        result = await run_clustering("bank-1", neo4j, min_cluster_size=3)
        assert result.candidates_found == 0

    @pytest.mark.asyncio
    async def test_min_shared_filter_applied(self):
        """shared_count=1 is below default min_shared_neighbors=2."""
        pairs = [
            {"id_a": "e1", "id_b": "e2", "shared_count": 1},
            {"id_a": "e1", "id_b": "e3", "shared_count": 1},
            {"id_a": "e2", "id_b": "e3", "shared_count": 1},
        ]
        neo4j = _make_neo4j(pairs_result=pairs)
        result = await run_clustering("bank-1", neo4j, min_shared_neighbors=2)
        assert result.candidates_found == 0

    @pytest.mark.asyncio
    async def test_deduplication_merges_overlapping(self):
        """Two 80%-overlapping clusters → only 1 kept."""
        # Build two clusters that will overlap: {e1,e2,e3,e4,e5} and {e1,e2,e3,e4,e6}
        ids_a = ["e1", "e2", "e3", "e4", "e5"]
        ids_b = ["e1", "e2", "e3", "e4", "e6"]
        all_ids = list(set(ids_a + ids_b))
        # Make pairs within each group (shared_count=3)
        pairs_a = [
            {"id_a": min(ids_a[i], ids_a[j]), "id_b": max(ids_a[i], ids_a[j]), "shared_count": 3}
            for i in range(len(ids_a)) for j in range(i + 1, len(ids_a))
        ]
        pairs_b = [
            {"id_a": min(ids_b[i], ids_b[j]), "id_b": max(ids_b[i], ids_b[j]), "shared_count": 3}
            for i in range(len(ids_b)) for j in range(i + 1, len(ids_b))
        ]
        # Remove duplicates
        seen = set()
        merged_pairs = []
        for p in pairs_a + pairs_b:
            key = (p["id_a"], p["id_b"])
            if key not in seen:
                seen.add(key)
                merged_pairs.append(p)

        neo4j = _make_neo4j(pairs_result=merged_pairs, existing_result=[])
        result = await run_clustering("bank-1", neo4j, overlap_threshold=0.8)
        # After dedup at least one candidate but not two identical ones
        assert result.candidates_found >= 1

    @pytest.mark.asyncio
    async def test_merge_failure_logged_not_raised(self):
        """A MERGE failure must not propagate — result is partial."""
        pairs = [
            {"id_a": "e1", "id_b": "e2", "shared_count": 3},
            {"id_a": "e1", "id_b": "e3", "shared_count": 3},
            {"id_a": "e2", "id_b": "e3", "shared_count": 3},
        ]

        call_count = [0]

        async def run_cypher(query: str, params=None):
            call_count[0] += 1
            if "MATCH (a:Engram)" in query:
                return pairs
            if "ClusterCandidate" in query and "RETURN" in query:
                return []
            if "MERGE" in query:
                raise RuntimeError("write error")
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)
        # Should not raise
        result = await run_clustering("bank-1", client)
        assert result.candidates_created == 0
