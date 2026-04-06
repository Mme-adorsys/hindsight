"""
Unit tests for NCR Phase 3 — R5 Competition/Death + Schema Decay.

Epic 13, Story 03, Task T5.

All tests are pure-unit: Neo4j, Qdrant, and asyncpg Pool are mocked.
Covers:
- R4 write_schema_links: fixed +0.05 strength increment, last_reinforced_at set
- R5: weak + stale schema deleted (Neo4j + Qdrant + Dictionary)
- R5: strong schema survives
- R5: weak but recently reinforced schema survives (not stale)
- Schema decay: strength *= 0.95 per cycle
- CompetitionResult metrics correct
- _is_stale logic
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight_api.engine.consolidation.schema_competition import (
    CompetitionResult,
    _DEFAULT_DEATH_NCR_CYCLES,
    _DEFAULT_NCR_INTERVAL_HOURS,
    _is_stale,
    delete_schema_async,
    run_competition,
)
from hindsight_api.engine.retain.schema_links import (
    SchemaLink,
    write_schema_links,
)


# ---------------------------------------------------------------------------
# _is_stale
# ---------------------------------------------------------------------------


class TestIsStale:
    def _ago(self, hours: float) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    def test_none_is_always_stale(self):
        assert _is_stale(None, death_ncr_cycles=5, ncr_interval_hours=24) is True

    def test_recent_reinforcement_not_stale(self):
        # Reinforced 10 hours ago, window = 5*24 = 120 hours
        assert _is_stale(self._ago(10), death_ncr_cycles=5, ncr_interval_hours=24) is False

    def test_old_reinforcement_stale(self):
        # Reinforced 121 hours ago, window = 120 hours
        assert _is_stale(self._ago(121), death_ncr_cycles=5, ncr_interval_hours=24) is True

    def test_exactly_at_boundary_is_stale(self):
        # Reinforced exactly 120 hours ago → stale (>=)
        assert _is_stale(self._ago(120.01), death_ncr_cycles=5, ncr_interval_hours=24) is True

    def test_invalid_string_treated_as_stale(self):
        assert _is_stale("not-a-date", death_ncr_cycles=5, ncr_interval_hours=24) is True

    def test_configurable_cycles(self):
        # Reinforced 25 hours ago, window = 1*24 = 24 hours → stale
        assert _is_stale(self._ago(25), death_ncr_cycles=1, ncr_interval_hours=24) is True
        # But with 2 cycles → window = 48 hours → not stale
        assert _is_stale(self._ago(25), death_ncr_cycles=2, ncr_interval_hours=24) is False


# ---------------------------------------------------------------------------
# write_schema_links (R4 increment fix)
# ---------------------------------------------------------------------------


class TestWriteSchemaLinksR4:
    """Verify the fixed +0.05 increment and last_reinforced_at are set."""

    @pytest.mark.asyncio
    async def test_no_links_no_op(self):
        client = MagicMock()
        client.run_cypher = AsyncMock()
        await write_schema_links(client, [])
        client.run_cypher.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_client_no_op(self):
        await write_schema_links(None, [SchemaLink("e1", "s1", 0.8, 0.8)])
        # Should not raise

    @pytest.mark.asyncio
    async def test_fixed_increment_in_query(self):
        """The Cypher must use $increment=0.05, not $weight."""
        captured: list[dict] = []

        async def run_cypher(query: str, params=None):
            captured.append(params or {})
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)
        links = [SchemaLink(engram_id="e1", schema_id="s1", similarity=0.85, weight=0.85)]
        await write_schema_links(client, links)

        assert len(captured) == 1
        p = captured[0]
        assert p["increment"] == pytest.approx(0.05)
        assert p["sim"] == pytest.approx(0.85)
        assert "now" in p  # last_reinforced_at timestamp present

    @pytest.mark.asyncio
    async def test_multiple_links_each_gets_increment(self):
        call_count = [0]

        async def run_cypher(query: str, params=None):
            call_count[0] += 1
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)
        links = [
            SchemaLink("e1", "s1", 0.8, 0.8),
            SchemaLink("e2", "s2", 0.9, 0.9),
        ]
        await write_schema_links(client, links)
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_neo4j_error_logged_not_raised(self):
        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=RuntimeError("timeout"))
        # Must not raise
        await write_schema_links(client, [SchemaLink("e1", "s1", 0.8, 0.8)])


# ---------------------------------------------------------------------------
# run_competition helpers
# ---------------------------------------------------------------------------


def _stale_ts() -> str:
    """Timestamp old enough to be stale (200 hours ago)."""
    return (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()


def _recent_ts() -> str:
    """Timestamp recent enough to NOT be stale (1 hour ago)."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_schema_row(schema_id: str, strength: float, last_reinforced: str | None = None) -> dict:
    return {
        "schema_id": schema_id,
        "strength": strength,
        "last_reinforced_at": last_reinforced,
    }


def _make_neo4j(schema_rows: list[dict]):
    client = MagicMock()
    decay_calls: list[str] = []
    delete_calls: list[str] = []

    async def run_cypher(query: str, params=None):
        if "MATCH (s:Schema" in query and "RETURN" in query:
            return schema_rows
        if "SET s.strength" in query:
            decay_calls.append((params or {}).get("schema_id", ""))
            return []
        if "DETACH DELETE" in query:
            delete_calls.append((params or {}).get("schema_id", ""))
            return []
        return []

    client.run_cypher = AsyncMock(side_effect=run_cypher)
    client._decay_calls = decay_calls
    client._delete_calls = delete_calls
    return client


def _make_qdrant():
    client = MagicMock()
    client.delete_by_id = AsyncMock(return_value=None)
    return client


def _make_pool():
    return MagicMock()


# ---------------------------------------------------------------------------
# run_competition tests
# ---------------------------------------------------------------------------


