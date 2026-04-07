"""
Unit tests for Epic 13 Story 04 — EngramSchemaProcessor.

All tests are pure-unit: Neo4j, Qdrant, asyncpg Pool, and the LLM are mocked.
Covers:
- EngramSchemaProcessor.process() orchestrates R1 → R2+R3 → R5 in order
- SchemaResult fields populated correctly from sub-module results
- Fault isolation: failure in R1 does not prevent R2+R3 or R5
- No sub-step called when previous step errors (R1 error → R2+R3 still runs)
- backward-compat aliases (created, deleted) are set alongside new fields
- SchemaResult new fields default to 0
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.engram_schema_processor import EngramSchemaProcessor
from hindsight_api.engine.consolidation.schema_clustering import ClusteringResult
from hindsight_api.engine.consolidation.schema_competition import CompetitionResult
from hindsight_api.engine.consolidation.schema_maturation import MaturationResult
from hindsight_api.engine.consolidation.schema_processor import SchemaResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor():
    neo4j = MagicMock()
    qdrant = MagicMock()
    pool = MagicMock()
    llm = MagicMock()

    async def _embed(text: str) -> list[float]:
        return [0.1] * 4

    return EngramSchemaProcessor(neo4j, qdrant, pool, llm, _embed)


def _cluster_result(candidates_created: int = 2) -> ClusteringResult:
    return ClusteringResult(candidates_created=candidates_created, details=[])


def _mat_result(schemas_created: int = 1, candidates_incremented: int = 2) -> MaturationResult:
    r = MaturationResult()
    r.schemas_created = schemas_created
    r.candidates_incremented = candidates_incremented
    return r


def _comp_result(schemas_deleted: int = 1, active_schemas: int = 3, avg_strength: float = 0.6) -> CompetitionResult:
    r = CompetitionResult()
    r.schemas_deleted = schemas_deleted
    r.active_schemas = active_schemas
    r.avg_strength = avg_strength
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEngramSchemaProcessor:
    @pytest.mark.asyncio
    async def test_all_steps_called_in_order(self):
        """R1 → R2+R3 → R5 are each called exactly once."""
        proc = _make_processor()
        call_order: list[str] = []

        async def mock_clustering(*a, **kw):
            call_order.append("R1")
            return _cluster_result()

        async def mock_maturation(*a, **kw):
            call_order.append("R2+R3")
            return _mat_result()

        async def mock_competition(*a, **kw):
            call_order.append("R5")
            return _comp_result()

        with (
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_clustering", mock_clustering),
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_maturation", mock_maturation),
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_competition", mock_competition),
        ):
            await proc.process("bank-1", [])

        assert call_order == ["R1", "R2+R3", "R5"]

    @pytest.mark.asyncio
    async def test_result_fields_populated(self):
        """clusters_found, schemas_matured, schemas_created, schemas_deleted are set."""
        proc = _make_processor()

        with (
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_clustering",
                AsyncMock(return_value=_cluster_result(candidates_created=3)),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_maturation",
                AsyncMock(return_value=_mat_result(schemas_created=2, candidates_incremented=3)),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_competition",
                AsyncMock(return_value=_comp_result(schemas_deleted=1)),
            ),
        ):
            result = await proc.process("bank-1", [])

        assert result.clusters_found == 3
        assert result.schemas_matured == 3
        assert result.schemas_created == 2
        assert result.schemas_deleted == 1

    @pytest.mark.asyncio
    async def test_backward_compat_aliases(self):
        """result.created and result.deleted mirror the new fields."""
        proc = _make_processor()

        with (
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_clustering",
                AsyncMock(return_value=_cluster_result()),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_maturation",
                AsyncMock(return_value=_mat_result(schemas_created=2)),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_competition",
                AsyncMock(return_value=_comp_result(schemas_deleted=1)),
            ),
        ):
            result = await proc.process("bank-1", [])

        assert result.created == result.schemas_created == 2
        assert result.deleted == result.schemas_deleted == 1

    @pytest.mark.asyncio
    async def test_r1_failure_does_not_stop_r2r3_or_r5(self):
        """If R1 raises, R2+R3 and R5 still execute; result has 0 clusters_found."""
        proc = _make_processor()

        async def broken_clustering(*a, **kw):
            raise RuntimeError("Neo4j down")

        with (
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_clustering", broken_clustering),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_maturation",
                AsyncMock(return_value=_mat_result(schemas_created=1)),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_competition",
                AsyncMock(return_value=_comp_result(schemas_deleted=0)),
            ),
        ):
            result = await proc.process("bank-1", [])

        assert result.clusters_found == 0
        assert result.schemas_created == 1  # R2+R3 still ran

    @pytest.mark.asyncio
    async def test_r2r3_failure_does_not_stop_r5(self):
        """If R2+R3 raises, R5 still runs."""
        proc = _make_processor()

        with (
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_clustering",
                AsyncMock(return_value=_cluster_result()),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_maturation",
                AsyncMock(side_effect=RuntimeError("LLM timeout")),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_competition",
                AsyncMock(return_value=_comp_result(schemas_deleted=2)),
            ),
        ):
            result = await proc.process("bank-1", [])

        assert result.schemas_created == 0  # R2+R3 failed
        assert result.schemas_deleted == 2  # R5 still ran

    @pytest.mark.asyncio
    async def test_r5_failure_does_not_propagate(self):
        """If R5 raises, process() still returns without raising."""
        proc = _make_processor()

        with (
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_clustering",
                AsyncMock(return_value=_cluster_result()),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_maturation",
                AsyncMock(return_value=_mat_result()),
            ),
            patch(
                "hindsight_api.engine.consolidation.engram_schema_processor.run_competition",
                AsyncMock(side_effect=RuntimeError("Qdrant gone")),
            ),
        ):
            result = await proc.process("bank-1", [])  # must not raise

        assert isinstance(result, SchemaResult)
        assert result.schemas_deleted == 0

    @pytest.mark.asyncio
    async def test_all_failures_returns_empty_result(self):
        """All three steps fail → empty SchemaResult, no exception."""
        proc = _make_processor()

        async def boom(*a, **kw):
            raise RuntimeError("total meltdown")

        with (
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_clustering", boom),
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_maturation", boom),
            patch("hindsight_api.engine.consolidation.engram_schema_processor.run_competition", boom),
        ):
            result = await proc.process("bank-1", [])

        assert result.clusters_found == 0
        assert result.schemas_created == 0
        assert result.schemas_deleted == 0

    @pytest.mark.asyncio
    async def test_schema_result_new_fields_default_zero(self):
        """SchemaResult new fields all default to 0."""
        r = SchemaResult()
        assert r.clusters_found == 0
        assert r.schemas_matured == 0
        assert r.schemas_created == 0
        assert r.schemas_deleted == 0
        assert r.reinforcements == 0
