"""C3 schema restructuring — Hyper-Schema R3 + Schema Death R5.

Block C of Epic 25. Where C2 (Stories 04–12) operates on raw engrams to
emit / reinforce schemas, C3 operates one layer up: it restructures the
**schema graph** itself.

R3 — Hyper-Schema Bildung (Story 13)
    Two semantically related schemas (centroid-cosine ≥ HYPER_SCHEMA_
    COHESION_THRESHOLD = 0.7) with **systematic property differences**
    are subsumed under a HyperSchema. Subschemas keep existing; the
    HyperSchema is a new node connected via ``(:Schema)-[:SPECIALIZES]->
    (:HyperSchema)``. Concept §13: schema-zu-hyper-schema is the only
    place R3 lives — engram-level property extraction is in C2 Story 07.

R5 — Schema Death (Story 14, this file)
    Long-inactive schemas with thin evidence get archived (status='archived')
    rather than deleted, so historical recalls still find them.

Bio mapping: REM-sleep schema-graph maintenance (concept §13 frequency
table). Both rules live in C3 because they reshape the cortex itself,
not the buffer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ..schema.centroid import compute_centroid
from ..schema.models import HyperSchemaModel, SchemaModel
from ..schema.schema_repository import archive_schema, create_schema, link_specialization, list_active_schemas
from .constants import (
    C3_CYCLE_PERIOD_DAYS,
    HYPER_SCHEMA_COHESION_THRESHOLD,
    HYPER_SCHEMA_MIN_PROPERTY_DIFF,
    R5_EVIDENCE_THRESHOLD,
    R5_K_CYCLES,
)

if TYPE_CHECKING:
    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HyperSchemaCandidate:
    """A pair of schemas that satisfy the R3 cohesion + property-diff gate."""

    left: SchemaModel
    right: SchemaModel
    cosine: float
    differing_keys: tuple[str, ...]


@dataclass
class R3Report:
    """Per-run summary surfaced by ``run_r3_hyper_schema``."""

    bank_id: str
    schemas_scanned: int = 0
    pairs_above_cosine: int = 0
    pairs_with_property_diff: int = 0
    hyper_schemas_created: int = 0
    candidates: list[HyperSchemaCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# R3 — Hyper-Schema candidate detection
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for already-L2-normalised vectors (centroids are unit).

    Falls back to raw dot-product divided by norms when one side isn't
    normalised — covers the rare case of legacy schemas that pre-date the
    L2-renorm policy.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _differing_property_keys(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, ...]:
    """Return the keys whose typed values differ between two property dicts.

    ``evidence_count`` is excluded — it's bookkeeping, not pattern shape.
    Numeric/temporal/categorical types are compared by their primary
    summary field (mean, range bounds, mode value).
    """
    differing: list[str] = []
    keys = (set(a.keys()) | set(b.keys())) - {"evidence_count"}
    for key in keys:
        if not (_summary(a.get(key)) == _summary(b.get(key))):
            differing.append(key)
    return tuple(sorted(differing))


def _summary(info: Any) -> Any:
    """Reduce a property-info dict to a comparable scalar/tuple.

    The aggregator (Story 07) emits dicts shaped per type; equality checks
    on the full dict are too strict (e.g. ``count`` may differ even when
    the ``value`` matches), so we compare only the discriminating field.
    """
    if not isinstance(info, dict):
        return info
    kind = info.get("type")
    if kind == "categorical":
        return ("categorical", info.get("value"))
    if kind == "numeric":
        return ("numeric", info.get("mean"))
    if kind == "temporal":
        return ("temporal", info.get("min_iso"), info.get("max_iso"))
    return ("unknown", repr(info))


async def _fetch_schema_centroids(
    qdrant: "QdrantEngineClient",
    schemas: list[SchemaModel],
) -> dict[UUID, list[float]]:
    """Pull centroid vectors for the given schemas in one Qdrant batch call.

    Schemas without a centroid (Qdrant→Neo4j drift) get dropped — the R3
    pairing skips them silently rather than crashing the whole run.
    """
    ids: list[str] = []
    by_qid: dict[str, UUID] = {}
    for schema in schemas:
        qid = schema.centroid_qdrant_id or schema.id
        ids.append(str(qid))
        by_qid[str(qid)] = schema.id
    if not ids:
        return {}
    points = await qdrant.retrieve_many(ids)
    centroids: dict[UUID, list[float]] = {}
    for point in points:
        qid = point.get("engram_id") or point.get("payload", {}).get("schema_id")
        vector = point.get("vector")
        if qid in by_qid and vector is not None:
            centroids[by_qid[qid]] = list(vector)
    return centroids


async def find_hyper_schema_candidates(
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    cohesion_threshold: float = HYPER_SCHEMA_COHESION_THRESHOLD,
    min_property_diff: int = HYPER_SCHEMA_MIN_PROPERTY_DIFF,
    limit: int = 200,
) -> list[HyperSchemaCandidate]:
    """Pairwise-scan active :Schema nodes for R3 hyper-schema candidates.

    O(N²) over the bank's active schemas; N is small in practice (per-bank
    schema counts are in the tens-to-low-hundreds). Pairs are deduplicated:
    each unordered pair appears at most once.
    """
    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit)
    schema_models: list[SchemaModel] = [s for s in schemas if isinstance(s, SchemaModel)]
    if len(schema_models) < 2:
        return []

    centroids = await _fetch_schema_centroids(qdrant, schema_models)
    candidates: list[HyperSchemaCandidate] = []
    for i, left in enumerate(schema_models):
        if left.id not in centroids:
            continue
        for j in range(i + 1, len(schema_models)):
            right = schema_models[j]
            if right.id not in centroids:
                continue
            cosine = _cosine(centroids[left.id], centroids[right.id])
            if cosine < cohesion_threshold:
                continue
            differing = _differing_property_keys(left.properties, right.properties)
            if len(differing) < min_property_diff:
                continue
            candidates.append(
                HyperSchemaCandidate(
                    left=left,
                    right=right,
                    cosine=cosine,
                    differing_keys=differing,
                )
            )
    return candidates


# ---------------------------------------------------------------------------
# R3 — Hyper-Schema creation
# ---------------------------------------------------------------------------


def _union_properties(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two schema property dicts into a hyper-schema property dict.

    - Same-type, same-summary → keep one copy (no information lost).
    - Same-type, different summary → broaden:
        * categorical: ``{type: "categorical_set", values: [...]}``.
        * numeric: union range — min(min), max(max), mean of means.
        * temporal: union interval — min(min_iso), max(max_iso).
    - Different types → categorical-set fallback (rare; mostly drift).
    - Missing on one side → keep the other.
    """
    out: dict[str, Any] = {}
    for key in (set(a.keys()) | set(b.keys())) - {"evidence_count"}:
        left = a.get(key)
        right = b.get(key)
        if left is None:
            out[key] = right
            continue
        if right is None:
            out[key] = left
            continue
        out[key] = _merge_property(left, right)
    out["evidence_count"] = int((a.get("evidence_count") or 0) + (b.get("evidence_count") or 0))
    return out


