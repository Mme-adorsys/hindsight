"""
Write Conflict Resolution — Epic 14 Story 02 (B2).

When an Agent-Engram is promoted to the Shared Bank and a semantically
similar Engram already exists (Similarity ≥ 0.85), this module decides
what to do:

  Merge              — similar, no contradiction. Stronger is base.
  Replace            — similar, no contradiction, candidate is stronger.
  Contradiction-Link — contradictory content. Higher Thalamus score wins.
                       Loser stays in Agent-Dictionary with a CONTRADICTION
                       link to the winner (no information is lost).
  Keep-Existing      — existing wins, candidate discarded.

Bio mapping: Dentate Gyrus pattern separation → conflict detection.
             CA3 pattern completion → merge / resolution.
             Contradiction graph = opposing memory traces in neocortex.

Concept: Ch. 15 B2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ConflictCandidate:
    """
    A Shared-Bank Engram that is semantically similar (≥ threshold) to the
    candidate being promoted.

    Values are extracted from the Qdrant payload returned by search_similar().
    """

    engram_id: UUID
    similarity: float  # Qdrant cosine score (higher = more similar)
    text: str
    strength: float = 0.0
    thalamus_overall: float = 0.0
    created_at: datetime | None = None


class Resolution(str, Enum):
    """Outcome of a conflict resolution decision."""

    MERGE = "merge"  # Candidate fuses into existing (existing is stronger base)
    REPLACE = "replace"  # Candidate wins — existing is archived
    KEEP_EXISTING = "keep_existing"  # Existing wins, candidate not promoted
    CONTRADICTION_LINK = "contradiction_link"  # Contradiction — winner + link


@dataclass
class ConflictResult:
    """Result of a single conflict resolution."""

    resolution: Resolution
    winner_id: UUID
    loser_id: UUID | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# T1 — Conflict Detection
# ---------------------------------------------------------------------------


async def detect_conflicts(
    qdrant_client,
    candidate_embedding: list[float],
    shared_bank_id: str,
    threshold: float = 0.85,
) -> list[ConflictCandidate]:
    """
    Query the Shared Bank for Engrams semantically similar to the candidate.

    Args:
        qdrant_client: QdrantEngineClient instance.
        candidate_embedding: Embedding of the Engram being promoted.
        shared_bank_id: The Shared Bank's bank_id (used as Qdrant filter).
        threshold: Minimum cosine similarity to count as a conflict (default 0.85).

    Returns:
        List of ConflictCandidate — empty if no conflicts found.
    """
    try:
        results = await qdrant_client.search_similar(
            embedding=candidate_embedding,
            limit=5,
            filters={"bank_id": shared_bank_id},
        )
    except Exception as exc:
        logger.warning("detect_conflicts: Qdrant search failed: %s", exc)
        return []

    candidates: list[ConflictCandidate] = []
    for hit in results:
        score = hit.get("score", 0.0)
        if score < threshold:
            continue

        payload = hit.get("payload", {})
        engram_id_raw = hit.get("engram_id")
        if not engram_id_raw:
            continue

        try:
            engram_id = UUID(str(engram_id_raw))
        except ValueError:
            continue

        created_at: datetime | None = None
        if raw_ts := payload.get("created_at"):
            try:
                if isinstance(raw_ts, str):
                    created_at = datetime.fromisoformat(raw_ts)
                elif isinstance(raw_ts, datetime):
                    created_at = raw_ts
            except (ValueError, TypeError):
                pass

        candidates.append(
            ConflictCandidate(
                engram_id=engram_id,
                similarity=score,
                text=payload.get("text", ""),
                strength=float(payload.get("strength", 0.0)),
                thalamus_overall=float(payload.get("thalamus_overall", 0.0)),
                created_at=created_at,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# T2 — Contradiction Detection (private)
# ---------------------------------------------------------------------------


async def _is_contradiction(llm, text_a: str, text_b: str) -> bool:
    """
    Ask a Small-Tier LLM whether two statements contradict each other.

    Returns True on contradiction, False otherwise (including on error).
    """
    prompt = (
        f"Do these two statements contradict each other?\nA: {text_a}\nB: {text_b}\nAnswer with exactly 'yes' or 'no'."
    )
    try:
        response = await llm.call(
            messages=[{"role": "user", "content": prompt}],
            scope="conflict_resolution",
            temperature=0.0,
            max_completion_tokens=10,
        )
        return str(response).strip().lower().startswith("yes")
    except Exception as exc:
        logger.warning("_is_contradiction: LLM call failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# T3 — Merge Logic
# ---------------------------------------------------------------------------


def merge_engrams(stronger, weaker):
    """
    Merge two FullEngrams where the stronger one is the canonical base.

    Returns a *copy* of `stronger` with:
    - strength = max(stronger.strength, weaker.strength)
    - content.extra["merged_from"] = str(weaker.engram_id)
    - tags: union of both tag lists (via metadata.tags if present)

    Args:
        stronger: FullEngram that will be the merged result's base.
        weaker:   FullEngram whose context is folded in.

    Returns:
        FullEngram (copy of stronger with merged fields).
    """
    merged = stronger.model_copy(deep=True)

    # Strength = max
    if merged.metadata is not None and weaker.metadata is not None:
        merged.metadata.strength = max(merged.metadata.strength, weaker.metadata.strength)

        # Tag union
        existing_tags = set(merged.metadata.tags or [])
        weaker_tags = set(weaker.metadata.tags or [])
        merged.metadata.tags = sorted(existing_tags | weaker_tags)

    # Record provenance in content.extra
    if merged.content is not None:
        merged.content.extra["merged_from"] = str(weaker.engram_id)

    return merged


# ---------------------------------------------------------------------------
# T4 — Contradiction Link
# ---------------------------------------------------------------------------


async def create_contradiction_link(
    neo4j_client,
    winner_id: UUID,
    loser_id: UUID,
    description: str = "",
) -> None:
    """
    Create a CONTRADICTION relationship in Neo4j from winner → loser.

    The loser Engram is NOT deleted — it remains in the Agent Dictionary with
    a visible contradiction link, preserving conflicting information in the
    knowledge graph.

    Args:
        neo4j_client: Neo4jEngineClient instance.
        winner_id:    UUID of the Engram that won (stays in / enters Shared Bank).
        loser_id:     UUID of the Engram that lost (stays in Agent Dictionary).
        description:  Human-readable description of the contradiction.
    """
    try:
        await neo4j_client.create_relationship(
            from_id=str(winner_id),
            to_id=str(loser_id),
            rel_type="CONTRADICTION",
            properties={"description": description, "resolved": True},
        )
    except Exception as exc:
        logger.warning(
            "create_contradiction_link: Neo4j write failed (winner=%s, loser=%s): %s",
            winner_id,
            loser_id,
            exc,
        )


# ---------------------------------------------------------------------------
# T5 — Resolve Conflict
# ---------------------------------------------------------------------------


async def resolve_conflict(
    candidate,
    existing: ConflictCandidate,
    llm,
) -> ConflictResult:
    """
    Determine how to resolve a conflict between a candidate Engram being
    promoted and an existing Shared-Bank Engram.

    Resolution rules (Concept Ch. 15 B2):
    1. Check for contradiction via LLM.
    2. Contradiction → higher Thalamus score wins.
       Tie → newer Engram wins.
       → Resolution: CONTRADICTION_LINK (winner promotes, loser gets link)
    3. No contradiction → stronger Engram wins (strength).
       Tie → candidate is newer → REPLACE; else MERGE (existing is base).

    Args:
        candidate: FullEngram being promoted to Shared Bank.
        existing:  ConflictCandidate from detect_conflicts().
        llm:       LLM instance for contradiction detection (Small-Tier).

    Returns:
        ConflictResult with resolution decision and winner/loser IDs.
    """
    candidate_text = ""
    if candidate.content is not None:
        candidate_text = candidate.content.text

    candidate_strength = candidate.metadata.strength if candidate.metadata else 0.0
    candidate_thalamus = candidate.metadata.thalamus_overall or 0.0 if candidate.metadata else 0.0
    candidate_created_at: datetime | None = candidate.metadata.created_at if candidate.metadata else None

    is_contradiction = await _is_contradiction(llm, candidate_text, existing.text)

    if is_contradiction:
        # Higher Thalamus score wins
        if candidate_thalamus > existing.thalamus_overall:
            return ConflictResult(
                resolution=Resolution.CONTRADICTION_LINK,
                winner_id=candidate.engram_id,
                loser_id=existing.engram_id,
                description=(
                    f"Candidate (thalamus={candidate_thalamus:.3f}) contradicts "
                    f"existing (thalamus={existing.thalamus_overall:.3f}). Candidate wins."
                ),
            )
        elif existing.thalamus_overall > candidate_thalamus:
            return ConflictResult(
                resolution=Resolution.KEEP_EXISTING,
                winner_id=existing.engram_id,
                loser_id=candidate.engram_id,
                description=(
                    f"Existing (thalamus={existing.thalamus_overall:.3f}) contradicts "
                    f"candidate (thalamus={candidate_thalamus:.3f}). Existing wins."
                ),
            )
        else:
            # Tie → newer wins
            candidate_newer = _candidate_is_newer(candidate_created_at, existing.created_at)
            if candidate_newer:
                return ConflictResult(
                    resolution=Resolution.CONTRADICTION_LINK,
                    winner_id=candidate.engram_id,
                    loser_id=existing.engram_id,
                    description="Contradiction with equal scores. Candidate is newer — wins.",
                )
            else:
                return ConflictResult(
                    resolution=Resolution.KEEP_EXISTING,
                    winner_id=existing.engram_id,
                    loser_id=candidate.engram_id,
                    description="Contradiction with equal scores. Existing is newer — wins.",
                )

    # No contradiction — merge/replace based on strength
    if candidate_strength >= existing.strength:
        # Candidate is stronger (or equal) → it becomes the base
        return ConflictResult(
            resolution=Resolution.REPLACE,
            winner_id=candidate.engram_id,
            loser_id=existing.engram_id,
            description=(
                f"No contradiction. Candidate strength ({candidate_strength:.3f}) ≥ "
                f"existing ({existing.strength:.3f}). Candidate replaces existing."
            ),
        )
    else:
        # Existing is stronger → existing stays as base, candidate folds in
        return ConflictResult(
            resolution=Resolution.MERGE,
            winner_id=existing.engram_id,
            loser_id=candidate.engram_id,
            description=(
                f"No contradiction. Existing strength ({existing.strength:.3f}) > "
                f"candidate ({candidate_strength:.3f}). Merge into existing."
            ),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _candidate_is_newer(candidate_dt: datetime | None, existing_dt: datetime | None) -> bool:
    """Return True if candidate was created more recently than existing."""
    if candidate_dt is None:
        return False
    if existing_dt is None:
        return True
    return candidate_dt > existing_dt
