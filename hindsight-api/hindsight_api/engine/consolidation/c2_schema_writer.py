"""Schema persistence for C2 (Epic 25 Story 09).

End of the C2 creation pipeline: a matured cluster that didn't match an
existing schema (Story 06) and got Properties (Story 07) + Description
(Story 08) is now committed atomically into Neo4j (`:Schema` node) and
Qdrant (centroid point with `payload.kind="schema"`).

Concept §4.2 — Schema has three parallel representations bound by the
same UUID: Centroid (Qdrant) for vector match, Description (Neo4j prop)
for humans/LLM, Properties (Neo4j prop) for structured queries. They
must be written together; partial writes leave the cortex inconsistent.

Atomicity model: write Neo4j first (cheap to roll back via
``archive_schema``), then Qdrant. If Qdrant fails the schema is archived
so HybridRetriever (Story 15) won't surface a phantom node — the row
isn't deleted because R5 Schema Death (Story 14) is the canonical
removal path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from ..db_utils import acquire_with_retry
from ..schema.models import SchemaModel
from ..schema.schema_repository import archive_schema, create_schema, update_schema
from .constants import SCHEMA_TOP_N_EVIDENCE
from .property_aggregator import aggregate_properties

if TYPE_CHECKING:
    import asyncpg

    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient
    from .c2_pattern_recognition import CreationPayload, MatchedForReinforcement

logger = logging.getLogger(__name__)


async def select_top_n_evidence(
    pool: "asyncpg.Pool",
    engram_ids: list[str],
    *,
    n: int = SCHEMA_TOP_N_EVIDENCE,
) -> list[UUID]:
    """Pick the strongest ``n`` engrams from a cluster (Indexing Theory pointer set).

    Strength = ``engram_dictionary.strength`` which since Epic 24 Story 03
    holds the composite score ``thalamus_overall * decay``. Highest-scoring
    members make the cut so the schema's audit trail points at the
    representative episodes, not at the noisiest ones.

    Returns ``list[UUID]`` ordered by descending strength. Missing ids are
    silently dropped — they may have been archived between R1 and persist.
    """
    if not engram_ids or n <= 0:
        return []
    query = """
        SELECT engram_id, COALESCE(strength, 0.0) AS strength
        FROM engram_dictionary
        WHERE engram_id = ANY($1::uuid[])
        ORDER BY strength DESC
        LIMIT $2
    """
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(query, [UUID(eid) for eid in engram_ids], n)
    return [row["engram_id"] for row in rows]


async def persist_new_schema(
    payload: "CreationPayload",
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> SchemaModel:
    """Create the :Schema node and its Qdrant centroid atomically.

    Returns the persisted ``SchemaModel`` (with the freshly minted ``id``
    and the ``evidence_engram_ids`` selection). On Qdrant failure the
    Neo4j node is archived (status='archived') so retrieval ignores it.
    """
    schema_id = uuid4()
    now = datetime.now(timezone.utc)
    evidence_ids = await select_top_n_evidence(pool, list(payload.cluster.engram_ids))

    model = SchemaModel(
        id=schema_id,
        description=payload.description,
        properties=dict(payload.properties),
        centroid_qdrant_id=schema_id,
        evidence_engram_ids=evidence_ids,
        evidence_count=int(payload.properties.get("evidence_count", len(payload.cluster.engram_ids)) or 0),
        cycles_survived=1,
        status="active",
        created_at=now,
        last_reinforced_at=now,
    )

    try:
        await create_schema(neo4j, model, label="Schema")
    except Exception:
        logger.exception("persist_new_schema neo4j create failed bank=%s schema_id=%s", bank_id, schema_id)
        raise

    try:
        await qdrant.upsert_schema_centroid(
            schema_id=str(schema_id),
            centroid=list(payload.cluster.centroid),
            schema_meta={"bank_id": bank_id, "description_short": _short(payload.description)},
        )
    except Exception:
        logger.error(
            "persist_new_schema qdrant upsert failed bank=%s schema_id=%s — archiving neo4j node",
            bank_id,
            schema_id,
        )
        try:
            await archive_schema(neo4j, schema_id, label="Schema")
        except Exception:
            logger.exception("persist_new_schema archive-fallback also failed bank=%s schema_id=%s", bank_id, schema_id)
        raise

    logger.info(
        "persist_new_schema bank=%s schema_id=%s evidence=%d properties=%d",
        bank_id,
        schema_id,
        len(evidence_ids),
        sum(1 for k in model.properties if k != "evidence_count"),
    )
    return model


async def persist_creation_payloads(
    payloads: tuple["CreationPayload", ...],
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> list[SchemaModel]:
    """Run :func:`persist_new_schema` per payload, sequentially.

    Sequential because per-bank Schema-ID space is small and Neo4j+Qdrant
    write contention is real; parallelism here would create a thundering
    herd against the cortex. Per-payload failures are logged but don't
    abort the batch — best-effort semantics matching the rest of C2.
    """
    persisted: list[SchemaModel] = []
    for payload in payloads:
        try:
            schema = await persist_new_schema(payload, bank_id, neo4j=neo4j, qdrant=qdrant, pool=pool)
        except Exception as exc:
            logger.warning(
                "persist_creation_payloads skipping payload bank=%s reason=%s",
                bank_id,
                exc.__class__.__name__,
            )
            continue
        persisted.append(schema)
    logger.info(
        "persist_creation_payloads bank=%s requested=%d persisted=%d",
        bank_id,
        len(payloads),
        len(persisted),
    )
    return persisted


def _short(text: str, max_chars: int = 80) -> str:
    """Truncate ``text`` for the Qdrant payload (debug-render only)."""
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


# ---------------------------------------------------------------------------
# Story 10 — R4 batch reinforcement
# ---------------------------------------------------------------------------


def weighted_centroid(
    old_centroid: list[float],
    old_weight: int,
    new_centroid: list[float],
    new_weight: int,
) -> list[float]:
    """Running weighted mean of two centroid vectors (concept §13 R4 step 3).

    Both centroids are assumed L2-normalised. The weighted mean is renormalised
    so the schema's centroid stays a unit vector — Qdrant uses cosine, and a
    drifting magnitude would silently bias future fingerprint matches.
    """
    if old_weight < 0 or new_weight < 0:
        raise ValueError("Weights must be non-negative")
    total = old_weight + new_weight
    if total == 0:
        raise ValueError("Cannot mean two zero-weighted centroids")
    if len(old_centroid) != len(new_centroid):
        raise ValueError("Centroid dimensions disagree")

    blended = [(old_centroid[i] * old_weight + new_centroid[i] * new_weight) / total for i in range(len(old_centroid))]
    norm = sum(x * x for x in blended) ** 0.5
    if norm == 0.0:
        raise ValueError("Weighted centroid collapsed to zero — input vectors cancel")
    return [x / norm for x in blended]


async def _fetch_member_tags(pool: "asyncpg.Pool", engram_ids: list[UUID]) -> list[list[str]]:
    """Fetch the per-engram ``tags`` arrays for a list of ids (Story 10 property refresh).

    Order of the result mirrors the order PostgreSQL returns; the aggregator
    is order-insensitive so we don't bother realigning to ``engram_ids``.
    Missing rows simply contribute fewer member-tag arrays — the aggregator
    handles short inputs gracefully (Story 07 edge case).
    """
    if not engram_ids:
        return []
    query = "SELECT COALESCE(tags, ARRAY[]::text[]) AS tags FROM engram_dictionary WHERE engram_id = ANY($1::uuid[])"
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(query, engram_ids)
    return [list(row["tags"] or ()) for row in rows]


async def reinforce_schema(
    matched: "MatchedForReinforcement",
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> SchemaModel:
    """Absorb a matured cluster into an existing schema (concept §13 R4).

    Steps mirror the concept text:
        1. ``evidence_count += cluster.size``
        2. Top-N refresh from PG (old top-N + new cluster ids → strongest 5)
        3. Centroid via :func:`weighted_centroid` (old × evidence_count + new × cluster.size)
        4. Properties re-aggregated over the new Top-N's tags (PG fetch)
        5. ``last_reinforced_at = now``; ``cycles_survived += 1``

    Persistence saga: Neo4j ``update_schema`` first; on Qdrant failure no
    rollback is attempted because update is idempotent and the Neo4j row
    still reflects a valid schema (just without the centroid refresh) —
    R4 will reconcile on the next match.
    """
    schema = matched.schema
    cluster = matched.cluster
    cluster_size = len(cluster.engram_ids)
    if cluster_size == 0:
        # Nothing to reinforce — return schema untouched.
        return schema

    candidate_ids = [str(eid) for eid in schema.evidence_engram_ids] + list(cluster.engram_ids)
    new_top_n = await select_top_n_evidence(pool, candidate_ids)
    new_evidence_count = int(schema.evidence_count) + cluster_size

    old_centroid = await _fetch_schema_centroid(qdrant, schema)
    if old_centroid is None or int(schema.evidence_count) <= 0:
        # Schema has no recorded centroid (orphan / archived / never-stored)
        # — bootstrap with the cluster's centroid as if the schema were fresh.
        new_centroid = list(cluster.centroid)
    else:
        new_centroid = weighted_centroid(
            old_centroid=old_centroid,
            old_weight=int(schema.evidence_count),
            new_centroid=list(cluster.centroid),
            new_weight=cluster_size,
        )

    refresh_tags = await _fetch_member_tags(pool, new_top_n)
    new_properties = aggregate_properties(refresh_tags) if refresh_tags else dict(schema.properties)

    now = datetime.now(timezone.utc)
    updated = SchemaModel(
        id=schema.id,
        description=schema.description,
        properties=new_properties,
        centroid_qdrant_id=schema.centroid_qdrant_id or schema.id,
        evidence_engram_ids=new_top_n,
        evidence_count=new_evidence_count,
        cycles_survived=int(schema.cycles_survived) + 1,
        status=schema.status or "active",
        created_at=schema.created_at,
        last_reinforced_at=now,
        # Carry over recall-time counters so reinforcement doesn't blank
        # the Story-21 reconsolidation state.
        access_count=int(schema.access_count),
        last_accessed=schema.last_accessed,
        # Story 22 — fresh evidence makes the schema "real" again, so the
        # drift budget resets. The next Validation-mode recall can start
        # a new 24h window.
        drift_count=0,
        last_drifted_at=None,
    )

    # update_schema rejects mutation of the immutable identity fields, so
    # strip them before handing over the freshly-built props dict.
    _props = updated.to_neo4j_props()
    for _k in ("id", "created_at"):
        _props.pop(_k, None)
    await update_schema(neo4j, schema.id, _props, label="Schema")

    try:
        await qdrant.upsert_schema_centroid(
            schema_id=str(schema.id),
            centroid=new_centroid,
            schema_meta={"bank_id": bank_id, "description_short": _short(updated.description)},
        )
    except Exception:
        # Idempotent update — leave Neo4j as-is and let the next R4 match
        # reconcile the centroid. Logging only.
        logger.warning(
            "reinforce_schema qdrant centroid refresh failed bank=%s schema_id=%s — neo4j stays updated",
            bank_id,
            schema.id,
        )

    logger.info(
        "reinforce_schema bank=%s schema_id=%s evidence_count=%d→%d cycles=%d",
        bank_id,
        schema.id,
        schema.evidence_count,
        new_evidence_count,
        updated.cycles_survived,
    )
    return updated


async def _fetch_schema_centroid(qdrant: "QdrantEngineClient", schema: SchemaModel) -> list[float] | None:
    """Pull the schema's centroid vector from Qdrant by ``centroid_qdrant_id``.

    SchemaModel doesn't carry the vector itself — only the point id — so
    reinforcement has to round-trip Qdrant once. Returns None when the
    point is missing (Qdrant→Neo4j drift) so the caller can bootstrap
    with the new cluster's centroid.
    """
    qid = schema.centroid_qdrant_id or schema.id
    try:
        point = await qdrant.get_by_id(str(qid))
    except Exception:
        logger.warning(
            "reinforce_schema centroid lookup failed schema_id=%s — bootstrapping",
            schema.id,
        )
        return None
    if point is None or point.get("vector") is None:
        return None
    return list(point["vector"])


async def reinforce_schema_single_engram(
    schema: SchemaModel,
    bank_id: str,
    *,
    engram_id: str,
    embedding: list[float],
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
    refresh_properties: bool | None = None,
) -> SchemaModel:
    """Single-engram R4 reinforcement (Epic 25 Story 12, retain-time path).

    Lighter than :func:`reinforce_schema` because the input is exactly one
    engram, not a cluster:

    - ``evidence_count += 1``
    - Top-N reshuffle: append the new engram and re-pick the strongest
      ``SCHEMA_TOP_N_EVIDENCE`` from PG (so a stronger newcomer can knock
      a weaker incumbent out)
    - Centroid: weighted mean with new engram weight = 1
    - ``last_reinforced_at = now``, ``cycles_survived += 1``
    - Property refresh defaults off (R4_INCREMENTAL_PROPERTY_REFRESH);
      one engram barely moves the aggregation, batch R4 (Story 10)
      handles it properly.
    """
    from .constants import R4_INCREMENTAL_PROPERTY_REFRESH

    do_refresh = R4_INCREMENTAL_PROPERTY_REFRESH if refresh_properties is None else refresh_properties

    candidate_ids = [str(eid) for eid in schema.evidence_engram_ids] + [engram_id]
    new_top_n = await select_top_n_evidence(pool, candidate_ids)
    new_evidence_count = int(schema.evidence_count) + 1

    old_centroid = await _fetch_schema_centroid(qdrant, schema)
    if old_centroid is None or int(schema.evidence_count) <= 0:
        new_centroid = list(embedding)
    else:
        new_centroid = weighted_centroid(
            old_centroid=old_centroid,
            old_weight=int(schema.evidence_count),
            new_centroid=list(embedding),
            new_weight=1,
        )

    if do_refresh:
        refresh_tags = await _fetch_member_tags(pool, new_top_n)
        new_properties = aggregate_properties(refresh_tags) if refresh_tags else dict(schema.properties)
    else:
        new_properties = dict(schema.properties)

    now = datetime.now(timezone.utc)
    updated = SchemaModel(
        id=schema.id,
        description=schema.description,
        properties=new_properties,
        centroid_qdrant_id=schema.centroid_qdrant_id or schema.id,
        evidence_engram_ids=new_top_n,
        evidence_count=new_evidence_count,
        cycles_survived=int(schema.cycles_survived) + 1,
        status=schema.status or "active",
        created_at=schema.created_at,
        last_reinforced_at=now,
        # Carry over recall-time counters so reinforcement doesn't blank
        # the Story-21 reconsolidation state.
        access_count=int(schema.access_count),
        last_accessed=schema.last_accessed,
        # Story 22 — fresh evidence makes the schema "real" again, so the
        # drift budget resets. The next Validation-mode recall can start
        # a new 24h window.
        drift_count=0,
        last_drifted_at=None,
    )

    # update_schema rejects mutation of the immutable identity fields, so
    # strip them before handing over the freshly-built props dict.
    _props = updated.to_neo4j_props()
    for _k in ("id", "created_at"):
        _props.pop(_k, None)
    await update_schema(neo4j, schema.id, _props, label="Schema")

    try:
        await qdrant.upsert_schema_centroid(
            schema_id=str(schema.id),
            centroid=new_centroid,
            schema_meta={"bank_id": bank_id, "description_short": _short(updated.description)},
        )
    except Exception:
        logger.warning(
            "reinforce_schema_single_engram qdrant centroid refresh failed bank=%s schema_id=%s — neo4j stays updated",
            bank_id,
            schema.id,
        )

    logger.info(
        "reinforce_schema_single_engram bank=%s schema_id=%s engram_id=%s evidence_count=%d→%d",
        bank_id,
        schema.id,
        engram_id,
        schema.evidence_count,
        new_evidence_count,
    )
    return updated


async def reinforce_matched(
    reinforcement: tuple["MatchedForReinforcement", ...],
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> list[SchemaModel]:
    """Sequentially run :func:`reinforce_schema` per matched candidate.

    Sequential for the same reason as ``persist_creation_payloads`` — limit
    write contention against Neo4j+Qdrant. Per-item failures are logged
    but don't abort the batch (best-effort C2 semantics).
    """
    reinforced: list[SchemaModel] = []
    for item in reinforcement:
        try:
            schema = await reinforce_schema(item, bank_id, neo4j=neo4j, qdrant=qdrant, pool=pool)
        except Exception as exc:
            logger.warning(
                "reinforce_matched skipping schema=%s bank=%s reason=%s",
                getattr(item.schema, "id", "?"),
                bank_id,
                exc.__class__.__name__,
            )
            continue
        reinforced.append(schema)
    logger.info(
        "reinforce_matched bank=%s requested=%d reinforced=%d",
        bank_id,
        len(reinforcement),
        len(reinforced),
    )
    return reinforced