def _merge_property(left: Any, right: Any) -> Any:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left if left == right else {"type": "categorical_set", "values": [left, right]}
    lt = left.get("type")
    rt = right.get("type")
    if lt == rt == "categorical":
        if left.get("value") == right.get("value"):
            return {**left, "count": int(left.get("count", 0)) + int(right.get("count", 0))}
        return {"type": "categorical_set", "values": sorted({left.get("value"), right.get("value")})}
    if lt == rt == "numeric":
        mins = (left.get("min"), right.get("min"))
        maxs = (left.get("max"), right.get("max"))
        means = (float(left.get("mean", 0)), float(right.get("mean", 0)))
        return {
            "type": "numeric",
            "min": min(mins),
            "max": max(maxs),
            "mean": (means[0] + means[1]) / 2,
            "count": int(left.get("count", 0)) + int(right.get("count", 0)),
        }
    if lt == rt == "temporal":
        return {
            "type": "temporal",
            "min_iso": min(left.get("min_iso"), right.get("min_iso")),
            "max_iso": max(left.get("max_iso"), right.get("max_iso")),
            "count": int(left.get("count", 0)) + int(right.get("count", 0)),
        }
    # Mixed types — fall back to categorical-set of summaries.
    return {"type": "categorical_set", "values": [_summary(left), _summary(right)]}


