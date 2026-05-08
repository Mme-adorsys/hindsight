"""Schema reconsolidation — Recall-time lability for Schema hits (Epic 25 Story 21).

Mirrors the engram-side reconsolidation pipeline (Epic 10): every Recall
reactivates the schema's representation and opens a brief lability window
during which mode-specific updates can land. The four modes map to
escalating commit levels:

    Precision   → access_count++ + last_accessed=now (touch only)
    Exploration → +Property-Refresh from the current Top-N evidence
    Analogy     → +Hyper-Schema linking hint (R3 fast-path candidate)
    Validation  → +Centroid drift towards the query embedding when a
                  prediction error fires (α=SCHEMA_CENTROID_DRIFT_ALPHA)

Bio mapping: Reconsolidation Window — every Recall reactivates the
schema-encoding pattern, briefly making it labile (LTP/LTD plasticity in
neocortical assemblies). Concept §10 (Reflect) + §4.2 (schema fields).

LLM-free; the property-aggregation path reuses the deterministic
``aggregate_properties`` helper from C2.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..consolidation.constants import (
    MAX_SCHEMA_DRIFTS_PER_DAY,
    SCHEMA_CENTROID_DRIFT_ALPHA,
)
from ..consolidation.property_aggregator import aggregate_properties
from ..db_utils import acquire_with_retry
from ..response_models import RetrievalMode
from ..schema.models import _serialise_props_for_neo4j
from ..schema.schema_repository import get_schema as _get
from ..schema.schema_repository import update_schema
from ..utils import fq_table

if TYPE_CHECKING:
    from ..schema.models import _SchemaBase
    from ..search.evidence_resolver import EvidenceEngram
    from ..search.hybrid_retriever import RetrievalHit

logger = logging.getLogger(__name__)


def drift_centroid(
    old: list[float],
    query: list[float],
    alpha: float = SCHEMA_CENTROID_DRIFT_ALPHA,
) -> list[float]:
    """Nudge ``old`` towards ``query`` by ``alpha`` and re-normalise to unit length.

    Returns ``(1-α) · old + α · query`` re-normalised. Empty / mismatched
    inputs raise — silent fallback would mask a wiring bug. Zero-norm
    (extremely unlikely after a real recall) returns the unchanged ``old``.
    """
    if not old or not query:
        raise ValueError("drift_centroid requires non-empty centroid + query embeddings")
    if len(old) != len(query):
        raise ValueError(f"centroid/query dimension mismatch: {len(old)} vs {len(query)}")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha out of [0, 1] range: {alpha}")

    blended = [(1.0 - alpha) * o + alpha * q for o, q in zip(old, query, strict=True)]
    norm = sum(v * v for v in blended) ** 0.5
    if norm == 0.0:
        return list(old)
    return [v / norm for v in blended]


def _refresh_properties(evidence: list["EvidenceEngram"]) -> dict[str, Any]:
    """Re-run the C2 deterministic aggregator over the resolved Top-N evidence."""
    if not evidence:
        return {}
    return aggregate_properties([list(e.tags or []) for e in evidence])


def _hash_query(embedding: list[float]) -> str:
    """Stable short hash for the audit row (no secrecy goal — just dedup)."""
    digest = hashlib.blake2b(digest_size=8)
    for v in embedding:
        digest.update(format(v, ".6f").encode("ascii"))
    return digest.hexdigest()


def _throttle_check(current: "_SchemaBase", *, now: datetime) -> tuple[int, datetime | None, bool]:
    """Apply the rolling 24h reset and decide whether the next drift can fire.

    Returns ``(new_count, new_last_drifted_at, allowed)``. The reset is
    triggered when the most recent drift is older than 24h — the counter
    drops back to zero before the throttle check, so a quiet day fully
    refills the budget.
    """
    last = current.last_drifted_at
    new_count = current.drift_count
    new_last = last
    if last is None or (now - last) > timedelta(days=1):
        new_count = 0
        new_last = None
    allowed = new_count < MAX_SCHEMA_DRIFTS_PER_DAY
    return new_count, new_last, allowed


async def _persist_drift_event(
    pool: Any,
    *,
    bank_id: str,
    schema_id: Any,
    alpha: float,
    query_hash: str,
    mode: RetrievalMode,
) -> None:
    """Insert one row into ``schema_drift_events`` — best-effort audit log."""
    sql = (
        f"INSERT INTO {fq_table('schema_drift_events')} "
        f"(bank_id, schema_id, alpha, query_hash, mode) VALUES ($1, $2, $3, $4, $5)"
    )
    try:
        async with acquire_with_retry(pool) as conn:
            await conn.execute(
                sql,
                bank_id,
                str(schema_id),
                float(alpha),
                query_hash,
                mode.value if isinstance(mode, RetrievalMode) else str(mode),
            )
    except Exception as exc:  # noqa: BLE001 — audit write must not break recall
        logger.warning("[SchemaReconsolidation] drift audit write failed for %s: %s", schema_id, exc)


async def reconsolidate_schema_hit(
    hit: "RetrievalHit",
    *,
    neo4j: Any,
    mode: RetrievalMode,
    evidence: list["EvidenceEngram"] | None = None,
    query_embedding: list[float] | None = None,
    prediction_error: bool = False,
    alpha: float = SCHEMA_CENTROID_DRIFT_ALPHA,
    qdrant: Any | None = None,
    pool: Any | None = None,
    bank_id: str | None = None,
) -> "_SchemaBase | None":
    """Apply mode-specific reconsolidation to a Schema RetrievalHit.

    When ``pool`` and ``bank_id`` are supplied the Validation-drift branch
    additionally writes a row into ``schema_drift_events`` (Story 22 audit
    trail) and rolls the throttle: at most ``MAX_SCHEMA_DRIFTS_PER_DAY``
    drifts may fire within a 24h window. The counter resets when the
    oldest drift is older than 24h.

    Returns the updated SchemaModel, or ``None`` when the hit is an engram
    (no-op), the schema does not exist, or the persistence step fails.
    Reconsolidation is best-effort — a failed update logs a warning but
    does not raise into the recall path.
    """
    if hit.kind != "schema":
        return None

    label = hit.schema_label or "Schema"
    now = datetime.now(timezone.utc)
    # Read-modify-write: update_schema's SET semantics can't increment atomically
    # without a custom Cypher path, so we resolve the current count first.
    current = await _get(neo4j, hit.id, label=label)
    if current is None:
        logger.debug("[SchemaReconsolidation] schema %s missing — skipping", hit.id)
        return None

    partial: dict[str, Any] = {
        "access_count": current.access_count + 1,
        "last_accessed": now.isoformat(),
    }

    # Mode-specific extras
    if mode == RetrievalMode.EXPLORATION and evidence:
        new_props = _refresh_properties(evidence)
        if new_props:
            partial["properties_json"] = _serialise_props_for_neo4j(new_props)

    drift_fired = False
    if mode == RetrievalMode.VALIDATION and prediction_error and query_embedding and qdrant is not None:
        # Story 22 — rolling 24h throttle. Decide *before* touching Qdrant.
        windowed_count, windowed_last, allowed = _throttle_check(current, now=now)
        if not allowed:
            logger.info(
                "[SchemaReconsolidation] schema %s drift throttled — %d/%d in last 24h",
                hit.id,
                windowed_count,
                MAX_SCHEMA_DRIFTS_PER_DAY,
            )
        else:
            try:
                point = await qdrant.get_by_id(str(hit.id))
                if point is not None and point.get("vector"):
                    drifted = drift_centroid(list(point["vector"]), query_embedding, alpha=alpha)
                    payload = point.get("payload") or {}
                    await qdrant.upsert_schema_centroid(
                        schema_id=str(hit.id),
                        centroid=drifted,
                        schema_meta=payload,
                    )
                    drift_fired = True
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("[SchemaReconsolidation] centroid drift failed for %s: %s", hit.id, exc)

        if drift_fired:
            partial["drift_count"] = windowed_count + 1
            partial["last_drifted_at"] = now.isoformat()
            if pool is not None and bank_id is not None:
                await _persist_drift_event(
                    pool,
                    bank_id=bank_id,
                    schema_id=hit.id,
                    alpha=alpha,
                    query_hash=_hash_query(query_embedding or []),
                    mode=mode,
                )
        elif windowed_last != current.last_drifted_at:
            # 24h window rolled — flush the stale state even if no new drift.
            partial["drift_count"] = windowed_count
            partial["last_drifted_at"] = None

    if mode == RetrievalMode.ANALOGY:
        # Hyper-Schema linking hint — exposed as a transient log line, the
        # actual R3 promotion runs on the C3 cadence. No persistent state
        # change for now beyond the access_count touch.
        logger.debug("[SchemaReconsolidation] analogy hit — schema %s flagged for next R3 sweep", hit.id)

    try:
        updated = await update_schema(neo4j, hit.id, partial, label=label)
    except Exception as exc:
        logger.warning("[SchemaReconsolidation] update_schema failed for %s: %s", hit.id, exc)
        return None
    if updated is None:
        return None
    logger.debug(
        "[SchemaReconsolidation] schema=%s mode=%s access_count=%d → %d drift_fired=%s",
        hit.id,
        mode.value if isinstance(mode, RetrievalMode) else mode,
        current.access_count,
        updated.access_count,
        drift_fired,
    )
    return updated
