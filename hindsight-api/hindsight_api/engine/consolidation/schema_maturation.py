"""
NCR Phase 3 — Schema Emergence, Rules R2+R3: Maturation & Abstraction.

R2 — Repetition/Maturation:
    ClusterCandidates that survive K NCR cycles without losing cohesion are
    promoted to full Schema nodes.  One-time clusters are ignored.

R3 — Abstraction/Specialization:
    On promotion an LLM (Medium-Tier) extracts the common pattern shared by
    the member Engrams and encodes it as the Schema content + tags.

Biological mapping:
  REM Sleep — pattern consolidation.  Schemas that survive repeated
  reactivation become durable abstract knowledge structures.
  Ref: concept.md ch. 13 — Schema Emergence, R2 Maturation / R3 Abstraction

Epic 13, Story 02.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import asyncpg
from pydantic import BaseModel, Field

from hindsight_api.engine import engram_dictionary as dict_repo

if TYPE_CHECKING:
    from hindsight_api.engine.neo4j_client import Neo4jEngineClient
    from hindsight_api.engine.qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_MATURATION_THRESHOLD: int = 3  # K NCR cycles before promotion
_DEFAULT_SCHEMA_INIT_STRENGTH: float = 0.3

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

_FETCH_CANDIDATES_CYPHER = """
MATCH (c:ClusterCandidate {bank_id: $bank_id})
RETURN c.candidate_id      AS candidate_id,
       c.member_ids         AS member_ids,
       c.cohesion           AS cohesion,
       c.ncr_cycles_survived AS ncr_cycles_survived
"""

_INCREMENT_CYCLES_CYPHER = """
MATCH (c:ClusterCandidate {candidate_id: $candidate_id, bank_id: $bank_id})
SET c.ncr_cycles_survived = c.ncr_cycles_survived + 1,
    c.updated_at = $now
RETURN c.ncr_cycles_survived AS ncr_cycles_survived
"""

_RESET_CYCLES_CYPHER = """
MATCH (c:ClusterCandidate {candidate_id: $candidate_id, bank_id: $bank_id})
SET c.ncr_cycles_survived = 0,
    c.updated_at = $now
"""

# Fetch active member Engrams to check cohesion + gather content for LLM
_FETCH_MEMBERS_CYPHER = """
MATCH (e:Engram {bank_id: $bank_id})
WHERE e.engram_id IN $member_ids
RETURN e.engram_id AS engram_id, e.status AS status,
       e.layer AS layer