async def create_hyper_schema(
    candidate: HyperSchemaCandidate,
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    schema_centroids: dict[UUID, list[float]],
) -> HyperSchemaModel:
    """Mint a HyperSchema from an R3 candidate pair and link it back.

    Centroid: mean of the two subschema centroids (L2-renormalised via
    ``compute_centroid``). Properties: union per type. Evidence_count:
    sum of subschemas. Description: simple template — the dedicated LLM
    pipeline-step (Story 08) is reserved for engram-level schemas; a
    hyper-schema's description can be regenerated later if needed.
    """
    left = candidate.left
    right = candidate.right
    left_centroid = schema_centroids.get(left.id)
    right_centroid = schema_centroids.get(right.id)
    if left_centroid is None or right_centroid is None:
        raise ValueError(f"create_hyper_schema needs centroids for both subschemas (left={left.id}, right={right.id})")

    centroid = compute_centroid([left_centroid, right_centroid])
    hyper_id = uuid4()
    now = datetime.now(timezone.utc)
    properties = _union_properties(left.properties, right.properties)
    description = (
        f"HyperSchema über {left.description!r} und {right.description!r} "
        f"(differing keys: {', '.join(candidate.differing_keys) or '—'})"
    )
    hyper = HyperSchemaModel(
        id=hyper_id,
        description=description,
        properties=properties,
        centroid_qdrant_id=hyper_id,
        evidence_engram_ids=[],
        evidence_count=int((left.evidence_count or 0) + (right.evidence_count or 0)),
        cycles_survived=1,
        status="active",
        created_at=now,
        last_reinforced_at=now,
    )

    await create_schema(neo4j, hyper, label="HyperSchema")
    try:
        await qdrant.upsert_schema_centroid(
            schema_id=str(hyper_id),
            centroid=centroid,
            schema_meta={"bank_id": bank_id, "kind_label": "HyperSchema"},
        )
    except Exception:
        logger.warning(
            "create_hyper_schema qdrant upsert failed bank=%s hyper_id=%s — neo4j node remains",
            bank_id,
            hyper_id,
        )

    for sub in (left, right):
        try:
            await link_specialization(neo4j, sub.id, hyper_id)
        except Exception:
            logger.warning(
                "create_hyper_schema link_specialization failed bank=%s hyper=%s sub=%s",
                bank_id,
                hyper_id,
                sub.id,
            )

    logger.info(
        "create_hyper_schema bank=%s hyper_id=%s subs=[%s, %s] cosine=%.4f differing=%d",
        bank_id,
        hyper_id,
        left.id,
        right.id,
        candidate.cosine,
        len(candidate.differing_keys),
    )
    return hyper


async def run_r3_hyper_schema(
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    cohesion_threshold: float = HYPER_SCHEMA_COHESION_THRESHOLD,
    min_property_diff: int = HYPER_SCHEMA_MIN_PROPERTY_DIFF,
) -> R3Report:
    """End-to-end R3 pass for a bank: find candidates → mint hyper-schemas.

    Per-pair failures are logged, not raised — best-effort C3 semantics
    matching C2. Returns a :class:`R3Report` for the orchestrator log.
    """
    schemas = await list_active_schemas(neo4j, label="Schema", limit=200)
    schema_models: list[SchemaModel] = [s for s in schemas if isinstance(s, SchemaModel)]
    report = R3Report(bank_id=bank_id, schemas_scanned=len(schema_models))
    if len(schema_models) < 2:
        logger.info("run_r3_hyper_schema bank=%s skipped — fewer than 2 schemas", bank_id)
        return report

    centroids = await _fetch_schema_centroids(qdrant, schema_models)
    used_ids: set[UUID] = set()
    for i, left in enumerate(schema_models):
        if left.id not in centroids or left.id in used_ids:
            continue
        for j in range(i + 1, len(schema_models)):
            right = schema_models[j]
            if right.id not in centroids or right.id in used_ids:
                continue
            cosine = _cosine(centroids[left.id], centroids[right.id])
            if cosine < cohesion_threshold:
                continue
            report.pairs_above_cosine += 1
            differing = _differing_property_keys(left.properties, right.properties)
            if len(differing) < min_property_diff:
                continue
            report.pairs_with_property_diff += 1
            candidate = HyperSchemaCandidate(left=left, right=right, cosine=cosine, differing_keys=differing)
            report.candidates.append(candidate)
            try:
                await create_hyper_schema(candidate, bank_id, neo4j=neo4j, qdrant=qdrant, schema_centroids=centroids)
            except Exception as exc:
                logger.warning(
                    "run_r3_hyper_schema create failed bank=%s reason=%s",
                    bank_id,
                    exc.__class__.__name__,
                )
                continue
            report.hyper_schemas_created += 1
            # Each subschema joins at most one hyper this run — keeps R3 stable.
            used_ids.add(left.id)
            used_ids.add(right.id)
            break

    logger.info(
        "run_r3_hyper_schema bank=%s schemas=%d above_cosine=%d with_diff=%d created=%d",
        bank_id,
        report.schemas_scanned,
        report.pairs_above_cosine,
        report.pairs_with_property_diff,
        report.hyper_schemas_created,
    )
    return report


