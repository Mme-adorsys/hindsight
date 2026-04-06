"""
Schema-fit checking for retain pipeline (R5, Game of Life Rule R4 — incremental).

At Retain time, each new Engram is checked against existing Schema nodes in Neo4j.
If the embedding similarity exceeds the threshold, a SCHEMA link is created and the
Schema's strength is incremented.

Bio-mapping: Schema Emergence (concept.md ch. 13) — repeated Engrams that fit a
schema reinforce it incrementally.  New schemas are NOT created here (that is NCR
Phase 3 / Epic 13).  This module only strengthens and links to existing schemas.

Lightweight by design: the schema check must not add significant latency to Retain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# Minimum cosine similarity for an Engram to be considered a fit for a Schema
SCHEMA_FIT_THRESHOLD: float = 0.7


@dataclass
class SchemaLink:
    """A directed link from an Engram to a matching Schema node."""

    engram_id: str  # unit_id of the new Engram
    schema_id: str  # Neo4j node id of the matching Schema
    similarity: float
    weight: float


async def check_schema_fit_batch(
    neo4j_client,
    unit_ids: list[str],
    embeddings: list[list[float]],
    threshold: float = SCHEMA_FIT_THRESHOLD,
) -> list[SchemaLink]:
    """Check each new Engram against existing Schema nodes in Neo4j.

    For each Engram embedding, fetches all Schema nodes (nodes with label Schema
    or property type='schema') and computes cosine similarity.  Returns SchemaLink
    objects for all pairs that exceed *threshold*.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None → returns [] (no-op).
        unit_ids: IDs of the newly stored Engrams (same order as embeddings).
        embeddings: 384-dim embedding vectors for each Engram.
        threshold: Minimum cosine similarity to create a schema link (default 0.7).

    Returns:
        List of SchemaLink objects — may be empty if no schemas exist yet.
    """
    if neo4j_client is None or not unit_ids or not embeddings:
        return []

    # Fetch all existing Schema nodes with their embeddings from Neo4j
    try:
        schema_rows = await neo4j_client.run_cypher(
            "MATCH (s:Schema) WHERE s.embedding IS NOT NULL RETURN s.schema_id AS schema_id, s.embedding AS embedding",
            {},
        )
    except Exception as exc:
        logger.warning(f"Schema node fetch failed: {exc}")
        return []

    if not schema_rows:
        return []

    # Parse schema embeddings
    schema_ids: list[str] = []
    schema_embs: list[list[float]] = []
    for row in schema_rows:
        try:
            schema_ids.append(row["schema_id"])
            schema_embs.append(row["embedding"])
        except (KeyError, TypeError):
            continue

    if not schema_ids:
        return []

    schema_matrix = np.array(schema_embs, dtype=np.float32)
    # L2-normalize schema rows so dot product equals cosine similarity
    norms = np.linalg.norm(schema_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    schema_matrix = schema_matrix / norms

    schema_links: list[SchemaLink] = []
    for unit_id, emb in zip(unit_ids, embeddings):
        emb_arr = np.array(emb, dtype=np.float32)
        emb_norm = np.linalg.norm(emb_arr)
        if emb_norm > 0:
            emb_arr = emb_arr / emb_norm
        similarities = np.dot(schema_matrix, emb_arr)
        for i, sim in enumerate(similarities):
            if float(sim) >= threshold:
                schema_links.append(
                    SchemaLink(
                        engram_id=unit_id,
                        schema_id=schema_ids[i],
                        similarity=float(sim),
                        weight=float(sim),
                    )
                )

    return schema_links


async def write_schema_links(neo4j_client, schema_links: list[SchemaLink]) -> None:
    """Write schema links to Neo4j and increment Schema strength.

    Uses MERGE to be idempotent. Errors are logged but not raised.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None (no-op).
        schema_links: Links returned by check_schema_fit_batch.
    """
    if neo4j_client is None or not schema_links:
        return

    now_str = datetime.now(timezone.utc).isoformat()
    for sl in schema_links:
        try:
            await neo4j_client.run_cypher(
                "MATCH (e:Engram {engram_id: $engram_id}), (s:Schema {schema_id: $schema_id}) "
                "MERGE (e)-[r:SCHEMA]->(s) "
                "ON CREATE SET r.weight = $sim, r.source = 'retain', r.created_at = $now "
                "ON MATCH SET r.weight = $sim "
                "WITH s "
                "SET s.strength = coalesce(s.strength, 0.0) + $increment, "
                "    s.last_reinforced_at = $now",
                {
                    "engram_id": sl.engram_id,
                    "schema_id": sl.schema_id,
                    "sim": sl.similarity,
                    "increment": 0.05,
                    "now": now_str,
                },
            )
        except Exception as exc:
            logger.warning(f"Schema link write failed ({sl.engram_id} → {sl.schema_id}): {exc}")
