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
    DOMINANT_TAGS_TOP_N,
    MATURATION_MIN_CYCLES,
    ClusterCandidate,
    DetectionStats,
    MaturedClusterCandidate,
    _dominant_tags,
    _l2_normalise,
    _mean_pairwise_cosine,
    detect_clusters,
    filter_matured,
    mature_clusters,
)
from hindsight_api.engine.consolidation.cluster_fingerprint_repository import FingerprintMatch


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


# ---------------------------------------------------------------------------
# Story 05 — R2 maturation
# ---------------------------------------------------------------------------


def _candidate(*, tags: tuple[tuple[str, ...], ...] = ()) -> ClusterCandidate:
    """Build a synthetic candidate for maturation tests."""
    member_embeddings = ((1.0, 0.0), (0.99, 0.01), (0.98, 0.02))
    return ClusterCandidate(
        engram_ids=tuple(str(uuid.uuid4()) for _ in range(3)),
        member_embeddings=member_embeddings,
        cohesion=0.9,
        member_tags=tags or (("a",), ("b",), ("c",)),
    )


class TestDominantTags:
    def test_top_n_by_frequency(self):
        member_tags = (
            ("work", "ml"),
            ("work", "ml", "deploy"),
            ("ml", "deploy"),
        )
        # 'ml' x3, 'work' x2, 'deploy' x2
        result = _dominant_tags(member_tags, top_n=2)
        assert result[0] == "ml"
        assert "work" in result or "deploy" in result

    def test_top_n_default_caps_at_concept_constant(self):
        assert DOMINANT_TAGS_TOP_N == 5

    def test_empty_member_tags_yields_empty_tuple(self):
        assert _dominant_tags(((), (), ())) == ()


class TestMatureClusters:
    async def test_first_run_yields_cycles_one_not_mature(self):
        cand = _candidate()
        pool = AsyncMock()
        new_id = uuid.uuid4()
        match_outcome = FingerprintMatch(
            fingerprint_id=new_id,
            cycles_survived=1,
            matched_existing=False,
            cosine=None,
        )
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.match_or_create",
            new=AsyncMock(return_value=match_outcome),
        ) as mocked:
            matured = await mature_clusters([cand], "bank-A", pool)

        assert len(matured) == 1
        result = matured[0]
        assert isinstance(result, MaturedClusterCandidate)
        assert result.cycles_survived == 1
        assert result.matched_existing is False
        assert result.is_mature is False
        assert result.fingerprint_id == new_id
        # Repository was called with the bank, the computed centroid, and the
        # dominant-tag rollup (which here is just ['a','b','c'] in some order).
        args, _ = mocked.await_args
        assert args[1] == "bank-A"
        assert isinstance(args[2], list)
        assert len(args[2]) == 2  # centroid is 2-d in the synthetic candidate
        assert sorted(args[3]) == ["a", "b", "c"]

    async def test_second_run_match_marks_mature(self):
        cand = _candidate()
        pool = AsyncMock()
        existing_id = uuid.uuid4()
        match_outcome = FingerprintMatch(
            fingerprint_id=existing_id,
            cycles_survived=2,
            matched_existing=True,
            cosine=0.91,
        )
        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.match_or_create",
            new=AsyncMock(return_value=match_outcome),
        ):
            matured = await mature_clusters([cand], "bank-A", pool)

        result = matured[0]
        assert result.matched_existing is True
        assert result.cycles_survived == 2
        assert result.is_mature is True

    async def test_filter_matured_keeps_only_min_cycles(self):
        c1 = MaturedClusterCandidate(
            engram_ids=("e1",),
            centroid=(1.0,),
            dominant_tags=(),
            cycles_survived=1,
            fingerprint_id=uuid.uuid4(),
            matched_existing=False,
            cohesion=0.9,
        )
        c2 = MaturedClusterCandidate(
            engram_ids=("e2",),
            centroid=(1.0,),
            dominant_tags=(),
            cycles_survived=2,
            fingerprint_id=uuid.uuid4(),
            matched_existing=True,
            cohesion=0.95,
        )
        c3 = MaturedClusterCandidate(
            engram_ids=("e3",),
            centroid=(1.0,),
            dominant_tags=(),
            cycles_survived=4,
            fingerprint_id=uuid.uuid4(),
            matched_existing=True,
            cohesion=0.88,
        )
        kept = filter_matured([c1, c2, c3])
        assert kept == [c2, c3]

    async def test_centroid_is_l2_normalised(self):
        cand = _candidate()
        pool = AsyncMock()
        captured: dict = {}

        async def _fake(_pool, bank_id, centroid, tags):
            captured["centroid"] = centroid
            captured["bank_id"] = bank_id
            captured["tags"] = tags
            return FingerprintMatch(
                fingerprint_id=uuid.uuid4(),
                cycles_survived=1,
                matched_existing=False,
                cosine=None,
            )

        with patch(
            "hindsight_api.engine.consolidation.c2_pattern_recognition.match_or_create",
            new=_fake,
        ):
            await mature_clusters([cand], "bank-A", pool)

        norm = math.sqrt(sum(x * x for x in captured["centroid"]))
        assert math.isclose(norm, 1.0, rel_tol=1e-6)


def test_maturation_min_cycles_locked():
    # concept §13 R2 fixes survival threshold at 2 cycles.
    assert MATURATION_MIN_CYCLES == 2