"""

# CREATE Schema node (no MERGE — we want exactly one Schema per candidate)
_CREATE_SCHEMA_CYPHER = """
CREATE (s:Schema {
    schema_id:         $schema_id,
    bank_id:           $bank_id,
    content:           $content,
    tags:              $tags,
    strength:          $strength,
    embedding:         $embedding,
    created_at:        $now,
    last_reinforced_at: $now
})
"""

# MERGE SCHEMA relationship: Engram → Schema
_SCHEMA_LINK_CYPHER = """
MATCH (e:Engram {engram_id: $engram_id}), (s:Schema {schema_id: $schema_id})
MERGE (e)-[r:SCHEMA]->(s)
ON CREATE SET r.weight = 1.0, r.source = 'ncr_maturation', r.created_at = $now
"""

_DELETE_CANDIDATE_CYPHER = """
MATCH (c:ClusterCandidate {candidate_id: $candidate_id, bank_id: $bank_id})
DETACH DELETE c
"""


# ---------------------------------------------------------------------------
# LLM response model
# ---------------------------------------------------------------------------


class _SchemaAbstraction(BaseModel):
    content: str = Field(description="Abstracted schema description — the common pattern.")
    tags: list[str] = Field(default_factory=list, description="2–5 short keyword tags.")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MaturedSchema:
    """A Schema that was created during this NCR run."""

    schema_id: str
    bank_id: str
    content: str
    tags: list[str]
    member_ids: list[str]
    candidate_id: str  # The ClusterCandidate this came from


@dataclass
class MaturationResult:
    """Summary of one R2+R3 run."""

    candidates_incremented: int = 0
    candidates_reset: int = 0
    schemas_created: int = 0
    details: list[MaturedSchema] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_member_ratio(member_rows: list[dict[str, Any]], member_ids: list[str]) -> float:
    """Fraction of requested members that are still active/neocortex."""
    if not member_ids:
        return 0.0
    active = sum(1 for r in member_rows if r.get("status") == "active" and r.get("layer") == "neocortex")
    return active / len(member_ids)


async def _abstract_with_llm(
    llm: Any,
    member_contents: list[str],
) -> _SchemaAbstraction:
    """
    Ask the LLM (Medium-Tier) to abstract the common pattern of member Engrams.

    Returns a _SchemaAbstraction with content + tags.
    Falls back to a plain concatenation summary on failure.
    """
    if not member_contents:
        return _SchemaAbstraction(content="(empty schema)", tags=[])

    facts_text = "\n".join(f"- {c}" for c in member_contents[:20])  # cap at 20

    try:
        result: _SchemaAbstraction = await llm.call(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a knowledge abstraction engine. "
                        "Your task is to identify the common pattern shared by a set of memory facts "
                        "and formulate a single, concise abstract statement that generalises them."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"These memory facts share a common pattern:\n{facts_text}\n\n"
                        "Identify the common pattern and respond with:\n"
                        "- content: one sentence abstract generalisation (drop specific names/values)\n"
                        "- tags: 2-5 short keyword tags (lowercase, no spaces)"
                    ),
                },
            ],
            response_format=_SchemaAbstraction,
            scope="schema_abstraction",
            temperature=0.3,
        )
        return result
    except Exception as exc:
        logger.warning(f"[R2/R3] LLM abstraction failed, using fallback: {exc}")
        # Fallback: truncated join
        joined = " | ".join(member_contents[:5])
        return _SchemaAbstraction(content=f"Pattern: {joined[:200]}", tags=[])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_maturation(
    bank_id: str,
    neo4j_client: "Neo4jEngineClient",
    qdrant_client: "QdrantEngineClient",
    pool: asyncpg.Pool,
    llm: Any,
    embeddings_fn: Any,
    *,
    maturation_threshold: int = _DEFAULT_MATURATION_THRESHOLD,
    init_strength: float = _DEFAULT_SCHEMA_INIT_STRENGTH,
    min_active_ratio: float = 0.67,
) -> MaturationResult:
    """
    Execute R2+R3 — Maturation & Abstraction for *bank_id*.

    1. Load all ClusterCandidates for this bank.
    2. Fetch each candidate's members to check cohesion health.
    3. If active member ratio < min_active_ratio → reset ncr_cycles_survived.
       Otherwise → increment ncr_cycles_survived.
    4. Candidates reaching maturation_threshold → promote to Schema.
       a. LLM abstracts common content (R3).
       b. Embedding generated from abstract content.
       c. Schema node created in Neo4j, Qdrant, Dictionary.
       d. SCHEMA links created to all active member Engrams.
       e. ClusterCandidate deleted.

    Args:
        bank_id: Memory bank to process.
        neo4j_client: Connected Neo4j client.
        qdrant_client: Connected Qdrant client.
        pool: asyncpg connection pool for Dictionary writes.
        llm: LLM wrapper (must support .call()).
        embeddings_fn: Callable[[str], Awaitable[list[float]]] — generates embedding.
        maturation_threshold: NCR cycles required for promotion (default 3).
        init_strength: Initial strength for new Schema nodes (default 0.3).
        min_active_ratio: Fraction of members that must still be active (default 0.67).

    Returns:
        MaturationResult with counts and details.
    """
    result = MaturationResult()
    now_str = datetime.now(timezone.utc).isoformat()

    # ── Step 1: Load candidates ───────────────────────────────────────────────
    try:
        candidate_rows = await neo4j_client.run_cypher(_FETCH_CANDIDATES_CYPHER, {"bank_id": bank_id})
    except Exception as exc:
        logger.error(f"[R2] Candidate fetch failed for bank {bank_id}: {exc}")
        return result

    if not candidate_rows:
        logger.debug(f"[R2] No ClusterCandidates for bank {bank_id}")
        return result

    # ── Step 2-4: Process each candidate ────────────────────────────────────
    for row in candidate_rows:
        candidate_id: str = row["candidate_id"]
        member_ids: list[str] = list(row["member_ids"])

        # Check member health
        try:
            member_rows = await neo4j_client.run_cypher(
                _FETCH_MEMBERS_CYPHER,
                {"bank_id": bank_id, "member_ids": member_ids},
            )
        except Exception as exc:
            logger.warning(f"[R2] Member fetch failed for {candidate_id}: {exc}")
            member_rows = []

        active_ratio = _active_member_ratio(member_rows, member_ids)

        if active_ratio < min_active_ratio:
            # Cohesion degraded — reset cycles
            try:
                await neo4j_client.run_cypher(
                    _RESET_CYCLES_CYPHER, {"candidate_id": candidate_id, "bank_id": bank_id, "now": now_str}
                )
                result.candidates_reset += 1
                logger.debug(f"[R2] Reset cycles for {candidate_id} (active_ratio={active_ratio:.2f})")
            except Exception as exc:
                logger.warning(f"[R2] Reset failed for {candidate_id}: {exc}")
            continue

        # Increment cycles
        try:
            inc_rows = await neo4j_client.run_cypher(
                _INCREMENT_CYCLES_CYPHER,
                {"candidate_id": candidate_id, "bank_id": bank_id, "now": now_str},
            )
            current_cycles = int(inc_rows[0]["ncr_cycles_survived"]) if inc_rows else 0
            result.candidates_incremented += 1
        except Exception as exc:
            logger.warning(f"[R2] Increment failed for {candidate_id}: {exc}")
            continue

        if current_cycles < maturation_threshold:
            logger.debug(f"[R2] {candidate_id} at {current_cycles}/{maturation_threshold} cycles")
            continue

        # ── PROMOTE to Schema ────────────────────────────────────────────────
        logger.info(f"[R2] Promoting {candidate_id} to Schema after {current_cycles} cycles")

        active_member_ids = [
            r["engram_id"] for r in member_rows if r.get("status") == "active" and r.get("layer") == "neocortex"
        ]

        # Gather content for LLM (fetch from Qdrant payloads)
        member_contents: list[str] = []
        for mid in active_member_ids[:20]:
            try:
                point = await qdrant_client.get_by_id(mid)
                if point and "payload" in point:
                    text = point["payload"].get("text", "")
                    if text:
                        member_contents.append(text)
            except Exception:
                pass

        # R3 — Abstract via LLM
        abstraction = await _abstract_with_llm(llm, member_contents)

        # Generate embedding for Schema content
        try:
            schema_embedding: list[float] = await embeddings_fn(abstraction.content)
        except Exception as exc:
            logger.warning(f"[R3] Embedding failed for schema from {candidate_id}: {exc}")
            schema_embedding = []

        schema_id = str(uuid.uuid4())

        # Write Schema node to Neo4j (includes embedding for R4 schema-fit checks)
        try:
            await neo4j_client.run_cypher(
                _CREATE_SCHEMA_CYPHER,
                {
                    "schema_id": schema_id,
                    "bank_id": bank_id,
                    "content": abstraction.content,
                    "tags": abstraction.tags,
                    "strength": init_strength,
                    "embedding": schema_embedding,
                    "now": now_str,
                },
            )
        except Exception as exc:
            logger.error(f"[R3] Schema node creation failed for {candidate_id}: {exc}")
            continue

        # Upsert to Qdrant
        if schema_embedding:
            try:
                await qdrant_client.upsert_point(
                    engram_id=schema_id,
                    embedding=schema_embedding,
                    payload={
                        "text": abstraction.content,
                        "tags": abstraction.tags,
                        "bank_id": bank_id,
                        "type": "schema",
                        "strength": init_strength,
                    },
                )
            except Exception as exc:
                logger.warning(f"[R3] Qdrant upsert failed for schema {schema_id}: {exc}")

        # Insert Dictionary entry
        try:
            await dict_repo.insert_entry(
                pool,
                {
                    "engram_id": schema_id,
                    "bank_id": bank_id,
                    "strength": init_strength,
                    "layer": "neocortex",
                    "abstraction_level": 0.8,
                    "tags": abstraction.tags,
                    "status": "active",
                },
            )
        except Exception as exc:
            logger.warning(f"[R3] Dictionary insert failed for schema {schema_id}: {exc}")

        # Create SCHEMA links to all active member Engrams
        for mid in active_member_ids:
            try:
                await neo4j_client.run_cypher(
                    _SCHEMA_LINK_CYPHER,
                    {"engram_id": mid, "schema_id": schema_id, "now": now_str},
                )
            except Exception as exc:
                logger.warning(f"[R3] SCHEMA link failed ({mid} → {schema_id}): {exc}")

        # Delete ClusterCandidate
        try:
            await neo4j_client.run_cypher(
                _DELETE_CANDIDATE_CYPHER,
                {"candidate_id": candidate_id, "bank_id": bank_id},
            )
        except Exception as exc:
            logger.warning(f"[R2] Candidate cleanup failed for {candidate_id}: {exc}")

        schema = MaturedSchema(
            schema_id=schema_id,
            bank_id=bank_id,
            content=abstraction.content,
            tags=abstraction.tags,
            member_ids=active_member_ids,
            candidate_id=candidate_id,
        )
        result.details.append(schema)
        result.schemas_created += 1

    logger.info(
        f"[R2/R3] bank={bank_id} incremented={result.candidates_incremented} "
        f"reset={result.candidates_reset} schemas_created={result.schemas_created}"
    )
    return result
