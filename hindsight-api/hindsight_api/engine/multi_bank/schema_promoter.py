"""Schema-level Shared-Bank promotion (Epic 25 Story 23).

Replaces the legacy engram-based ``multi_bank_promoter.promote_to_shared``
(Epic 14) with a schema-level analogue: a strong agent-local schema is
*copied* into the Shared Bank under a fresh id, with ``source_bank_id``
stamped on for audit. Evidence engrams stay agent-local — sharing is for
the generalisation, not the episodes.

Bio mapping: cortical schemas can converge across individuals over time
(shared semantic structure in human populations); episodic traces stay
person-specific. Concept §15 (Multi-Bank Architecture) + §4.2 (CLS).

Promotion criteria (drift-guarded constants in
``engine.consolidation.constants``):

  evidence_count        ≥ SHARED_PROMOTION_MIN_EVIDENCE  (= 10)
  cycles_survived       ≥ SHARED_PROMOTION_MIN_CYCLES    (= 3)
  last_reinforced_at    >  now - SHARED_PROMOTION_MAX_DAYS_INACTIVE
                                                          (= 7d)

The Story 26 cleanup retires the legacy engram-promoter; until then both
paths coexist (schema promotion is wired manually, engram promotion runs
on the existing NCR ``shared`` phase).
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..consolidation.constants import (
    SHARED_PROMOTION_MAX_DAYS_INACTIVE,
    SHARED_PROMOTION_MIN_CYCLES,
    SHARED_PROMOTION_MIN_EVIDENCE,
)
from ..schema.models import SchemaModel
from ..schema.schema_repository import create_schema, list_active_schemas

if TYPE_CHECKING:
    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)


@dataclass
class SchemaPromotionResult:
    """Aggregate result of one ``promote_schemas_batch`` run."""

    source_bank_id: str
    shared_bank_id: str
    scanned: int = 0
    promoted: int = 0
    skipped_below_evidence: int = 0
    skipped_below_cycles: int = 0
    skipped_inactive: int = 0
    promoted_ids: list[_uuid_mod.UUID] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _meets_criteria(
    schema: SchemaModel,
    *,
    now: datetime,
    min_evidence: int,
    min_cycles: int,
    max_days_inactive: int,
) -> tuple[bool, str | None]:
    """Return ``(eligible, skip_reason)`` for a single schema."""
    if schema.evidence_count < min_evidence:
        return False, "below_evidence"
    if schema.cycles_survived < min_cycles:
        return False, "below_cycles"
    last = schema.last_reinforced_at
    if last is None or (now - last) > timedelta(days=max_days_inactive):
        return False, "inactive"
    return True, None


async def find_schema_promotion_candidates(
    *,
    neo4j: "Neo4jEngineClient",
    min_evidence: int = SHARED_PROMOTION_MIN_EVIDENCE,
    min_cycles: int = SHARED_PROMOTION_MIN_CYCLES,
    max_days_inactive: int = SHARED_PROMOTION_MAX_DAYS_INACTIVE,
    limit: int = 200,
    now: datetime | None = None,
) -> list[SchemaModel]:
    """List active Schemas that pass the Shared-Bank promotion criteria.

    Reads via the bank-agnostic ``list_active_schemas`` helper and filters
    in Python — schema counts per bank are small (tens, not millions) so
    a roundtrip-then-filter beats a custom Cypher with parameters that
    repeat the constants.
    """
    when = now or datetime.now(timezone.utc)
    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit)
    candidates: list[SchemaModel] = []
    for s in schemas:
        if not isinstance(s, SchemaModel):
            continue
        ok, _reason = _meets_criteria(
            s,
            now=when,
            min_evidence=min_evidence,
            min_cycles=min_cycles,
            max_days_inactive=max_days_inactive,
        )
        if ok:
            candidates.append(s)
    logger.info(
        "[SchemaPromoter] candidates scanned=%d eligible=%d (min_evidence=%d min_cycles=%d max_days_inactive=%d)",
        len(schemas),
        len(candidates),
        min_evidence,
        min_cycles,
        max_days_inactive,
    )
    return candidates


async def promote_schema_to_shared(
    schema: SchemaModel,
    *,
    source_bank_id: str,
    shared_bank_id: str,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient | None" = None,
    qdrant_centroid: list[float] | None = None,
) -> SchemaModel:
    """Copy ``schema`` into the Shared Bank under a fresh id.

    The copy keeps description, properties, and ``evidence_count`` (audit
    value — Shared-side has no engrams to point at, so
    ``evidence_engram_ids`` is reset to ``[]``). ``source_bank_id`` is
    stamped onto the ``properties`` dict so downstream cross-agent
    convergence (Story 24) can attribute the source.

    When ``qdrant`` and ``qdrant_centroid`` are supplied the centroid is
    also written into Qdrant under the new id with ``bank_id=shared``.
    Per-step Qdrant failures are logged but do not abort the promotion —
    the cortex Schema node is the source of truth, the centroid will be
    rebuilt on the next C2 cycle in the shared bank if needed.
    """
    new_id = _uuid_mod.uuid4()
    now = datetime.now(timezone.utc)
    promoted_props = dict(schema.properties)
    promoted_props["source_bank_id"] = source_bank_id
    promoted_props["promoted_from_schema_id"] = str(schema.id)

    copy = SchemaModel(
        id=new_id,
        description=schema.description,
        properties=promoted_props,
        centroid_qdrant_id=new_id if qdrant_centroid else None,
        evidence_engram_ids=[],
        evidence_count=int(schema.evidence_count),
        cycles_survived=int(schema.cycles_survived),
        status="active",
        created_at=now,
        last_reinforced_at=now,
    )
    await create_schema(neo4j, copy, label="Schema")

    if qdrant is not None and qdrant_centroid is not None:
        try:
            await qdrant.upsert_schema_centroid(
                schema_id=str(new_id),
                centroid=qdrant_centroid,
                schema_meta={
                    "bank_id": shared_bank_id,
                    "source_bank_id": source_bank_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[SchemaPromoter] qdrant centroid copy failed schema=%s reason=%s",
                new_id,
                exc.__class__.__name__,
            )
    logger.info(
        "[SchemaPromoter] promoted schema %s → shared %s (source=%s evidence=%d cycles=%d)",
        schema.id,
        new_id,
        source_bank_id,
        schema.evidence_count,
        schema.cycles_survived,
    )
    return copy


async def promote_schemas_batch(
    *,
    source_bank_id: str,
    shared_bank_id: str,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient | None" = None,
    min_evidence: int = SHARED_PROMOTION_MIN_EVIDENCE,
    min_cycles: int = SHARED_PROMOTION_MIN_CYCLES,
    max_days_inactive: int = SHARED_PROMOTION_MAX_DAYS_INACTIVE,
    limit: int = 200,
    now: datetime | None = None,
) -> SchemaPromotionResult:
    """Run schema-promotion for one Agent Bank → Shared Bank pass.

    Per-schema failures are logged into ``result.errors`` but the batch
    keeps going (best-effort, matching the rest of Block D/F semantics).
    """
    when = now or datetime.now(timezone.utc)
    result = SchemaPromotionResult(
        source_bank_id=source_bank_id,
        shared_bank_id=shared_bank_id,
    )

    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit)
    schemas = [s for s in schemas if isinstance(s, SchemaModel)]
    result.scanned = len(schemas)

    for schema in schemas:
        ok, reason = _meets_criteria(
            schema,
            now=when,
            min_evidence=min_evidence,
            min_cycles=min_cycles,
            max_days_inactive=max_days_inactive,
        )
        if not ok:
            if reason == "below_evidence":
                result.skipped_below_evidence += 1
            elif reason == "below_cycles":
                result.skipped_below_cycles += 1
            elif reason == "inactive":
                result.skipped_inactive += 1
            continue
        try:
            centroid = None
            if qdrant is not None and schema.centroid_qdrant_id is not None:
                point = await qdrant.get_by_id(str(schema.centroid_qdrant_id))
                centroid = list(point["vector"]) if point and point.get("vector") else None
            copy = await promote_schema_to_shared(
                schema,
                source_bank_id=source_bank_id,
                shared_bank_id=shared_bank_id,
                neo4j=neo4j,
                qdrant=qdrant,
                qdrant_centroid=centroid,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            msg = f"promote schema={schema.id} failed: {exc.__class__.__name__}"
            logger.warning("[SchemaPromoter] %s", msg)
            result.errors.append(msg)
            continue
        result.promoted += 1
        result.promoted_ids.append(copy.id)

    logger.info(
        "[SchemaPromoter] batch source=%s scanned=%d promoted=%d skipped(evid/cycles/inactive)=%d/%d/%d errors=%d",
        source_bank_id,
        result.scanned,
        result.promoted,
        result.skipped_below_evidence,
        result.skipped_below_cycles,
        result.skipped_inactive,
        len(result.errors),
    )
    return result
