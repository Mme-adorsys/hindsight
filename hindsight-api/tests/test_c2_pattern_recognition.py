"""Unit tests for Epic 25 Story 04 — C2 R1 cluster detection.

Pure unit: PostgreSQL pool and Qdrant client are mocked; HDBSCAN runs for
real (it's a deterministic local library, no network). Tests pin the three
acceptance behaviours from the story: clean clusters → candidates emitted,
scattered embeddings → empty list, low cohesion → filtered out.
"""

from __future__ import annotations

import math
import uuid
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from hindsight_api.engine.consolidation.c2_pattern_recognition import (
    COHESION_THRESHOLD,
    ClusterCandidate,
    DetectionStats,
    _l2_normalise,
    _mean_pairwise_cosine,
    detect_clusters,
)


def _unit(theta: float, dim: int = 8) -> list[float]:
    """Return a unit vector parameterised by an angle in the first 2 dims."""
    v = np.zeros(dim, dtype=np.float64)
    v[0] = math.cos(theta)
    v[1] = math.sin(theta)
    return v.tolist()


def _entries(ids: list[str]) -> list[dict]:
    return [{"engram_id": eid, "layer": "buffer", "status": "active"} for eid in ids]


def _qdrant_mock(ids: list[str], vectors: list[list[float]]):
    qdrant = AsyncMock()
    qdrant.retrieve_many = AsyncMock(
        return_value=[{"engram_id": eid, "vector": vec, "payload": {}} for eid, vec in zip(ids, vectors, strict=True)]
    )
    return qdrant


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_l2_normalise_unit_rows(self):
        rows = np.array([[3.0, 4.0], [0.0, 0.0], [1.0, 1.0]])
        out = _l2_normalise(rows)
        assert math.isclose(np.linalg.norm(out[0]), 1.0)
        # Zero row stays zero (guarded).
        assert np.allclose(out[1], [0.0, 0.0])
        assert math.isclose(np.linalg.norm(out[2]), 1.0)

    def test_mean_pairwise_cosine_identical_rows(self):
        rows = _l2_normalise(np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]))
        assert math.isclose(_mean_pairwise_cosine(rows), 1.0)

    def test_mean_pairwise_cosine_orthogonal_rows(self):
        rows = np.array([[1.0, 0.0], [0.0, 1.0]])
        assert math.isclose(_mean_pairwise_cosine(rows), 0.0)


# ---------------------------------------------------------------------------
# detect_clusters — full pipeline (HDBSCAN runs for real)
# ---------------------------------------------------------------------------