# ---------------------------------------------------------------------------
# R5 — Schema Death (Story 14)
# ---------------------------------------------------------------------------


@dataclass
class R5Report:
    """Per-run summary surfaced by ``archive_dead_schemas``."""

    bank_id: str
    schemas_scanned: int = 0
    archived: int = 0
    archived_ids: list[UUID] = field(default_factory=list)


def cycles_since_reinforced(
    schema: SchemaModel,
    *,
    now: datetime | None = None,
    cycle_period_days: int = C3_CYCLE_PERIOD_DAYS,
) -> int:
    """Estimate how many C3 cycles a schema has gone without reinforcement.

    Schemas don't carry a per-bank C3-cycle counter today; we derive the
    cycle count from wallclock days since ``last_reinforced_at`` divided
    by the C3 period. ``last_reinforced_at`` defaults to ``created_at``
    on a fresh schema (Story 09), so this works from birth onwards.
    """
    if schema.last_reinforced_at is None:
        return 0
    reference = now or datetime.now(timezone.utc)
    delta = reference - schema.last_reinforced_at
    days = max(0, int(delta.total_seconds() // (24 * 3600)))
    return days // max(1, cycle_period_days)


async def archive_dead_schemas(
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    k_cycles: int = R5_K_CYCLES,
    evidence_threshold: int = R5_EVIDENCE_THRESHOLD,
    cycle_period_days: int = C3_CYCLE_PERIOD_DAYS,
    now: datetime | None = None,
    limit: int = 500,
) -> R5Report:
    """Archive schemas that satisfy the R5 death gate (concept §13).

    Death triggers when **both** are true:
        - ``cycles_since_reinforced > k_cycles`` (long inactive)
        - ``evidence_count < evidence_threshold`` (thin support)

    Bootstrap defaults are intentionally lenient (K=8 ≈ 2 months,
    threshold=3) — early-life banks shouldn't lose schemas just because
    they're triggered rarely. Once a system has dense banks the values
    can be tightened toward concept-default K=4 / threshold=5.

    Schemas are **archived** (status='archived'), not deleted: historical
    recalls still find them; HybridRetriever (Story 15) excludes
    archived from active search.
    """
    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit)
    schema_models: list[SchemaModel] = [s for s in schemas if isinstance(s, SchemaModel)]
    report = R5Report(bank_id=bank_id, schemas_scanned=len(schema_models))

    for schema in schema_models:
        cycles = cycles_since_reinforced(schema, now=now, cycle_period_days=cycle_period_days)
        if cycles <= k_cycles:
            continue
        if int(schema.evidence_count or 0) >= evidence_threshold:
            continue
        try:
            await archive_schema(neo4j, schema.id, label="Schema")
        except Exception as exc:
            logger.warning(
                "archive_dead_schemas failed bank=%s schema_id=%s reason=%s",
                bank_id,
                schema.id,
                exc.__class__.__name__,
            )
            continue
        report.archived += 1
        report.archived_ids.append(schema.id)
        logger.info(
            "archive_dead_schemas archived bank=%s schema_id=%s cycles=%d evidence=%d",
            bank_id,
            schema.id,
            cycles,
            schema.evidence_count or 0,
        )

    logger.info(
        "archive_dead_schemas bank=%s scanned=%d archived=%d (k=%d, threshold=%d)",
        bank_id,
        report.schemas_scanned,
        report.archived,
        k_cycles,
        evidence_threshold,
    )
    return report
