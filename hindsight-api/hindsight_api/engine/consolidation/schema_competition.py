"""
NCR Phase 3 — Schema Emergence, Rules R5 + Schema Decay.

R5 — Competition/Death:
    Weak Schemas that have not been reinforced (via R4 at Retain time) for
    more than `death_ncr_cycles` NCR cycles and whose strength falls below
    `death_strength_threshold` are deleted from Neo4j, Qdrant, and the
    engram_dictionary.  This prevents Schema inflation.

Schema Decay:
    All active Schema nodes lose `1 - decay_rate` of their strength each NCR
    cycle (default: keep 95 %).  Schemas are more stable than individual
    Engrams (Engram decay = 0.90) because they represent consolidated patterns.
    R4 Reinforcement at Retain time counteracts this decay.

Biological mapping:
  REM Sleep — synaptic homeostasis, weak structural patterns are pruned.
  Competition: Only schema patterns with sustained activation survive long-term.
  Ref: concept.md ch. 13 — Schema Emergence, R5 Competition/Death

Epic 13, Story 03.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo

if TYPE_CHECKING:
    from hindsight_api.engine.neo4j_client import Neo4jEngineClient
    from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_SCHEMA_DECAY_RATE: float = 0.95  # Schemas lose 5% per NCR cycle
_DEFAULT_DEATH_STRENGTH: float = 0.1  # Below this → candidate for deletion
_DEFAULT_DEATH_NCR_CYCLES: int = 5  # Must be unreinforced for this many NCR intervals
_DEFAULT_NCR_INTERVAL_HOURS: float = 24.0  # Used to compute staleness window

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

_FETCH_ALL_SCHEMAS_CYPHER = """
MATCH (s:Schema {bank_id: $bank_id})
RETURN s.schema_id         AS schema_id,
       s.strength           AS strength,
       s.last_reinforced_at AS last_reinforced_at
"""

_DECAY_SCHEMA_CYPHER = """
MATCH (s:Schema {schema_id: $schema_id, bank_id: $bank_id})
SET s.strength   = $new_strength,
    s.updated_at = $now
