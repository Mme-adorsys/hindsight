"""
Link creation for retain pipeline.

Handles creation of temporal, semantic, causal, temporal_proximity,
and co_activated links between facts.
"""

import logging
from datetime import timedelta

from ..memory_engine import fq_table
from . import link_utils
from .types import ProcessedFact

logger = logging.getLogger(__name__)


async def create_temporal_links_batch(conn, bank_id: str, unit_ids: list[str]) -> int:
    """
    Create temporal links between facts.

    Links facts that occurred close in time to each other.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        unit_ids: List of unit IDs to create links for

    Returns:
        Number of temporal links created
    """
    if not unit_ids:
        return 0

    return await link_utils.create_temporal_links_batch_per_fact(conn, bank_id, unit_ids, log_buffer=[])


async def create_semantic_links_batch(conn, bank_id: str, unit_ids: list[str], embeddings: list[list[float]]) -> int:
    """
    Create semantic links between facts.

    Links facts that are semantically similar based on embeddings.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        unit_ids: List of unit IDs to create links for
        embeddings: List of embedding vectors (same length as unit_ids)

    Returns:
        Number of semantic links created
    """
    if not unit_ids or not embeddings:
        return 0

    if len(unit_ids) != len(embeddings):
        raise ValueError(f"Mismatch between unit_ids ({len(unit_ids)}) and embeddings ({len(embeddings)})")

    return await link_utils.create_semantic_links_batch(conn, bank_id, unit_ids, embeddings, log_buffer=[])


async def create_causal_links_batch(conn, unit_ids: list[str], facts: list[ProcessedFact]) -> int:
    """
    Create causal links between facts.

    Links facts that have causal relationships (causes, enables, prevents).

    Args:
        conn: Database connection
        unit_ids: List of unit IDs (same length as facts)
        facts: List of ProcessedFact objects with causal_relations

    Returns:
        Number of causal links created
    """
    if not unit_ids or not facts:
        return 0

    if len(unit_ids) != len(facts):
        raise ValueError(f"Mismatch between unit_ids ({len(unit_ids)}) and facts ({len(facts)})")

    # Extract causal relations in the format expected by link_utils
    # Format: List of lists, where each inner list is the causal relations for that fact
    causal_relations_per_fact = []
    for fact in facts:
        if fact.causal_relations:
            # Convert CausalRelation objects to dicts
            relations_dicts = [
                {
                    "relation_type": rel.relation_type,
                    "target_fact_index": rel.target_fact_index,
                    "strength": rel.strength,
                }
                for rel in fact.causal_relations
            ]
            causal_relations_per_fact.append(relations_dicts)
        else:
            causal_relations_per_fact.append([])

    link_count = await link_utils.create_causal_links_batch(conn, unit_ids, causal_relations_per_fact)

    return link_count