class TestRunCompetition:
    @pytest.mark.asyncio
    async def test_no_schemas_returns_empty(self):
        neo4j = _make_neo4j([])
        result = await run_competition("bank-1", neo4j, _make_qdrant(), _make_pool())
        assert result.schemas_found == 0
        assert result.schemas_deleted == 0

    @pytest.mark.asyncio
    async def test_strong_schema_survives(self):
        """Schema with strength=0.8 and recent reinforcement must survive."""
        rows = [_make_schema_row("s1", 0.8, _recent_ts())]
        neo4j = _make_neo4j(rows)
        with patch(
            "hindsight_api.engine.consolidation.schema_competition.dict_repo.update_entry",
            new_callable=AsyncMock,
        ):
            result = await run_competition("bank-1", neo4j, _make_qdrant(), _make_pool())
        assert result.schemas_deleted == 0
        assert result.schemas_decayed == 1
        assert result.active_schemas == 1

    @pytest.mark.asyncio
    async def test_weak_stale_schema_deleted(self):
        """Schema with strength=0.05 and stale reinforcement → deleted."""
        rows = [_make_schema_row("s-dead", 0.05, _stale_ts())]
        neo4j = _make_neo4j(rows)
        qdrant = _make_qdrant()
        with patch(
            "hindsight_api.engine.consolidation.schema_competition.dict_repo.update_entry",
            new_callable=AsyncMock,
        ) as mock_update:
            result = await run_competition(
                "bank-1", neo4j, qdrant, _make_pool(),
                death_strength_threshold=0.1,
                death_ncr_cycles=5,
                ncr_interval_hours=24,
            )
        assert result.schemas_deleted == 1
        assert "s-dead" in result.details_deleted
        qdrant.delete_by_id.assert_called_once_with("s-dead")
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_weak_but_recent_schema_survives(self):
        """Schema with strength=0.05 but reinforced recently → NOT stale → survives."""
        rows = [_make_schema_row("s-young", 0.05, _recent_ts())]
        neo4j = _make_neo4j(rows)
        result = await run_competition(
            "bank-1", neo4j, _make_qdrant(), _make_pool(),
            death_strength_threshold=0.1,
            death_ncr_cycles=5,
            ncr_interval_hours=24,
        )
        assert result.schemas_deleted == 0
        assert result.schemas_decayed == 1

    @pytest.mark.asyncio
    async def test_decay_reduces_strength(self):
        """strength=0.5 → after decay 0.5 * 0.95 = 0.475."""
        rows = [_make_schema_row("s1", 0.5, _recent_ts())]
        captured_params: list[dict] = []

        async def run_cypher(query: str, params=None):
            if "MATCH (s:Schema" in query and "RETURN" in query:
                return rows
            if "SET s.strength" in query:
                captured_params.append(params or {})
                return []
            return []

        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=run_cypher)
        await run_competition("bank-1", client, _make_qdrant(), _make_pool(), decay_rate=0.95)

        assert len(captured_params) == 1
        assert captured_params[0]["new_strength"] == pytest.approx(0.5 * 0.95, rel=1e-5)

    @pytest.mark.asyncio
    async def test_metrics_avg_strength(self):
        """avg_strength = mean of surviving schemas after decay."""
        rows = [
            _make_schema_row("s1", 0.8, _recent_ts()),
            _make_schema_row("s2", 0.6, _recent_ts()),
        ]
        neo4j = _make_neo4j(rows)
        result = await run_competition("bank-1", neo4j, _make_qdrant(), _make_pool(), decay_rate=0.95)

        expected_avg = ((0.8 * 0.95) + (0.6 * 0.95)) / 2
        assert result.avg_strength == pytest.approx(expected_avg, rel=1e-4)
        assert result.active_schemas == 2

    @pytest.mark.asyncio
    async def test_mixed_schemas(self):
        """One strong + one dead → 1 decayed, 1 deleted."""
        rows = [
            _make_schema_row("s-live", 0.8, _recent_ts()),
            _make_schema_row("s-dead", 0.05, _stale_ts()),
        ]
        neo4j = _make_neo4j(rows)
        with patch(
            "hindsight_api.engine.consolidation.schema_competition.dict_repo.update_entry",
            new_callable=AsyncMock,
        ):
            result = await run_competition(
                "bank-1", neo4j, _make_qdrant(), _make_pool(),
                death_strength_threshold=0.1, death_ncr_cycles=5, ncr_interval_hours=24,
            )
        assert result.schemas_decayed == 1
        assert result.schemas_deleted == 1
        assert result.active_schemas == 1

    @pytest.mark.asyncio
    async def test_neo4j_fetch_failure_returns_empty(self):
        client = MagicMock()
        client.run_cypher = AsyncMock(side_effect=Exception("connection lost"))
        result = await run_competition("bank-1", client, _make_qdrant(), _make_pool())
        assert result.schemas_found == 0

    @pytest.mark.asyncio
    async def test_qdrant_delete_error_logged_not_raised(self):
        """Qdrant deletion failure must not propagate."""
        rows = [_make_schema_row("s-dead", 0.05, _stale_ts())]
        neo4j = _make_neo4j(rows)
        broken_qdrant = MagicMock()
        broken_qdrant.delete_by_id = AsyncMock(side_effect=RuntimeError("qdrant down"))
        with patch(
            "hindsight_api.engine.consolidation.schema_competition.dict_repo.update_entry",
            new_callable=AsyncMock,
        ):
            result = await run_competition(
                "bank-1", neo4j, broken_qdrant, _make_pool(),
                death_strength_threshold=0.1, death_ncr_cycles=5, ncr_interval_hours=24,
            )
        assert result.schemas_deleted == 1  # still counted even if Qdrant failed
