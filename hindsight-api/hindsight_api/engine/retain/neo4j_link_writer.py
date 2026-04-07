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
        "PREDICTION_ERROR",
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

    Groups links by relationship type and issues one UNWIND query per type,
    reducing N+1 queries to at most 8 (one per valid rel_type). Falls back to
    individual writes if the batch query fails.

    Uses MERGE to avoid duplicates on retry. Unknown relationship types are skipped
    with a warning. Errors are logged but never re-raised so that Neo4j failures do
    not block the PostgreSQL-committed retain pipeline.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None (no-op).
        links: List of LinkRecord objects to write.
    """
    if neo4j_client is None or not links:
        return

    # Group valid links by rel_type (Neo4j requires rel_type in query string, not as parameter)
    by_type: dict[str, list[LinkRecord]] = {}
    for link in links:
        rel_type = _to_neo4j_rel_type(link.rel_type)
        if rel_type not in _VALID_REL_TYPES:
            logger.warning("Skipping unknown Neo4j relationship type '%s'", rel_type)
            continue
        by_type.setdefault(rel_type, []).append(link)

    for rel_type, type_links in by_type.items():
        params_list = [
            {
                "from_id": lnk.from_id,
                "to_id": lnk.to_id,
                "weight": lnk.weight,
                "source": lnk.source,
                "created_at": lnk.created_at,
            }
            for lnk in type_links
        ]
        try:
            await neo4j_client.run_cypher(
                f"UNWIND $links AS link "
                f"MATCH (a:Engram {{engram_id: link.from_id}}), (b:Engram {{engram_id: link.to_id}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) "
                f"SET r.weight = link.weight, r.source = link.source, r.created_at = link.created_at",
                {"links": params_list},
            )
        except Exception:
            logger.warning(
                "Neo4j batch link write failed for %s (%d links), falling back to individual writes",
                rel_type,
                len(type_links),
                exc_info=True,
            )
            for lnk in type_links:
                try:
                    await neo4j_client.run_cypher(
                        f"MATCH (a:Engram {{engram_id: $from_id}}), (b:Engram {{engram_id: $to_id}}) "
                        f"MERGE (a)-[r:{rel_type}]->(b) "
                        f"SET r.weight = $weight, r.created_at = $created_at, r.source = $source",
                        {
                            "from_id": lnk.from_id,
                            "to_id": lnk.to_id,
                            "weight": lnk.weight,
                            "created_at": lnk.created_at,
                            "source": lnk.source,
                        },
                    )
                except Exception as exc:
                    logger.warning("Neo4j link write failed (%s -[%s]-> %s): %s", lnk.from_id, rel_type, lnk.to_id, exc)