"""

_DELETE_SCHEMA_CYPHER = """
MATCH (s:Schema {schema_id: $schema_id, bank_id: $bank_id})
DETACH DELETE s
"""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CompetitionResult:
    """Summary of one R5 + Schema-Decay run."""

    schemas_found: int = 0
    schemas_decayed: int = 0
    schemas_deleted: int = 0
    active_schemas: int = 0
    avg_strength: float = 0.0
    details_deleted: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_stale(last_reinforced_at_str: str | None, death_ncr_cycles: int, ncr_interval_hours: float) -> bool:
    """Return True if the schema has not been reinforced within the death window."""
    if last_reinforced_at_str is None:
        return True
    try:
        last = datetime.fromisoformat(last_reinforced_at_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    elapsed_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    threshold_hours = death_ncr_cycles * ncr_interval_hours
    return elapsed_hours >= threshold_hours


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_competition(
    bank_id: str,
    neo4j_client: "Neo4jEngineClient",
    qdrant_client: "QdrantEngineClient",
    pool: asyncpg.Pool,
    *,
    decay_rate: float = _DEFAULT_SCHEMA_DECAY_RATE,
    death_strength_threshold: float = _DEFAULT_DEATH_STRENGTH,
    death_ncr_cycles: int = _DEFAULT_DEATH_NCR_CYCLES,
    ncr_interval_hours: float = _DEFAULT_NCR_INTERVAL_HOURS,
) -> CompetitionResult:
    """
    Execute R5 + Schema Decay for *bank_id*.

    1. Load all Schema nodes for this bank.
    2. Apply decay: strength *= decay_rate.
    3. Check death condition: strength < threshold AND last_reinforced_at stale.
       If dead → delete from Neo4j (DETACH DELETE), Qdrant, Dictionary.
       Otherwise → write decayed strength back.
    4. Compute and return metrics.

    Args:
        bank_id: Memory bank to process.
        neo4j_client: Connected Neo4j client.
        qdrant_client: Connected Qdrant client.
        pool: asyncpg Pool for Dictionary updates.
        decay_rate: Fraction of strength retained per cycle (default 0.95).
        death_strength_threshold: Below this strength schemas are mortal (default 0.1).
        death_ncr_cycles: Staleness window in NCR cycles (default 5).
        ncr_interval_hours: Hours per NCR cycle for staleness computation (default 24).

    Returns:
        CompetitionResult with metrics.
    """
    result = CompetitionResult()
    now_str = datetime.now(timezone.utc).isoformat()

    # ── Step 1: Load all Schema nodes ────────────────────────────────────────
    try:
        schema_rows = await neo4j_client.run_cypher(_FETCH_ALL_SCHEMAS_CYPHER, {"bank_id": bank_id})
    except Exception as exc:
        logger.error(f"[R5] Schema fetch failed for bank {bank_id}: {exc}")
        return result

    result.schemas_found = len(schema_rows)
    if not schema_rows:
        logger.debug(f"[R5] No Schema nodes for bank {bank_id}")
        return result

    surviving_strengths: list[float] = []

    for row in schema_rows:
        schema_id: str = row["schema_id"]
        strength: float = float(row.get("strength") or 0.0)
        last_reinforced: str | None = row.get("last_reinforced_at")

        # ── Step 2: Decay ────────────────────────────────────────────────────
        new_strength = strength * decay_rate

        # ── Step 3: Death check ──────────────────────────────────────────────
        stale = _is_stale(last_reinforced, death_ncr_cycles, ncr_interval_hours)
        if new_strength < death_strength_threshold and stale:
            await delete_schema_async(schema_id, bank_id, neo4j_client, qdrant_client, pool)
            result.schemas_deleted += 1
            result.details_deleted.append(schema_id)
            continue

        # ── Survive: write back decayed strength ────────────────────────────
        try:
            await neo4j_client.run_cypher(
                _DECAY_SCHEMA_CYPHER,
                {"schema_id": schema_id, "bank_id": bank_id, "new_strength": round(new_strength, 6), "now": now_str},
            )
            result.schemas_decayed += 1
        except Exception as exc:
            logger.warning(f"[R5] Decay write failed for schema {schema_id}: {exc}")

        surviving_strengths.append(new_strength)

    # ── Step 4: Metrics ──────────────────────────────────────────────────────
    result.active_schemas = len(surviving_strengths)
    result.avg_strength = sum(surviving_strengths) / len(surviving_strengths) if surviving_strengths else 0.0

    logger.info(
        f"[R5] bank={bank_id} found={result.schemas_found} decayed={result.schemas_decayed} "
        f"deleted={result.schemas_deleted} active={result.active_schemas} avg_strength={result.avg_strength:.3f}"
    )
    return result


async def delete_schema_async(
    schema_id: str,
    bank_id: str,
    neo4j_client: "Neo4jEngineClient",
    qdrant_client: "QdrantEngineClient",
    pool: asyncpg.Pool,
) -> None:
    """Delete a Schema from Neo4j, Qdrant, and Dictionary (async, errors logged)."""
    # Neo4j
    try:
        await neo4j_client.run_cypher(_DELETE_SCHEMA_CYPHER, {"schema_id": schema_id, "bank_id": bank_id})
    except Exception as exc:
        logger.warning(f"[R5] Neo4j delete failed for schema {schema_id}: {exc}")

    # Qdrant
    try:
        await qdrant_client.delete_by_id(schema_id)
    except Exception as exc:
        logger.warning(f"[R5] Qdrant delete failed for schema {schema_id}: {exc}")

    # Dictionary (mark as archived)
    try:
        await dict_repo.update_entry(pool, schema_id, {"status": "archived"})
    except Exception as exc:
        logger.warning(f"[R5] Dictionary update failed for schema {schema_id}: {exc}")