class TestDetectClusters:
    async def test_three_tight_clusters_yield_three_candidates(self):
        # Three tight bundles, well separated in angle space → 3 clusters.
        bundles = [
            [_unit(0.00 + d) for d in (-0.02, 0.0, 0.02)],
            [_unit(2.10 + d) for d in (-0.02, 0.0, 0.02)],
            [_unit(4.20 + d) for d in (-0.02, 0.0, 0.02)],
        ]
        ids = [str(uuid.uuid4()) for _ in range(9)]
        vectors = [v for bundle in bundles for v in bundle]

        pool = AsyncMock()
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
            new=AsyncMock(return_value=_entries(ids)),
        ):
            qdrant = _qdrant_mock(ids, vectors)
            candidates = await detect_clusters("bank-A", pool, qdrant)

        assert len(candidates) == 3
        assert all(isinstance(c, ClusterCandidate) for c in candidates)
        assert all(c.size == 3 for c in candidates)
        assert all(c.cohesion >= COHESION_THRESHOLD for c in candidates)
        # No engram appears in two candidates.
        seen: set[str] = set()
        for c in candidates:
            assert seen.isdisjoint(c.engram_ids)
            seen.update(c.engram_ids)

    async def test_scattered_embeddings_produce_no_candidates(self):
        # 12 vectors evenly spread around the circle — HDBSCAN should leave
        # them as noise (label -1). Even if it did surface something, none
        # could satisfy cohesion >= 0.75 over a 30° spread.
        n = 12
        ids = [str(uuid.uuid4()) for _ in range(n)]
        vectors = [_unit(2 * math.pi * i / n) for i in range(n)]

        pool = AsyncMock()
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
            new=AsyncMock(return_value=_entries(ids)),
        ):
            qdrant = _qdrant_mock(ids, vectors)
            candidates = await detect_clusters("bank-A", pool, qdrant)

        assert candidates == []

    async def test_low_cohesion_cluster_is_filtered(self):
        # One loose cluster of 4 spanning ~100° (0 → 1.8 rad) — mean pairwise
        # cosine ≈ 0.50, well below 0.75 — must be discarded. We patch HDBSCAN
        # to surface a single cluster so the test isolates the filter logic.
        n = 4
        ids = [str(uuid.uuid4()) for _ in range(n)]
        vectors = [_unit(0.0 + 0.6 * i) for i in range(n)]

        pool = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
                new=AsyncMock(return_value=_entries(ids)),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_pattern_recognition._run_hdbscan",
                return_value=np.zeros(n, dtype=int),
            ),
        ):
            qdrant = _qdrant_mock(ids, vectors)
            candidates = await detect_clusters("bank-A", pool, qdrant)

        assert candidates == []

    async def test_too_few_buffer_engrams_short_circuits(self):
        ids = [str(uuid.uuid4())]
        pool = AsyncMock()
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
            new=AsyncMock(return_value=_entries(ids)),
        ):
            qdrant = _qdrant_mock(ids, [_unit(0.0)])
            candidates = await detect_clusters("bank-A", pool, qdrant)
        # qdrant should not even be queried in the short-circuit path.
        qdrant.retrieve_many.assert_not_called()
        assert candidates == []

    async def test_hdbscan_failure_is_swallowed(self):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        vectors = [_unit(0.0), _unit(0.01), _unit(0.02)]
        pool = AsyncMock()
        with (
            patch(
                "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
                new=AsyncMock(return_value=_entries(ids)),
            ),
            patch(
                "hindsight_api.engine.consolidation.c2_pattern_recognition._run_hdbscan",
                side_effect=RuntimeError("synthetic"),
            ),
        ):
            qdrant = _qdrant_mock(ids, vectors)
            candidates = await detect_clusters("bank-A", pool, qdrant)
        assert candidates == []

    async def test_missing_qdrant_vectors_are_skipped(self):
        # 4 PG ids but only 2 have a vector in Qdrant — must short-circuit
        # because <3 aligned ids remain after intersect.
        ids = [str(uuid.uuid4()) for _ in range(4)]
        pool = AsyncMock()
        partial_qdrant = AsyncMock()
        partial_qdrant.retrieve_many = AsyncMock(
            return_value=[
                {"engram_id": ids[0], "vector": _unit(0.0), "payload": {}},
                {"engram_id": ids[1], "vector": _unit(0.01), "payload": {}},
            ]
        )
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.filter_entries",
            new=AsyncMock(return_value=_entries(ids)),
        ):
            candidates = await detect_clusters("bank-A", pool, partial_qdrant)
        assert candidates == []


# ---------------------------------------------------------------------------
# DetectionStats — used for log output
# ---------------------------------------------------------------------------


class TestDetectionStats:
    def test_default_counters(self):
        stats = DetectionStats(bank_id="bank-A")
        assert stats.buffer_engrams == 0
        assert stats.raw_clusters == 0
        assert stats.cohesion_filtered == 0
        assert stats.candidates == 0
        assert stats.skipped_reason is None
        assert stats.cluster_details == []


@pytest.mark.parametrize("threshold", [COHESION_THRESHOLD])
def test_threshold_constant_matches_concept(threshold):
    # concept §13 R1 fixes the cohesion floor at 0.75; tightening it without
    # also touching the concept doc would cause silent drift.
    assert threshold == 0.75
