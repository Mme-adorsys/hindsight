"""
Neo4j link writer for retain pipeline.

Writes all link types (semantic, temporal, entity, causal, temporal_proximity, schema)
as Neo4j Relationships after they have been committed to PostgreSQL.

Design principles:
- MERGE (not CREATE) — idempotent, safe for retries
- Errors are logged but never re-raised (Neo4j is eventual-consistency)
- PostgreSQL is source of truth; Neo4j is denormalized for graph traversal
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# All valid Neo4j relationship types (subset used during Retain)
_VALID_REL_TYPES = frozenset(
    {
        "SEMANTIC",
        "TEMPORAL",
        "ENTITY",
        "CAUSAL",
        "CO_ACTIVATED",
        "TEMPORAL_PROXIMITY",
        "SCHEMA",
        "CONTRADICTION",
    }
)


@dataclass
class LinkRecord:
    """Represents a single directed link to be written to Neo4j."""

    from_id: str  # engram_id of source node
    to_id: str  # engram_id of target node
    rel_type: str  # must be in _VALID_REL_TYPES
    weight: float = 1.0
    source: str = "retain"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _to_neo4j_rel_type(link_type: str) -> str:
    """Convert lowercase link_type (PostgreSQL) to uppercase Neo4j relationship type."""
    return link_type.upper().replace(" ", "_")


async def write_links_to_neo4j(neo4j_client, links: list[LinkRecord]) -> None:
    """Write a batch of LinkRecord objects as Neo4j Relationships.

    Uses MERGE to avoid duplicates on retry. Properties ``weight``, ``created_at``,
    and ``source`` are set on each relationship. Unknown relationship types are skipped
    with a warning.

    Errors per link are logged but never re-raised so that Neo4j failures do not
    block the PostgreSQL-committed retain pipeline.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None (no-op).
        links: List of LinkRecord objects to write.
    """
    if neo4j_client is None or not links:
        return

    for link in links:
        rel_type = _to_neo4j_rel_type(link.rel_type)
        if rel_type not in _VALID_REL_TYPES:
            logger.warning(f"Skipping unknown Neo4j relationship type '{rel_type}'")
            continue
        try:
            await neo4j_client.run_cypher(
                f"MATCH (a:Engram {{engram_id: $from_id}}), (b:Engram {{engram_id: $to_id}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) "
                f"SET r.weight = $weight, r.created_at = $created_at, r.source = $source",
                {
                    "from_id": link.from_id,
                    "to_id": link.to_id,
                    "weight": link.weight,
                    "created_at": link.created_at,
                    "source": link.source,
                },
            )
        except Exception as exc:
            logger.warning(f"Neo4j link write failed ({link.from_id} -[{rel_type}]-> {link.to_id}): {exc}")
