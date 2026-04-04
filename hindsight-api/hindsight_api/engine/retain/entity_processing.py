"""
Entity processing for retain pipeline.

Handles entity extraction, resolution, and link creation for stored facts.
Includes LLM-based disambiguation for ambiguous entity names (R4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from . import link_utils
from .types import EntityLink, ProcessedFact

logger = logging.getLogger(__name__)


@dataclass
class AmbiguousEntity:
    """An extracted entity name that matches more than one known entity in the bank."""

    name: str
    candidates: list[dict]  # [{"entity_id": ..., "canonical_name": ..., "type": ...}]


@dataclass
class ResolvedEntity:
    """Result of LLM disambiguation — maps an ambiguous name to a chosen candidate."""

    name: str
    chosen_candidate: dict  # single entry from AmbiguousEntity.candidates


def detect_ambiguous_entities(
    entity_names: list[str],
    known_entities: list[dict],
) -> list[AmbiguousEntity]:
    """Detect entity names that match more than one known entity (case-insensitive substring).

    A name is considered ambiguous when two or more entries in *known_entities* contain
    it as a case-insensitive substring of their ``canonical_name``.  For example, the
    extracted name ``"Apple"`` would match both ``"Apple Inc."`` and ``"Apple (fruit)"``.

    Args:
        entity_names: Raw entity names extracted from fact text.
        known_entities: All entities currently in the bank.
                        Each dict must have at least ``"canonical_name"``.

    Returns:
        List of AmbiguousEntity objects — only names with >= 2 candidates are included.
    """
    ambiguous: list[AmbiguousEntity] = []
    for name in entity_names:
        name_lower = name.lower()
        matches = [e for e in known_entities if name_lower in e.get("canonical_name", "").lower()]
        if len(matches) > 1:
            ambiguous.append(AmbiguousEntity(name=name, candidates=matches))
    return ambiguous


async def disambiguate_entities(
    ambiguous: list[AmbiguousEntity],
    fact_context: str,
    llm,
) -> list[ResolvedEntity]:
    """Use an LLM (Small-Tier) to pick the correct candidate for each ambiguous entity.

    All ambiguous entities are batched into a single LLM call to minimise latency.
    On any error (LLM failure, parse error, unknown candidate ID), the first candidate
    is used as a fallback so that the pipeline never blocks.

    Args:
        ambiguous: List of AmbiguousEntity objects produced by detect_ambiguous_entities.
        fact_context: The fact text providing context for disambiguation.
        llm: LLM instance (Small-Tier from LLMRegistry).

    Returns:
        List of ResolvedEntity — one per input AmbiguousEntity, in the same order.
    """
    if not ambiguous:
        return []

    # Build a compact prompt for all ambiguous entities in one call
    items = []
    for i, ae in enumerate(ambiguous):
        candidates_str = ", ".join(f"{j + 1}={c.get('canonical_name', '?')}" for j, c in enumerate(ae.candidates))
        items.append(f'{i + 1}. "{ae.name}" → options: {candidates_str}')

    prompt = (
        f"Context: {fact_context}\n\n"
        f"For each entity mention below, pick the number of the best-matching option.\n"
        f"Reply with ONLY the numbers separated by commas (e.g. '1,2,1').\n\n" + "\n".join(items)
    )

    resolved: list[ResolvedEntity] = []
    try:
        response = await llm.complete(prompt)
        # Parse comma-separated integers from the response
        parts = [p.strip() for p in response.split(",")]
        for ae, part in zip(ambiguous, parts):
            idx = int(part) - 1  # 1-based → 0-based
            chosen = ae.candidates[idx] if 0 <= idx < len(ae.candidates) else ae.candidates[0]
            resolved.append(ResolvedEntity(name=ae.name, chosen_candidate=chosen))
    except Exception as exc:
        logger.warning(f"Entity disambiguation failed ({exc}), using first candidate as fallback")
        for ae in ambiguous:
            resolved.append(ResolvedEntity(name=ae.name, chosen_candidate=ae.candidates[0]))

    # Ensure we always return one entry per input (safety net for partial parses)
    while len(resolved) < len(ambiguous):
        ae = ambiguous[len(resolved)]
        resolved.append(ResolvedEntity(name=ae.name, chosen_candidate=ae.candidates[0]))

    return resolved


async def _load_known_entities(conn, bank_id: str) -> list[dict]:
    """Fetch all known entities for a bank in a single query.

    Used by the disambiguation flow to find candidates for ambiguous names.
    """
    from ..memory_engine import fq_table

    rows = await conn.fetch(
        f"SELECT id::text AS entity_id, canonical_name FROM {fq_table('entities')} WHERE bank_id = $1",
        bank_id,
    )
    return [{"entity_id": r["entity_id"], "canonical_name": r["canonical_name"], "type": "CONCEPT"} for r in rows]


async def process_entities_batch(
    entity_resolver,
    conn,
    bank_id: str,
    unit_ids: list[str],
    facts: list[ProcessedFact],
    log_buffer: list[str] = None,
    user_entities_per_content: dict[int, list[dict]] = None,
    llm=None,
) -> list[EntityLink]:
    """
    Process entities for all facts and create entity links.

    This function:
    1. Extracts entity mentions from fact texts
    2. Merges user-provided entities with LLM-extracted entities
    3. (R4) Disambiguates ambiguous entity names via LLM when *llm* is provided
    4. Resolves entity names to canonical entities
    5. Creates entity records in the database
    6. Returns entity links ready for insertion

    Args:
        entity_resolver: EntityResolver instance for entity resolution
        conn: Database connection
        bank_id: Bank identifier
        unit_ids: List of unit IDs (same length as facts)
        facts: List of ProcessedFact objects
        log_buffer: Optional buffer for detailed logging
        user_entities_per_content: Dict mapping content_index to list of user-provided entities
        llm: Optional Small-Tier LLM for entity disambiguation (R4). When None, the
             disambiguation step is skipped (backward-compatible).

    Returns:
        List of EntityLink objects for batch insertion
    """
    if not unit_ids or not facts:
        return []

    if len(unit_ids) != len(facts):
        raise ValueError(f"Mismatch between unit_ids ({len(unit_ids)}) and facts ({len(facts)})")

    user_entities_per_content = user_entities_per_content or {}

    # Extract data for link_utils function
    fact_texts = [fact.fact_text for fact in facts]
    # Use occurred_start if available, otherwise use mentioned_at for entity timestamps
    fact_dates = [fact.occurred_start if fact.occurred_start is not None else fact.mentioned_at for fact in facts]

    # Convert EntityRef objects to dict format and merge with user-provided entities
    entities_per_fact = []
    for fact in facts:
        # Start with LLM-extracted entities
        llm_entities = [{"text": entity.name, "type": "CONCEPT"} for entity in (fact.entities or [])]

        # Get user entities for this content (use content_index from fact)
        user_entities = user_entities_per_content.get(fact.content_index, [])

        # Merge with case-insensitive deduplication
        seen_texts = {e["text"].lower() for e in llm_entities}
        for user_entity in user_entities:
            if user_entity["text"].lower() not in seen_texts:
                llm_entities.append(
                    {
                        "text": user_entity["text"],
                        "type": user_entity.get("type", "CONCEPT"),
                    }
                )
                seen_texts.add(user_entity["text"].lower())

        entities_per_fact.append(llm_entities)

    # R4: LLM-based disambiguation for ambiguous entity names
    if llm is not None:
        try:
            known_entities = await _load_known_entities(conn, bank_id)
            if known_entities:
                all_names = list({e["text"] for fact_entities in entities_per_fact for e in fact_entities})
                ambiguous = detect_ambiguous_entities(all_names, known_entities)
                if ambiguous:
                    combined_context = " | ".join(fact_texts[:3])
                    resolved = await disambiguate_entities(ambiguous, combined_context, llm)
                    replacements = {r.name: r.chosen_candidate.get("canonical_name", r.name) for r in resolved}
                    for fact_entities in entities_per_fact:
                        for entity in fact_entities:
                            if entity["text"] in replacements:
                                entity["text"] = replacements[entity["text"]]
                    if log_buffer is not None:
                        log_buffer.append(f"  Disambiguated {len(ambiguous)} ambiguous entities")
        except Exception as exc:
            logger.warning(f"Entity disambiguation step failed ({exc}), continuing without disambiguation")

    # Use existing link_utils function for entity processing
    entity_links = await link_utils.extract_entities_batch_optimized(
        entity_resolver,
        conn,
        bank_id,
        unit_ids,
        fact_texts,
        "",  # context (not used in current implementation)
        fact_dates,
        entities_per_fact,
        log_buffer,  # Pass log_buffer for detailed logging
    )

    return entity_links


async def create_entity_nodes_batch(neo4j_client, entities: list[dict]) -> None:
    """Create or merge Entity nodes in Neo4j (T4 — R4).

    Uses MERGE so repeated calls are safe (idempotent).
    Errors are logged but never re-raised — Neo4j is eventual-consistency relative
    to the PostgreSQL transaction (see T5 / story-04 acceptance criteria).

    Args:
        neo4j_client: Connected Neo4jEngineClient instance, or None (no-op).
        entities: List of dicts with keys ``entity_id``, ``canonical_name``, ``type``.
    """
    if not entities or neo4j_client is None:
        return

    for entity in entities:
        try:
            await neo4j_client.run_cypher(
                "MERGE (e:Entity {entity_id: $entity_id}) SET e.canonical_name = $canonical_name, e.type = $type",
                {
                    "entity_id": entity["entity_id"],
                    "canonical_name": entity.get("canonical_name", ""),
                    "type": entity.get("type", "CONCEPT"),
                },
            )
        except Exception as exc:
            logger.warning(f"Neo4j entity node creation failed for {entity.get('entity_id')}: {exc}")


async def create_entity_relationships_batch(neo4j_client, entity_links: list[EntityLink]) -> None:
    """Create ENTITY relationships between Engram nodes in Neo4j (T5 — R4).

    For each EntityLink that connects two Engrams through a shared entity,
    a directed ENTITY relationship is MERGE'd in Neo4j.  Errors are logged but
    never re-raised (eventual consistency — PostgreSQL is the source of truth).

    Args:
        neo4j_client: Connected Neo4jEngineClient instance, or None (no-op).
        entity_links: Entity link objects returned by process_entities_batch.
    """
    if not entity_links or neo4j_client is None:
        return

    for link in entity_links:
        try:
            await neo4j_client.run_cypher(
                "MATCH (a:Engram {engram_id: $from_id}), (b:Engram {engram_id: $to_id}) "
                "MERGE (a)-[r:ENTITY {entity_id: $entity_id}]->(b) "
                "SET r.weight = $weight",
                {
                    "from_id": str(link.from_unit_id),
                    "to_id": str(link.to_unit_id),
                    "entity_id": str(link.entity_id),
                    "weight": link.weight,
                },
            )
        except Exception as exc:
            logger.warning(
                f"Neo4j ENTITY relationship creation failed ({link.from_unit_id} → {link.to_unit_id}): {exc}"
            )


async def insert_entity_links_batch(conn, entity_links: list[EntityLink]) -> None:
    """
    Insert entity links in batch.

    Args:
        conn: Database connection
        entity_links: List of EntityLink objects
    """
    if not entity_links:
        return

    await link_utils.insert_entity_links_batch(conn, entity_links)
