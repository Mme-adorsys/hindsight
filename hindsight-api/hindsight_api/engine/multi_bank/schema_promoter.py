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

from ..consolidation.c2_schema_writer import weighted_centroid
from ..consolidation.constants import (
    CROSS_AGENT_MATCH_THRESHOLD,
    SHARED_PROMOTION_MAX_DAYS_INACTIVE,
    SHARED_PROMOTION_MIN_CYCLES,
    SHARED_PROMOTION_MIN_EVIDENCE,
)
from ..schema.models import SchemaModel
from ..schema.schema_repository import (
    create_schema,
    get_schema,
    link_contradicts,
    list_active_schemas,
    update_schema,
)
from .property_diff import ConflictReport, detect_conflicts

if TYPE_CHECKING:
    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)

# Story 24 — Property keys we manage on Shared-side schemas. Kept in one
# place so the create + reinforce paths stay in sync.
_PROP_SOURCE_BANK_IDS = "source_bank_ids"
_PROP_CROSS_AGENT_COUNT = "cross_agent_count"
_PROP_CONFIDENCE_TIER = "confidence_tier"
_PROP_DISPUTED_KEYS = "disputed_keys"  # Story 25 — keys flagged in detect_conflicts
_TIER_AGENT_LOCAL = "agent_local"
_TIER_CROSS_VALIDATED = "cross_agent_validated"
_TIER_DISPUTED = "cross_agent_disputed"  # Story 25


@dataclass
class SchemaPromotionResult:
    """Aggregate result of one ``promote_schemas_batch`` run."""

    source_bank_id: str
    shared_bank_id: str
    scanned: int = 0
    promoted: int = 0
    reinforced: int = 0  # Story 24 — convergent matches with an existing Shared schema
    disputed: int = 0  # Story 25 — conflicts detected; alternative hypothesis minted
    skipped_below_evidence: int = 0
    skipped_below_cycles: int = 0
    skipped_inactive: int = 0
    promoted_ids: list[_uuid_mod.UUID] = field(default_factory=list)
    reinforced_ids: list[_uuid_mod.UUID] = field(default_factory=list)
    disputed_ids: list[_uuid_mod.UUID] = field(default_factory=list)
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
    # Story 24 — initial cross-agent metadata. The list grows on later
    # reinforce calls; the tier upgrades to "cross_agent_validated" once
    # the second source bank arrives.
    promoted_props[_PROP_SOURCE_BANK_IDS] = [source_bank_id]
    promoted_props[_PROP_CROSS_AGENT_COUNT] = 1
    promoted_props[_PROP_CONFIDENCE_TIER] = _TIER_AGENT_LOCAL

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


async def match_existing_shared_schema(
    centroid: list[float],
    *,
    shared_bank_id: str,
    qdrant: "QdrantEngineClient",
    neo4j: "Neo4jEngineClient",
    threshold: float = CROSS_AGENT_MATCH_THRESHOLD,
) -> tuple[SchemaModel, float] | None:
    """Look for a Shared-Bank schema whose centroid is within ``threshold``.

    Returns ``(schema, cosine)`` of the closest match, or ``None`` if none
    qualify. Bank-scoped via Qdrant payload filter; ``kind=schema`` keeps
    engram points out of the search. Best-effort — Qdrant errors are
    logged as ``None`` so the caller falls back to the create path.
    """
    if not centroid:
        return None
    try:
        hits = await qdrant.search_similar(
            embedding=centroid,
            limit=3,
            filters={"must": [{"key": "bank_id", "match": {"value": shared_bank_id}}]},
            kind="schema",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("[SchemaPromoter] match qdrant search failed: %s", exc.__class__.__name__)
        return None
    for hit in hits:
        score = float(hit.get("score", 0.0))
        if score < threshold:
            continue
        schema_id_raw = hit.get("payload", {}).get("schema_id") or hit.get("engram_id")
        if not schema_id_raw:
            continue
        try:
            sid = _uuid_mod.UUID(str(schema_id_raw))
        except (TypeError, ValueError):
            continue
        try:
            existing = await get_schema(neo4j, sid, label="Schema")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SchemaPromoter] match get_schema failed: %s", exc.__class__.__name__)
            continue
        if existing is None or not isinstance(existing, SchemaModel):
            continue
        return existing, score
    return None


