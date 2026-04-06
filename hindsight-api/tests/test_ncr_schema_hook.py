"""
Unit tests for NCR Phase 3 — Schema Compression Hook.

Epic 12, Story 04, Task T4.

Tests cover:
- SchemaResult dataclass defaults and isolation
- NoOpSchemaProcessor: returns empty result, callable, accepts any engrams
- Interface contract: SchemaProcessor is abstract, NoOp is concrete
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hindsight_api.engine.consolidation.schema_processor import (
    NoOpSchemaProcessor,
    SchemaProcessor,
    SchemaResult,
)


# ---------------------------------------------------------------------------
# SchemaResult
# ---------------------------------------------------------------------------


class TestSchemaResult:
    def test_defaults(self):
        r = SchemaResult()
        assert r.created == 0
        assert r.strengthened == 0
        assert r.deleted == 0
        assert r.details == []

    def test_explicit_values(self):
        r = SchemaResult(created=3, strengthened=1, deleted=2, details=["x"])
        assert r.created == 3
        assert r.strengthened == 1
        assert r.deleted == 2
        assert r.details == ["x"]

    def test_details_not_shared(self):
        r1 = SchemaResult()
        r2 = SchemaResult()
        r1.details.append("foo")
        assert r2.details == []


# ---------------------------------------------------------------------------
# NoOpSchemaProcessor
# ---------------------------------------------------------------------------


class TestNoOpSchemaProcessor:
    @pytest.mark.asyncio
    async def test_returns_empty_result(self):
        proc = NoOpSchemaProcessor()
        result = await proc.process("test-bank", engrams=[])
        assert result.created == 0
        assert result.strengthened == 0
        assert result.deleted == 0
        assert result.details == []

    @pytest.mark.asyncio
    async def test_accepts_engram_list(self):
        proc = NoOpSchemaProcessor()
        fake_engrams = [MagicMock(), MagicMock()]
        result = await proc.process("test-bank", engrams=fake_engrams)
        assert isinstance(result, SchemaResult)

    @pytest.mark.asyncio
    async def test_returns_schema_result_type(self):
        proc = NoOpSchemaProcessor()
        result = await proc.process("bank-xyz", engrams=[])
        assert isinstance(result, SchemaResult)

    @pytest.mark.asyncio
    async def test_multiple_calls_independent(self):
        proc = NoOpSchemaProcessor()
        r1 = await proc.process("bank-a", engrams=[])
        r2 = await proc.process("bank-b", engrams=[])
        r1.details.append("x")
        assert r2.details == []


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


class TestSchemaProcessorInterface:
    def test_schema_processor_is_abstract(self):
        with pytest.raises(TypeError):
            SchemaProcessor()  # type: ignore[abstract]

    def test_noop_is_concrete_subclass(self):
        assert issubclass(NoOpSchemaProcessor, SchemaProcessor)
        proc = NoOpSchemaProcessor()
        assert isinstance(proc, SchemaProcessor)

    def test_custom_implementation_can_be_registered(self):
        class CustomProcessor(SchemaProcessor):
            async def process(self, bank_id, engrams):
                return SchemaResult(created=1)

        proc = CustomProcessor()
        assert isinstance(proc, SchemaProcessor)
