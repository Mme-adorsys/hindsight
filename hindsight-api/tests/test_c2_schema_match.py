"""Unit tests for Epic 25 Story 06 — C2 schema-fingerprint match.

Pure unit: Qdrant client and schema_lookup are mocked; the test asserts
the search-filter shape (kind=schema + bank_id), the cosine cutoff
behaviour, the bank-isolation contract, and the qdrant→neo4j drift fallback.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client.http import models as qdrant_models

from hindsight_api.engine.consolidation.c2_schema_match import match_existing_schema
from hindsight_api.engine.consolidation.constants import SCHEMA_MATCH_THRESHOLD


def _hit(*, schema_id: str, score: float, bank_id: str = "bank-A") -> dict:
    """Mimic the dict shape that QdrantEngineClient.search_similar returns."""
    return {
        "engram_id": schema_id,  # legacy fallback in payload['engram_id']
        "score": score,
        "payload": {"kind": "schema", "schema_id": schema_id, "bank_id": bank_id},
    }


def _qdrant(hits: list[dict]) -> AsyncMock:
    qdrant = AsyncMock()
    qdrant.search_similar = AsyncMock(return_value=hits)
    return qdrant


def _make_schema_lookup(schemas_by_id: dict[str, MagicMock]):
    async def _lookup(schema_id):
        return schemas_by_id.get(str(schema_id))

    return _lookup


# ---------------------------------------------------------------------------
# Threshold + drift guard
# ---------------------------------------------------------------------------


class TestSchemaMatchThreshold:
    def test_threshold_locked_to_concept(self):
        # concept §13 R4 fixes the cosine cutoff at 0.85.
        assert SCHEMA_MATCH_THRESHOLD == 0.85


# ---------------------------------------------------------------------------
# match_existing_schema
# ---------------------------------------------------------------------------


class TestMatchExistingSchema:
    async def test_above_threshold_returns_schema(self):
        sid = str(uuid.uuid4())
        schema_obj = MagicMock(name="schema_obj")
        qdrant = _qdrant([_hit(schema_id=sid, score=0.92)])
        lookup = _make_schema_lookup({sid: schema_obj})

        result, score = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A")

        assert result is schema_obj
        assert score == pytest.approx(0.92)

    async def test_below_threshold_returns_none_with_best_score(self):
        sid = str(uuid.uuid4())
        schema_obj = MagicMock()
        qdrant = _qdrant([_hit(schema_id=sid, score=0.70)])
        lookup = _make_schema_lookup({sid: schema_obj})

        result, score = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A")

        assert result is None
        assert score == pytest.approx(0.70)

    async def test_no_hits_returns_zero_best_score(self):
        qdrant = _qdrant([])
        lookup = _make_schema_lookup({})

        result, score = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A")

        assert result is None
        assert score == 0.0

    async def test_qdrant_search_uses_kind_schema_and_bank_filter(self):
        qdrant = _qdrant([])
        await match_existing_schema(qdrant, _make_schema_lookup({}), [0.1] * 4, "bank-A")

        kwargs = qdrant.search_similar.await_args.kwargs
        assert kwargs["kind"] == "schema"
        assert kwargs["limit"] == 1
        bank_filter = kwargs["filters"]
        assert "must" in bank_filter
        cond = bank_filter["must"][0]
        assert isinstance(cond, qdrant_models.FieldCondition)
        assert cond.key == "bank_id"
        assert cond.match.value == "bank-A"

    async def test_explicit_threshold_overrides_default(self):
        sid = str(uuid.uuid4())
        schema_obj = MagicMock()
        qdrant = _qdrant([_hit(schema_id=sid, score=0.88)])
        lookup = _make_schema_lookup({sid: schema_obj})

        # 0.88 ≥ 0.85 default → match. With threshold 0.95 → miss.
        result_default, _ = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A")
        result_strict, score_strict = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A", threshold=0.95)
        assert result_default is schema_obj
        assert result_strict is None
        assert score_strict == pytest.approx(0.88)

    async def test_qdrant_neo4j_drift_treated_as_miss(self):
        # Qdrant returns a schema id that no longer exists in Neo4j.
        sid = str(uuid.uuid4())
        qdrant = _qdrant([_hit(schema_id=sid, score=0.95)])
        lookup = _make_schema_lookup({})  # no schema mapped

        result, score = await match_existing_schema(qdrant, lookup, [0.1] * 4, "bank-A")
        assert result is None
        assert score == pytest.approx(0.95)

    async def test_payload_missing_schema_id_treated_as_miss(self):
        # Defensive: a kind=schema point without schema_id (write-side bug).
        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock(
            return_value=[
                {
                    "engram_id": None,
                    "score": 0.99,
                    "payload": {"kind": "schema"},  # schema_id missing
                }
            ]
        )
        result, score = await match_existing_schema(qdrant, _make_schema_lookup({}), [0.1] * 4, "bank-A")
        assert result is None
        assert score == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# Bank isolation — cross-bank schemas must not match
# ---------------------------------------------------------------------------


class TestBankIsolation:
    async def test_qdrant_filter_keeps_other_banks_out(self):
        # The match function relies on Qdrant honouring the bank filter; we
        # simulate that by returning an empty hit list when the filter
        # specifies a bank that doesn't host the schema.
        sid = str(uuid.uuid4())
        schema_obj = MagicMock()
        schemas = {sid: schema_obj}

        async def _bank_isolated_search(*, embedding, limit, filters, kind):
            # Inspect the filter and only return hits when bank_id == 'bank-A'
            bank_cond = next(
                c for c in filters["must"] if isinstance(c, qdrant_models.FieldCondition) and c.key == "bank_id"
            )
            if bank_cond.match.value == "bank-A":
                return [_hit(schema_id=sid, score=0.95, bank_id="bank-A")]
            return []

        qdrant = AsyncMock()
        qdrant.search_similar = AsyncMock(side_effect=_bank_isolated_search)

        # Bank A: schema is found.
        result_a, score_a = await match_existing_schema(qdrant, _make_schema_lookup(schemas), [0.0] * 4, "bank-A")
        assert result_a is schema_obj
        assert score_a == pytest.approx(0.95)

        # Bank B: bank-A's schema is invisible.
        result_b, score_b = await match_existing_schema(qdrant, _make_schema_lookup(schemas), [0.0] * 4, "bank-B")
        assert result_b is None
        assert score_b == 0.0
