"""Unit tests for Epic 25 Story 03 — Schema centroid + Qdrant kind payload.

Pure unit: the Qdrant client is mocked so we assert payload/filter shapes
without touching a real Qdrant instance. Centroid math is verified against
hand-computed expectations.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.http import models as qdrant_models

from hindsight_api.engine.qdrant_client import QdrantEngineClient
from hindsight_api.engine.schema import compute_centroid

# ---------------------------------------------------------------------------
# compute_centroid — pure math
# ---------------------------------------------------------------------------


class TestComputeCentroid:
    def test_orthogonal_pair_yields_diagonal_unit_vector(self):
        # Mean of [1,0,0] and [0,1,0] is [0.5,0.5,0]; L2-norm is sqrt(2)/2 → unit.
        result = compute_centroid([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        assert result == pytest.approx([math.sqrt(2) / 2, math.sqrt(2) / 2, 0.0], rel=1e-9)
        assert math.isclose(math.sqrt(sum(x * x for x in result)), 1.0, rel_tol=1e-9)

    def test_identical_vectors_collapse_to_normalised_input(self):
        result = compute_centroid([[3.0, 4.0], [3.0, 4.0]])
        # Mean = [3,4], norm = 5 → unit [0.6, 0.8].
        assert result == pytest.approx([0.6, 0.8], rel=1e-9)

    def test_single_vector_passes_through_normalisation(self):
        result = compute_centroid([[2.0, 0.0]])
        assert result == pytest.approx([1.0, 0.0])

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            compute_centroid([])

    def test_canceling_vectors_raise_zero_norm(self):
        with pytest.raises(ValueError, match="zero vector"):
            compute_centroid([[1.0, 0.0], [-1.0, 0.0]])


# ---------------------------------------------------------------------------
# Qdrant client — payload and filter shapes
# ---------------------------------------------------------------------------


def _wired_client() -> tuple[QdrantEngineClient, MagicMock]:
    """Return a connected client whose internal driver is a MagicMock."""
    client = QdrantEngineClient(url="http://test", api_key=None, collection="engrams")
    inner = MagicMock()
    inner.upsert = AsyncMock()
    inner.set_payload = AsyncMock()
    inner.query_points = AsyncMock()
    inner.collection_exists = AsyncMock(return_value=False)
    inner.create_collection = AsyncMock()
    inner.create_payload_index = AsyncMock()
    client._client = inner  # type: ignore[attr-defined] — bypass connect() for unit tests
    return client, inner


class TestEngramUpsertForcesKind:
    async def test_upsert_point_pins_kind_engram_even_if_caller_overrides(self):
        client, inner = _wired_client()
        eid = str(uuid.uuid4())
        await client.upsert_point(
            engram_id=eid,
            embedding=[0.1] * 4,
            payload={"text": "hello", "kind": "schema"},  # malicious override
        )
        point = inner.upsert.call_args.kwargs["points"][0]
        assert point.payload["kind"] == "engram"
        assert point.payload["engram_id"] == eid
        assert point.payload["text"] == "hello"

    async def test_batch_upsert_pins_kind_engram(self):
        client, inner = _wired_client()
        eid1, eid2 = str(uuid.uuid4()), str(uuid.uuid4())
        await client.batch_upsert(
            [
                {"engram_id": eid1, "embedding": [0.1] * 4, "payload": {"kind": "wrong", "tags": ["a"]}},
                {"engram_id": eid2, "embedding": [0.2] * 4, "payload": {}},
            ]
        )
        points = inner.upsert.call_args.kwargs["points"]
        assert all(p.payload["kind"] == "engram" for p in points)
        assert points[0].payload["tags"] == ["a"]


class TestSchemaCentroidUpsert:
    async def test_writes_kind_schema_with_schema_id(self):
        client, inner = _wired_client()
        sid = str(uuid.uuid4())
        await client.upsert_schema_centroid(
            schema_id=sid,
            centroid=[0.1] * 4,
            schema_meta={"description": "coffee meetings"},
        )
        point = inner.upsert.call_args.kwargs["points"][0]
        assert point.payload["kind"] == "schema"
        assert point.payload["schema_id"] == sid
        assert point.payload["description"] == "coffee meetings"
        # No engram_id on schema points — they have their own keyword index.
        assert "engram_id" not in point.payload

    async def test_caller_kind_override_is_ignored(self):
        client, inner = _wired_client()
        sid = str(uuid.uuid4())
        await client.upsert_schema_centroid(
            schema_id=sid,
            centroid=[0.1] * 4,
            schema_meta={"kind": "engram", "schema_id": "tampered"},
        )
        point = inner.upsert.call_args.kwargs["points"][0]
        assert point.payload["kind"] == "schema"
        assert point.payload["schema_id"] == sid

    async def test_no_meta_still_works(self):
        client, inner = _wired_client()
        sid = str(uuid.uuid4())
        await client.upsert_schema_centroid(schema_id=sid, centroid=[0.0] * 4)
        point = inner.upsert.call_args.kwargs["points"][0]
        assert point.payload == {"kind": "schema", "schema_id": sid}


class TestSearchKindFilter:
    async def test_no_kind_no_filter_passes_none(self):
        client, inner = _wired_client()
        inner.query_points.return_value = MagicMock(points=[])
        await client.search_similar([0.1] * 4, limit=5)
        assert inner.query_points.call_args.kwargs["query_filter"] is None

    async def test_kind_only_builds_must_with_field_condition(self):
        client, inner = _wired_client()
        inner.query_points.return_value = MagicMock(points=[])
        await client.search_similar([0.1] * 4, kind="schema")
        f: qdrant_models.Filter = inner.query_points.call_args.kwargs["query_filter"]
        assert f is not None
        assert len(f.must) == 1
        cond = f.must[0]
        assert isinstance(cond, qdrant_models.FieldCondition)
        assert cond.key == "kind"
        assert cond.match.value == "schema"

    async def test_kind_composes_with_caller_filter(self):
        client, inner = _wired_client()
        inner.query_points.return_value = MagicMock(points=[])
        bank_cond = qdrant_models.FieldCondition(
            key="bank_id",
            match=qdrant_models.MatchValue(value="bank-A"),
        )
        await client.search_similar(
            [0.1] * 4,
            filters={"must": [bank_cond]},
            kind="engram",
        )
        f: qdrant_models.Filter = inner.query_points.call_args.kwargs["query_filter"]
        assert f is not None
        assert len(f.must) == 2
        keys = {c.key for c in f.must if isinstance(c, qdrant_models.FieldCondition)}
        assert keys == {"bank_id", "kind"}


# ---------------------------------------------------------------------------
# Migration script smoke test
# ---------------------------------------------------------------------------


def _patched_async_qdrant(scroll_pages, exists: bool = True):
    """Build a MagicMock that mimics AsyncQdrantClient for the migration script."""
    inner = MagicMock()
    inner.collection_exists = AsyncMock(return_value=exists)
    inner.scroll = AsyncMock(side_effect=scroll_pages)
    inner.set_payload = AsyncMock()
    inner.close = AsyncMock()
    return inner


def _point(point_id: str, payload: dict) -> MagicMock:
    p = MagicMock()
    p.id = point_id
    p.payload = payload
    return p


_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "dev" / "migrate_qdrant_kind_payload.py"
)


def _load_migration_module():
    """Load the migration script by file path (it lives outside hindsight-api/)."""
    spec = importlib.util.spec_from_file_location("migrate_qdrant_kind_payload", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationScript:
    def _import_module(self):
        return _load_migration_module()

    async def test_skips_already_tagged_points(self):
        module = self._import_module()
        scroll_pages = [
            (
                [
                    _point("a", {"kind": "engram"}),
                    _point("b", {"kind": "schema"}),
                ],
                None,
            ),
        ]
        inner = _patched_async_qdrant(scroll_pages)
        with patch.object(module, "AsyncQdrantClient", return_value=inner):
            scanned, updated = await module._migrate(
                url="http://test",
                api_key=None,
                collection="engrams",
                batch=10,
                dry_run=False,
            )
        assert scanned == 2
        assert updated == 0
        inner.set_payload.assert_not_called()

    async def test_stamps_unkinded_points(self):
        module = self._import_module()
        scroll_pages = [
            (
                [
                    _point("a", {}),
                    _point("b", {"text": "hi"}),
                    _point("c", {"kind": "engram"}),
                ],
                None,
            ),
        ]
        inner = _patched_async_qdrant(scroll_pages)
        with patch.object(module, "AsyncQdrantClient", return_value=inner):
            scanned, updated = await module._migrate(
                url="http://test",
                api_key=None,
                collection="engrams",
                batch=10,
                dry_run=False,
            )
        assert scanned == 3
        assert updated == 2
        inner.set_payload.assert_awaited_once()
        assert inner.set_payload.call_args.kwargs["payload"] == {"kind": "engram"}

    async def test_dry_run_does_not_write(self):
        module = self._import_module()
        scroll_pages = [
            ([_point("a", {})], None),
        ]
        inner = _patched_async_qdrant(scroll_pages)
        with patch.object(module, "AsyncQdrantClient", return_value=inner):
            scanned, updated = await module._migrate(
                url="http://test",
                api_key=None,
                collection="engrams",
                batch=10,
                dry_run=True,
            )
        assert scanned == 1
        assert updated == 1  # counted as 'would-update'
        inner.set_payload.assert_not_called()

    async def test_missing_collection_short_circuits(self):
        module = self._import_module()
        inner = _patched_async_qdrant([], exists=False)
        with patch.object(module, "AsyncQdrantClient", return_value=inner):
            scanned, updated = await module._migrate(
                url="http://test",
                api_key=None,
                collection="absent",
                batch=10,
                dry_run=False,
            )
        assert scanned == 0
        assert updated == 0
        inner.scroll.assert_not_called()
