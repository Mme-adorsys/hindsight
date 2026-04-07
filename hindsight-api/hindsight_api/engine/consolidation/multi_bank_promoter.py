"""
Multi-Bank Promoter — Epic 14 Story 03 (B3 + B5).

Promotes strong Agent-Dictionary Engrams into the Shared Memory Bank.

Promotion pipeline per candidate:
  1. Novelty Check (B3)  — Qdrant similarity against Shared Bank (≥ 0.85?)
     NOVEL      → promote_to_shared()  (new Engram in Shared Bank)
     REDUNDANT  → reinforce_shared()   (strengthen existing Shared Engram)

  2. Three trigger types (B5):
     NCR-based          — neocortex layer + strength ≥ threshold (main channel)
     Cross-Agent Conv.  — ≥ 2 agents have similar Engrams → threshold lowered to 0.4
     Schema-Trigger     — Engram has SCHEMA relationship → auto-candidate

Bio mapping:
  Hippocampus → Neocortex consolidation.
  "Sleep replay" (NCR) promotes frequently activated memories to long-term
  cortical storage (Shared Bank). Schema-linked memories are promoted faster
  because they encode generalised knowledge — the neocortex's speciality.

Concept: Ch. 15 B3 + B5.
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import asyncpg

from hindsight_api.engine import engram_dictionary as dict_repo

logger = logging.getLogger(__name__)

# Threshold for novelty / conflict detection
NOVELTY_SIMILARITY_THRESHOLD = 0.85

# Initial strength when an Engram first enters the Shared Bank
SHARED_INITIAL_STRENGTH = 0.3

# Strength delta on reinforce
REINFORCE_DELTA = 0.05

# Cross-Agent Convergence: if ≥ this many agents have similar Engrams,
# lower the strength threshold for promotion
CROSS_AGENT_MIN_COUNT = 2
CROSS_AGENT_THRESHOLD = 0.4  # lowered from the default 0.6


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class NoveltyResult(str, Enum):
    """Outcome of the novelty check against the Shared Bank."""

    NOVEL = "novel"  # No similar Engram → promote
    REDUNDANT = "redundant"  # Similar Engram exists → reinforce


@dataclass
class NoveltyCheckResult:
    """Extended novelty result carrying the existing Shared Engram ID when REDUNDANT."""

    result: NoveltyResult
    existing_engram_id: str | None = None  # Set when REDUNDANT
    existing_strength: float = 0.0


@dataclass
class PromotionResult:
    """Aggregate result of a promote_batch() run."""

    promoted: int = 0  # New Engrams created in Shared Bank
    reinforced: int = 0  # Existing Shared Engrams strengthened
    skipped: int = 0  # Candidates below threshold (after convergence adjustment)
    schema_triggered: int = 0  # Candidates found via Schema-Trigger
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# T1 — Find Promotion Candidates
# ---------------------------------------------------------------------------


async def find_promotion_candidates(
    pool: asyncpg.Pool,
    bank_id: str,
    strength_threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """
    Return neocortex Engrams eligible for Shared Bank promotion.

    Combines two sources:
    1. NCR-channel: neocortex + strength ≥ threshold.
    2. Schema-trigger: any Engram with a SCHEMA Neo4j relationship,
       regardless of strength (handled separately in promote_batch).

    This function returns the NCR-channel candidates only.
    Schema-triggered candidates are identified in promote_batch via
    find_schema_triggered_candidates().

    Args:
        pool:               asyncpg connection pool.
        bank_id:            Agent Dictionary bank ID to scan.
        strength_threshold: Minimum strength for NCR-channel promotion.

    Returns:
        List of engram_dictionary row dicts, ordered by strength DESC.
    """
    return await dict_repo.filter_entries(
        pool,
        bank_id=bank_id,
        strength_min=strength_threshold,
        layer="neocortex",
        status="active",
        limit=1000,
    )


# ---------------------------------------------------------------------------
# T6 — Schema-Trigger
# ---------------------------------------------------------------------------


async def find_schema_triggered_candidates(
    pool: asyncpg.Pool,
    neo4j_client,
    bank_id: str,
) -> list[dict[str, Any]]:
    """
    Return Engrams that are part of a Schema cluster (SCHEMA relationship in Neo4j).

    These are auto-promoted regardless of their strength — schema membership
    indicates generalised knowledge that benefits cross-agent sharing.

    Args:
        pool:          asyncpg connection pool.
        neo4j_client:  Neo4jEngineClient instance.
        bank_id:       Agent Dictionary bank ID.

    Returns:
        List of engram_dictionary row dicts for schema-linked Engrams.
    """
    # Query Neo4j for engram_ids that have a SCHEMA relationship
    try:
        records = await neo4j_client.run_cypher(
            "MATCH (e:Engram)-[:SCHEMA]->(s:Engram) "
            "WHERE e.bank_id = $bank_id "
            "RETURN DISTINCT e.engram_id AS engram_id",
            params={"bank_id": bank_id},
        )
    except Exception as exc:
        logger.warning("find_schema_triggered_candidates: Neo4j query failed: %s", exc)
        return []

    if not records:
        return []

    schema_ids = {str(r["engram_id"]) for r in records if r.get("engram_id")}
    if not schema_ids:
        return []

    # Fetch metadata from PostgreSQL for those engram_ids
    try:
        all_entries = await dict_repo.filter_entries(pool, bank_id=bank_id, status="active", limit=10000)
        return [e for e in all_entries if str(e["engram_id"]) in schema_ids]
    except Exception as exc:
        logger.warning("find_schema_triggered_candidates: dict lookup failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# T2 — Novelty Check
# ---------------------------------------------------------------------------


async def check_novelty(
    qdrant_client,
    candidate_embedding: list[float],
    shared_bank_id: str,
    threshold: float = NOVELTY_SIMILARITY_THRESHOLD,
) -> NoveltyCheckResult:
    """
    Check whether a semantically similar Engram already exists in the Shared Bank.

    Args:
        qdrant_client:       QdrantEngineClient instance.
        candidate_embedding: Embedding of the candidate Engram.
        shared_bank_id:      Shared Bank ID used as Qdrant filter.
        threshold:           Minimum cosine similarity (default 0.85).

    Returns:
        NoveltyCheckResult — NOVEL (no match) or REDUNDANT (match found).
    """
    try:
        hits = await qdrant_client.search_similar(
            embedding=candidate_embedding,
            limit=3,
            filters={"bank_id": shared_bank_id},
        )
    except Exception as exc:
        logger.warning("check_novelty: Qdrant search failed: %s", exc)
        return NoveltyCheckResult(result=NoveltyResult.NOVEL)

    for hit in hits:
        score = hit.get("score", 0.0)
        if score >= threshold:
            payload = hit.get("payload", {})
            return NoveltyCheckResult(
                result=NoveltyResult.REDUNDANT,
                existing_engram_id=str(hit.get("engram_id", "")),
                existing_strength=float(payload.get("strength", 0.0)),
            )

    return NoveltyCheckResult(result=NoveltyResult.NOVEL)


# ---------------------------------------------------------------------------
# T5 — Cross-Agent Convergence
# ---------------------------------------------------------------------------


async def count_agents_with_similar(
    qdrant_client,
    candidate_embedding: list[float],
    agent_bank_ids: list[str],
    threshold: float = NOVELTY_SIMILARITY_THRESHOLD,
) -> int:
    """
    Count how many distinct agents have an Engram similar to the candidate.

    Used for Cross-Agent Convergence (B5): if ≥ 2 agents share similar
    Engrams, the promotion threshold is lowered to 0.4, accelerating
    cross-agent knowledge sharing.

    Args:
        qdrant_client:       QdrantEngineClient instance.
        candidate_embedding: Embedding of the candidate.
        agent_bank_ids:      All known agent dictionary bank IDs to check.
        threshold:           Similarity threshold (default 0.85).

    Returns:
        Number of agents that have a similar Engram.
    """
    if not agent_bank_ids:
        return 0

    matching_agents = 0
    for agent_bank_id in agent_bank_ids:
        try:
            hits = await qdrant_client.search_similar(
                embedding=candidate_embedding,
                limit=1,
                filters={"bank_id": agent_bank_id},
            )
            if hits and hits[0].get("score", 0.0) >= threshold:
                matching_agents += 1
        except Exception as exc:
            logger.warning("count_agents_with_similar: Qdrant error for bank=%s: %s", agent_bank_id, exc)

    return matching_agents


# ---------------------------------------------------------------------------
# T3 — Promote to Shared Bank
# ---------------------------------------------------------------------------


async def promote_to_shared(
    pool: asyncpg.Pool,
    qdrant_client,
    neo4j_client,
    candidate: dict[str, Any],
    shared_bank_id: str,
) -> str:
    """
    Write a new Engram into the Shared Bank (Dict + Qdrant + Neo4j link).

    A new engram_id is minted for the Shared copy. A CROSS_BANK link is
    created in Neo4j from the Shared copy → original Agent Engram.

    Args:
        pool:           asyncpg connection pool.
        qdrant_client:  QdrantEngineClient instance.
        neo4j_client:   Neo4jEngineClient instance.
        candidate:      engram_dictionary row dict from the Agent Bank.
        shared_bank_id: Target Shared Bank ID.

    Returns:
        The new Shared engram_id (str).
    """
    shared_engram_id = str(_uuid_mod.uuid4())

    # 1. Insert into PostgreSQL engram_dictionary
    shared_entry = {
        "engram_id": shared_engram_id,
        "bank_id": shared_bank_id,
        "layer": "neocortex",
        "strength": SHARED_INITIAL_STRENGTH,
        "status": "active",
        "abstraction_level": candidate.get("abstraction_level", 0.0),
        "tags": candidate.get("tags", []),
        "novelty": candidate.get("novelty"),
        "surprise": candidate.get("surprise"),
        "task_relevance": candidate.get("task_relevance"),
        "emotional_valence": candidate.get("emotional_valence"),
        "thalamus_overall": candidate.get("thalamus_overall"),
        "confidence_score": candidate.get("confidence_score"),
    }
    await dict_repo.insert_entry(pool, shared_entry)

    # 2. Upsert into Qdrant (re-use embedding from candidate's payload if available)
    embedding = candidate.get("embedding") or []
    if embedding:
        try:
            payload = {
                "bank_id": shared_bank_id,
                "text": candidate.get("text", ""),
                "strength": SHARED_INITIAL_STRENGTH,
                "thalamus_overall": candidate.get("thalamus_overall", 0.0),
                "layer": "neocortex",
            }
            await qdrant_client.upsert_point(
                engram_id=shared_engram_id,
                embedding=embedding,
                payload=payload,
            )
        except Exception as exc:
            logger.warning("promote_to_shared: Qdrant upsert failed for %s: %s", shared_engram_id, exc)

    # 3. Create CROSS_BANK relationship in Neo4j (Shared → Agent original)
    original_id = str(candidate.get("engram_id", ""))
    if original_id:
        try:
            await neo4j_client.create_relationship(
                from_id=shared_engram_id,
                to_id=original_id,
                rel_type="CROSS_BANK",
                properties={"source_bank": candidate.get("bank_id", ""), "promoted": True},
            )
        except Exception as exc:
            logger.warning("promote_to_shared: Neo4j CROSS_BANK link failed: %s", exc)

    logger.info(
        "promote_to_shared: promoted engram=%s → shared=%s (bank=%s)",
        original_id,
        shared_engram_id,
        shared_bank_id,
    )
    return shared_engram_id


# ---------------------------------------------------------------------------
# T4 — Reinforce Shared Engram
# ---------------------------------------------------------------------------


async def reinforce_shared(
    pool: asyncpg.Pool,
    existing_engram_id: str,
    existing_strength: float,
) -> None:
    """
    Strengthen an existing Shared Bank Engram when a similar Agent Engram
    would have been promoted (REDUNDANT novelty result).

    strength += REINFORCE_DELTA (capped at 1.0).
    access_count += 1.

    Args:
        pool:               asyncpg connection pool.
        existing_engram_id: Shared Bank Engram ID to reinforce.
        existing_strength:  Current strength of the Shared Engram.
    """
    new_strength = min(1.0, existing_strength + REINFORCE_DELTA)
    try:
        await dict_repo.update_strength(pool, existing_engram_id, new_strength)
        await dict_repo.update_access(pool, existing_engram_id)
        logger.debug("reinforce_shared: %s strength %.3f → %.3f", existing_engram_id, existing_strength, new_strength)
    except Exception as exc:
        logger.warning("reinforce_shared: failed for %s: %s", existing_engram_id, exc)


# ---------------------------------------------------------------------------
# T7 — Batch Promotion (Phase 4 entry point)
# ---------------------------------------------------------------------------


async def promote_batch(
    pool: asyncpg.Pool,
    qdrant_client,
    neo4j_client,
    bank_id: str,
    shared_bank_id: str,
    agent_bank_ids: list[str] | None = None,
    strength_threshold: float = 0.6,
) -> PromotionResult:
    """
    Run the full promotion pipeline for one Agent Bank → Shared Bank.

    Called as NCR Phase 4 after Schema (Phase 3).

    Pipeline per candidate:
      1. Determine effective threshold (Cross-Agent Convergence may lower it).
      2. Novelty check against Shared Bank.
      3. NOVEL  → promote_to_shared()
         REDUNDANT → reinforce_shared()

    Schema-triggered candidates bypass the strength threshold entirely.

    Args:
        pool:               asyncpg connection pool.
        qdrant_client:      QdrantEngineClient instance.
        neo4j_client:       Neo4jEngineClient instance.
        bank_id:            Source Agent Dictionary bank ID.
        shared_bank_id:     Target Shared Bank ID.
        agent_bank_ids:     All agent dictionary bank IDs (for convergence check).
                            If None, convergence check is skipped.
        strength_threshold: Default NCR promotion threshold (default 0.6).

    Returns:
        PromotionResult with counts for promoted, reinforced, skipped, errors.
    """
    result = PromotionResult()

    # Collect candidates: NCR-channel + Schema-triggered (deduplicated)
    try:
        ncr_candidates = await find_promotion_candidates(pool, bank_id, strength_threshold)
    except Exception as exc:
        msg = f"find_promotion_candidates failed: {exc}"
        logger.error("[Promote] %s", msg)
        result.errors.append(msg)
        ncr_candidates = []

    try:
        schema_candidates = await find_schema_triggered_candidates(pool, neo4j_client, bank_id)
        result.schema_triggered = len(schema_candidates)
    except Exception as exc:
        logger.warning("[Promote] find_schema_triggered_candidates failed: %s", exc)
        schema_candidates = []

    # Deduplicate (schema candidates may overlap with NCR candidates)
    seen_ids: set[str] = set()
    all_candidates: list[dict[str, Any]] = []
    for c in ncr_candidates + schema_candidates:
        eid = str(c.get("engram_id", ""))
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            all_candidates.append(c)

    logger.info(
        "[Promote] bank=%s candidates=%d (ncr=%d schema=%d)",
        bank_id,
        len(all_candidates),
        len(ncr_candidates),
        len(schema_candidates),
    )

    for candidate in all_candidates:
        try:
            await _process_candidate(
                pool=pool,
                qdrant_client=qdrant_client,
                neo4j_client=neo4j_client,
                candidate=candidate,
                shared_bank_id=shared_bank_id,
                agent_bank_ids=agent_bank_ids or [],
                default_threshold=strength_threshold,
                result=result,
            )
        except Exception as exc:
            msg = f"Candidate {candidate.get('engram_id')} failed: {exc}"
            logger.error("[Promote] %s", msg)
            result.errors.append(msg)

    logger.info(
        "[Promote] Done bank=%s promoted=%d reinforced=%d skipped=%d errors=%d",
        bank_id,
        result.promoted,
        result.reinforced,
        result.skipped,
        len(result.errors),
    )
    return result


async def _process_candidate(
    pool: asyncpg.Pool,
    qdrant_client,
    neo4j_client,
    candidate: dict[str, Any],
    shared_bank_id: str,
    agent_bank_ids: list[str],
    default_threshold: float,
    result: PromotionResult,
) -> None:
    """Process a single promotion candidate through the full pipeline."""
    embedding = candidate.get("embedding") or []

    # T5 — Cross-Agent Convergence: adjust threshold if enough agents agree
    effective_threshold = default_threshold
    if agent_bank_ids and embedding:
        agent_count = await count_agents_with_similar(qdrant_client, embedding, agent_bank_ids)
        if agent_count >= CROSS_AGENT_MIN_COUNT:
            effective_threshold = CROSS_AGENT_THRESHOLD
            logger.debug(
                "[Promote] Cross-agent convergence: %d agents → threshold %.1f",
                agent_count,
                effective_threshold,
            )

    # Skip if below effective threshold (applies to NCR channel; schema-triggered bypass)
    candidate_strength = float(candidate.get("strength", 0.0))
    is_schema_triggered = candidate.get("_schema_triggered", False)
    if not is_schema_triggered and candidate_strength < effective_threshold:
        result.skipped += 1
        return

    # T2 — Novelty check
    if not embedding:
        # No embedding available — can't check novelty, skip
        result.skipped += 1
        return

    novelty = await check_novelty(qdrant_client, embedding, shared_bank_id)

    if novelty.result == NoveltyResult.NOVEL:
        # T3 — Promote
        await promote_to_shared(pool, qdrant_client, neo4j_client, candidate, shared_bank_id)
        result.promoted += 1
    else:
        # T4 — Reinforce existing Shared Engram
        if novelty.existing_engram_id:
            await reinforce_shared(pool, novelty.existing_engram_id, novelty.existing_strength)
        result.reinforced += 1