async def create_temporal_proximity_links_batch(
    conn,
    bank_id: str,
    unit_ids: list[str],
    session_id=None,  # reserved: will filter by session when memory_units stores session_id (Epic 06+)
    time_window_minutes: int = 30,
) -> int:
    """Create temporal_proximity links for Engrams stored within the same session time window.

    Bio-mapping: Synaptic Tagging & Capture (Frey & Morris 1997) — memories formed within a
    short time window receive a "tag" that enables later association, even without shared recall.
    Shorter window (30 min default) than temporal links (24h) to capture intra-session proximity.

    Weight formula: ``max(0.1, 1.0 - time_diff_minutes / time_window_minutes)``.

    Args:
        conn: Database connection.
        bank_id: Bank identifier.
        unit_ids: Newly stored unit IDs.
        session_id: Reserved for future use. When ``memory_units`` stores session_id
                    (Epic 06+), this will additionally restrict matches to the same session.
        time_window_minutes: Half-window in minutes (default 30).

    Returns:
        Number of temporal_proximity links created.
    """
    if not unit_ids:
        return 0

    # Fetch mentioned_at for the new units (session-time proxy until session_id is on memory_units)
    rows = await conn.fetch(
        f"SELECT id, mentioned_at FROM {fq_table('memory_units')} WHERE id::text = ANY($1)",
        unit_ids,
    )
    new_units = {str(row["id"]): row["mentioned_at"] for row in rows if row["mentioned_at"] is not None}
    if not new_units:
        return 0

    # Single query for all candidates within the time window
    min_dt = min(new_units.values()) - timedelta(minutes=time_window_minutes)
    max_dt = max(new_units.values()) + timedelta(minutes=time_window_minutes)

    candidates = await conn.fetch(
        f"""
        SELECT id, mentioned_at FROM {fq_table("memory_units")}
        WHERE bank_id = $1
          AND mentioned_at BETWEEN $2 AND $3
          AND id::text != ALL($4)
          AND mentioned_at IS NOT NULL
        ORDER BY mentioned_at DESC
        """,
        bank_id,
        min_dt,
        max_dt,
        unit_ids,
    )

    links: list[tuple] = []

    # New units ↔ existing candidates
    for unit_id, unit_time in new_units.items():
        for cand in candidates:
            diff_min = abs((unit_time - cand["mentioned_at"]).total_seconds() / 60)
            if diff_min <= time_window_minutes:
                weight = max(0.0, 1.0 - diff_min / time_window_minutes)
                if weight <= 0.0:
                    continue
                cand_id = str(cand["id"])
                links.append((unit_id, cand_id, "temporal_proximity", weight, None))
                links.append((cand_id, unit_id, "temporal_proximity", weight, None))

    # Within-batch links
    items = list(new_units.items())
    for i, (uid, utime) in enumerate(items):
        for j in range(i + 1, len(items)):
            oid, otime = items[j]
            diff_min = abs((utime - otime).total_seconds() / 60)
            if diff_min <= time_window_minutes:
                weight = max(0.0, 1.0 - diff_min / time_window_minutes)
                if weight <= 0.0:
                    continue
                links.append((uid, oid, "temporal_proximity", weight, None))
                links.append((oid, uid, "temporal_proximity", weight, None))

    if links:
        # Deduplicate in Python before insert
        links = list(set(links))
        await conn.executemany(
            f"""
            INSERT INTO {fq_table("memory_links")} (from_unit_id, to_unit_id, link_type, weight, entity_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (from_unit_id, to_unit_id, link_type,
                         COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid))
            DO NOTHING
            """,
            links,
        )

    return len(links)


async def create_association_window_link(neo4j_client, from_id: str, to_id: str, weight: float = 0.1) -> None:
    """Create or strengthen a TEMPORAL_PROXIMITY relationship between two Engram nodes in Neo4j.

    Called by AssociationWindow.flush_to_neo4j() when STC-associated Engram pairs
    are written after the session's association window check.

    Uses MERGE + min() cap so the link remains weak (max 0.5).

    Bio-mapping: Synaptic Tagging & Capture — memories formed / activated within a short
    time window receive a tag that allows later capture into a weak association.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None (no-op).
        from_id: engram_id of the source Engram.
        to_id: engram_id of the target Engram.
        weight: Link weight to set/merge (capped at 0.5 inside Cypher).
    """
    if neo4j_client is None:
        return
    try:
        await neo4j_client.run_cypher(
            "MATCH (a:Engram {engram_id: $from_id}), (b:Engram {engram_id: $to_id}) "
            "MERGE (a)-[r:TEMPORAL_PROXIMITY]->(b) "
            "SET r.weight = min(coalesce(r.weight, 0.0) + $weight, 0.5), "
            "    r.time_delta = 0",
            {"from_id": from_id, "to_id": to_id, "weight": weight},
        )
    except Exception as exc:
        logger.warning(f"Neo4j TEMPORAL_PROXIMITY link creation failed ({from_id} → {to_id}): {exc}")


async def create_co_activation_link(neo4j_client, from_id: str, to_id: str, weight: float = 1.0) -> None:
    """Create a CO_ACTIVATED relationship between two Engram nodes in Neo4j.

    Interface stub — actual activation tracking happens at Recall time (Epic 07/09).
    Called here to provide the interface for future callers without changing signatures.

    Bio-mapping: Hebbian learning — "neurons that fire together wire together." Co-activation
    links form only after repeated shared retrieval, not from a single event.

    Args:
        neo4j_client: Connected Neo4jEngineClient, or None (no-op).
        from_id: engram_id of the source Engram.
        to_id: engram_id of the target Engram.
        weight: Link strength (default 1.0, incremented by Recall pipeline).
    """
    if neo4j_client is None:
        return
    try:
        await neo4j_client.run_cypher(
            "MATCH (a:Engram {engram_id: $from_id}), (b:Engram {engram_id: $to_id}) "
            "MERGE (a)-[r:CO_ACTIVATED]->(b) "
            "SET r.weight = coalesce(r.weight, 0) + $weight, r.activation_count = coalesce(r.activation_count, 0) + 1",
            {"from_id": from_id, "to_id": to_id, "weight": weight},
        )
    except Exception as exc:
        logger.warning(f"Neo4j CO_ACTIVATED link creation failed ({from_id} → {to_id}): {exc}")