async def reinforce_shared_schema(
    existing: SchemaModel,
    incoming: SchemaModel,
    *,
    source_bank_id: str,
    shared_bank_id: str,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient | None" = None,
    incoming_centroid: list[float] | None = None,
) -> SchemaModel:
    """Merge an incoming agent-local schema into an existing Shared one.

    Updates:
        - ``evidence_count += incoming.evidence_count`` (audit-only sum)
        - ``cross_agent_count++`` and ``source_bank_ids`` extended by the
          new ``source_bank_id`` (deduplicated — re-promoting from the
          same agent is a no-op on the counter)
        - ``confidence_tier`` upgrades to ``cross_agent_validated`` once
          two distinct source banks have contributed
        - ``last_reinforced_at`` = now
        - Centroid running mean (existing × cross_agent_count + incoming
          × 1, L2-renormalised), persisted via ``upsert_schema_centroid``
          when both ``qdrant`` and ``incoming_centroid`` are supplied
    """
    props = dict(existing.properties)

    sources_raw = props.get(_PROP_SOURCE_BANK_IDS) or []
    source_set = list(dict.fromkeys([*sources_raw, source_bank_id]))  # preserve order, dedup
    new_count = len(source_set)
    same_source = new_count == len(sources_raw)

    props[_PROP_SOURCE_BANK_IDS] = source_set
    props[_PROP_CROSS_AGENT_COUNT] = new_count
    props[_PROP_CONFIDENCE_TIER] = _TIER_CROSS_VALIDATED if new_count >= 2 else _TIER_AGENT_LOCAL

    now = datetime.now(timezone.utc)
    partial = {
        "properties_json": _serialise_props(props),
        "evidence_count": int(existing.evidence_count) + int(incoming.evidence_count),
        "last_reinforced_at": now.isoformat(),
    }

    await update_schema(neo4j, existing.id, partial, label="Schema")

    if qdrant is not None and incoming_centroid is not None and existing.centroid_qdrant_id is not None:
        try:
            point = await qdrant.get_by_id(str(existing.centroid_qdrant_id))
            old_centroid = list(point["vector"]) if point and point.get("vector") else None
            if old_centroid:
                # Existing centroid carries `cross_agent_count` agents'
                # worth of evidence; the incoming schema is one new agent.
                # Weight the running mean accordingly.
                merged = weighted_centroid(
                    old_centroid=old_centroid,
                    old_weight=max(1, len(sources_raw)),
                    new_centroid=incoming_centroid,
                    new_weight=1,
                )
                payload = (point.get("payload") if point else {}) or {}
                payload["bank_id"] = shared_bank_id
                await qdrant.upsert_schema_centroid(
                    schema_id=str(existing.id),
                    centroid=merged,
                    schema_meta=payload,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[SchemaPromoter] cross-agent centroid merge failed schema=%s reason=%s",
                existing.id,
                exc.__class__.__name__,
            )

    logger.info(
        "[SchemaPromoter] reinforced shared schema %s ← %s (source=%s sources_now=%d tier=%s%s)",
        existing.id,
        incoming.id,
        source_bank_id,
        new_count,
        props[_PROP_CONFIDENCE_TIER],
        " same-source-dedup" if same_source else "",
    )
    return existing.model_copy(
        update={
            "properties": props,
            "evidence_count": partial["evidence_count"],
            "last_reinforced_at": now,
        }
    )


def _serialise_props(props: dict) -> str:
    """Round-trip via the SchemaModel helper so the JSON shape stays canonical."""
    from ..schema.models import _serialise_props_for_neo4j

    return _serialise_props_for_neo4j(props)


async def _mint_disputed_alternative(
    *,
    incoming: SchemaModel,
    existing: SchemaModel,
    conflicts: list[ConflictReport],
    source_bank_id: str,
    shared_bank_id: str,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient | None" = None,
    qdrant_centroid: list[float] | None = None,
) -> SchemaModel:
    """Story 25 conflict path — fork instead of merge.

    Mints a fresh Shared-Bank schema carrying the incoming agent's view of
    the disputed slots, marks both schemas ``cross_agent_disputed``, and
    links them via a symmetric :CONTRADICTS edge. Existing Shared schema
    is not mutated beyond the tier flip — its evidence trail stays
    intact, the alternative hypothesis lives next to it.
    """
    new_id = _uuid_mod.uuid4()
    now = datetime.now(timezone.utc)
    fork_props = dict(incoming.properties)
    fork_props["source_bank_id"] = source_bank_id
    fork_props["promoted_from_schema_id"] = str(incoming.id)
    fork_props[_PROP_SOURCE_BANK_IDS] = [source_bank_id]
    fork_props[_PROP_CROSS_AGENT_COUNT] = 1
    fork_props[_PROP_CONFIDENCE_TIER] = _TIER_DISPUTED
    fork_props[_PROP_DISPUTED_KEYS] = sorted({c.key for c in conflicts})

    fork = SchemaModel(
        id=new_id,
        description=incoming.description,
        properties=fork_props,
        centroid_qdrant_id=new_id if qdrant_centroid else None,
        evidence_engram_ids=[],
        evidence_count=int(incoming.evidence_count),
        cycles_survived=int(incoming.cycles_survived),
        status="active",
        created_at=now,
        last_reinforced_at=now,
    )
    await create_schema(neo4j, fork, label="Schema")

    # Flip the existing schema's tier so downstream consumers know it has
    # a contested twin. ``disputed_keys`` mirror what the fork advertises.
    existing_props = dict(existing.properties)
    existing_props[_PROP_CONFIDENCE_TIER] = _TIER_DISPUTED
    existing_props[_PROP_DISPUTED_KEYS] = sorted(
        {*(existing_props.get(_PROP_DISPUTED_KEYS) or []), *(c.key for c in conflicts)}
    )
    try:
        await update_schema(
            neo4j,
            existing.id,
            {"properties_json": _serialise_props(existing_props)},
            label="Schema",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "[SchemaPromoter] disputed-tier update failed for existing=%s reason=%s",
            existing.id,
            exc.__class__.__name__,
        )

    try:
        await link_contradicts(neo4j, existing.id, fork.id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "[SchemaPromoter] :CONTRADICTS edge failed %s↔%s reason=%s",
            existing.id,
            fork.id,
            exc.__class__.__name__,
        )

    if qdrant is not None and qdrant_centroid is not None:
        try:
            await qdrant.upsert_schema_centroid(
                schema_id=str(new_id),
                centroid=qdrant_centroid,
                schema_meta={
                    "bank_id": shared_bank_id,
                    "source_bank_id": source_bank_id,
                    "confidence_tier": _TIER_DISPUTED,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SchemaPromoter] disputed centroid upsert failed schema=%s reason=%s",
                new_id,
                exc.__class__.__name__,
            )

    logger.info(
        "[SchemaPromoter] disputed fork existing=%s fork=%s source=%s keys=%s",
        existing.id,
        new_id,
        source_bank_id,
        fork_props[_PROP_DISPUTED_KEYS],
    )
    return fork


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

            # Story 24 — try cross-agent convergence first; fall back to
            # the create path when no Shared schema sits within the
            # cosine threshold.
            match = None
            if qdrant is not None and centroid is not None:
                match = await match_existing_shared_schema(
                    centroid,
                    shared_bank_id=shared_bank_id,
                    qdrant=qdrant,
                    neo4j=neo4j,
                )
            if match is not None:
                existing, _score = match
                # Story 25 — before merging, check whether the property
                # sets actually agree. Categorical/numeric/scalar
                # disagreement above the configured thresholds means the
                # two banks describe different things; we mint a parallel
                # "alternative hypothesis" instead of corrupting the
                # existing shared schema.
                conflicts = detect_conflicts(existing.properties, schema.properties)
                if conflicts:
                    fork = await _mint_disputed_alternative(
                        incoming=schema,
                        existing=existing,
                        conflicts=conflicts,
                        source_bank_id=source_bank_id,
                        shared_bank_id=shared_bank_id,
                        neo4j=neo4j,
                        qdrant=qdrant,
                        qdrant_centroid=centroid,
                    )
                    result.disputed += 1
                    result.disputed_ids.append(fork.id)
                    continue
                merged = await reinforce_shared_schema(
                    existing,
                    schema,
                    source_bank_id=source_bank_id,
                    shared_bank_id=shared_bank_id,
                    neo4j=neo4j,
                    qdrant=qdrant,
                    incoming_centroid=centroid,
                )
                result.reinforced += 1
                result.reinforced_ids.append(merged.id)
                continue

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
        "[SchemaPromoter] batch source=%s scanned=%d promoted=%d reinforced=%d "
        "skipped(evid/cycles/inactive)=%d/%d/%d errors=%d",
        source_bank_id,
        result.scanned,
        result.promoted,
        result.reinforced,
        result.skipped_below_evidence,
        result.skipped_below_cycles,
        result.skipped_inactive,
        len(result.errors),
    )
    return result
