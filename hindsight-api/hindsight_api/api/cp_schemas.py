"""Control Plane Schema-Explorer endpoints (Epic 25 Story 27).

Backend for the Schema-Explorer view in the Control Plane UI. Reads
exclusively from the new CLS-side data:

  - Neo4j ``:Schema`` / ``:HyperSchema`` nodes (description, properties,
    evidence_engram_ids, evidence_count, cycles_survived, status,
    last_reinforced_at, access_count, drift_count, …)
  - Qdrant centroid points (kind="schema") for the optional 2D scatter
  - PostgreSQL ``memory_units`` ⨝ ``engram_dictionary`` for the Top-N
    evidence engram resolution behind a Schema hit

The legacy CP API (Epic 22) read engram-with-layer='neocortex' rows;
that path is gone. Endpoints mount under ``/v1/cp`` from
``api/http.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..engine.qdrant_client import QdrantEngineClient
from ..engine.schema.models import HyperSchemaModel, SchemaModel
from ..engine.schema.schema_repository import (
    get_schema as get_schema_node,
)
from ..engine.schema.schema_repository import (
    list_active_schemas,
)
from ..engine.search.evidence_resolver import (
    EvidenceEngram,
    fetch_active_engrams_by_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/cp", tags=["Control Plane — Schemas"])

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class SchemaListItemDTO(BaseModel):
    """Compact row for the explorer's master list."""

    id: UUID
    description: str = ""
    evidence_count: int = 0
    cycles_survived: int = 0
    status: str = "active"
    last_reinforced_at: datetime | None = None
    confidence_tier: str | None = None  # Story 24/25 — exposed if present


class SchemaDetailDTO(SchemaListItemDTO):
    """Full schema view including freeform properties + evidence pointers."""

    properties: dict[str, Any] = Field(default_factory=dict)
    evidence_engram_ids: list[UUID] = Field(default_factory=list)
    centroid_qdrant_id: UUID | None = None
    access_count: int = 0
    last_accessed: datetime | None = None
    drift_count: int = 0
    last_drifted_at: datetime | None = None
    centroid: list[float] | None = None  # only when ?include_centroid=true


class EvidenceEngramDTO(BaseModel):
    """Top-N evidence engram resolved from PostgreSQL."""

    id: UUID
    text: str
    fact_type: str | None = None
    context: str | None = None
    strength: float | None = None
    tags: list[str] = Field(default_factory=list)


class HyperSchemaDTO(BaseModel):
    """HyperSchema row plus the ids of its specialised children."""

    id: UUID
    description: str = ""
    evidence_count: int = 0
    cycles_survived: int = 0
    status: str = "active"
    last_reinforced_at: datetime | None = None
    children_ids: list[UUID] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class CentroidPoint2DDTO(BaseModel):
    """One projected centroid for the 2-D scatter plot."""

    schema_id: UUID
    description: str = ""
    x: float
    y: float
    cluster_label: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_item(model: SchemaModel | HyperSchemaModel) -> SchemaListItemDTO:
    tier = model.properties.get("confidence_tier") if model.properties else None
    return SchemaListItemDTO(
        id=model.id,
        description=model.description,
        evidence_count=model.evidence_count,
        cycles_survived=model.cycles_survived,
        status=model.status,
        last_reinforced_at=model.last_reinforced_at,
        confidence_tier=tier if isinstance(tier, str) else None,
    )


def _detail(model: SchemaModel | HyperSchemaModel, *, centroid: list[float] | None = None) -> SchemaDetailDTO:
    base = _list_item(model)
    return SchemaDetailDTO(
        **base.model_dump(),
        properties=dict(model.properties),
        evidence_engram_ids=list(model.evidence_engram_ids),
        centroid_qdrant_id=model.centroid_qdrant_id,
        access_count=model.access_count,
        last_accessed=model.last_accessed,
        drift_count=model.drift_count,
        last_drifted_at=model.last_drifted_at,
        centroid=centroid,
    )


def _evidence_dto(evidence_engram: EvidenceEngram) -> EvidenceEngramDTO:
    return EvidenceEngramDTO(
        id=evidence_engram.id,
        text=evidence_engram.text,
        fact_type=evidence_engram.fact_type,
        context=evidence_engram.context,
        strength=evidence_engram.strength,
        tags=list(evidence_engram.tags),
    )


def _filter_bank(schemas: list, bank_id: str) -> list:
    """Filter Neo4j schemas by bank_id stamped in `properties.bank_id` or
    `properties.source_bank_ids`. The legacy bank-tagging convention is to
    stamp the bank into the centroid Qdrant payload; here we cheaply
    pre-filter on the Neo4j side using whichever property carries it.

    Schemas without any bank stamp are returned unchanged — single-bank
    setups don't need the filter and the explorer just shows everything.
    """
    out = []
    for s in schemas:
        props = s.properties if isinstance(s.properties, dict) else {}
        if not props:
            out.append(s)
            continue
        if props.get("bank_id") == bank_id:
            out.append(s)
            continue
        sources = props.get("source_bank_ids") or []
        if isinstance(sources, list) and bank_id in sources:
            out.append(s)
            continue
        if "bank_id" not in props and not sources:
            out.append(s)
    return out


def _qdrant(request: Request) -> QdrantEngineClient | None:
    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant is None:
        return None
    return qdrant


def _neo4j(request: Request) -> Any:
    neo4j = getattr(request.app.state, "neo4j", None)
    if neo4j is None:
        raise HTTPException(status_code=503, detail="Neo4j client not initialised")
    return neo4j


def _pool(request: Request) -> Any:
    memory = getattr(request.app.state, "memory", None)
    pool = getattr(memory, "_pool", None) if memory is not None else None
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialised")
    return pool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/schemas",
    response_model=list[SchemaListItemDTO],
    summary="List active Schemas for a bank",
    description="Returns one row per active :Schema node in the bank.",
)
async def list_bank_schemas(
    bank_id: str,
    request: Request,
    status: str = Query("active", pattern="^(active|archived|all)$"),
    sort_by: str = Query(
        "last_reinforced_at",
        pattern="^(last_reinforced_at|evidence_count|cycles_survived)$",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[SchemaListItemDTO]:
    neo4j = _neo4j(request)
    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit + offset)
    if status != "active":
        # list_active_schemas hard-codes status='active'; for archived/all
        # we'd need a dedicated query — Story 19/20 scope keeps it simple.
        if status == "archived":
            schemas = []
    schemas = _filter_bank(schemas, bank_id)
    if sort_by == "evidence_count":
        schemas.sort(key=lambda s: s.evidence_count, reverse=True)
    elif sort_by == "cycles_survived":
        schemas.sort(key=lambda s: s.cycles_survived, reverse=True)
    # last_reinforced_at is the default order from the repository helper.
    page = schemas[offset : offset + limit]
    return [_list_item(s) for s in page]


@router.get(
    "/schemas/{schema_id}",
    response_model=SchemaDetailDTO,
    summary="Schema detail",
)
async def get_schema_detail(
    schema_id: UUID,
    request: Request,
    include_centroid: bool = Query(False),
) -> SchemaDetailDTO:
    neo4j = _neo4j(request)
    model = await get_schema_node(neo4j, schema_id, label="Schema")
    if model is None:
        # Try HyperSchema as fallback — the explorer doesn't know the label
        # from the URL alone.
        model = await get_schema_node(neo4j, schema_id, label="HyperSchema")
    if model is None:
        raise HTTPException(status_code=404, detail=f"Schema {schema_id} not found")

    centroid: list[float] | None = None
    if include_centroid:
        qdrant = _qdrant(request)
        cid = model.centroid_qdrant_id
        if qdrant is not None and cid is not None:
            try:
                point = await qdrant.get_by_id(str(cid))
                if point and point.get("vector"):
                    centroid = list(point["vector"])
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("[CP] centroid fetch failed schema=%s: %s", schema_id, exc)

    return _detail(model, centroid=centroid)


@router.get(
    "/schemas/{schema_id}/evidence",
    response_model=list[EvidenceEngramDTO],
    summary="Top-N evidence engrams for a schema",
)
async def get_schema_evidence(
    schema_id: UUID,
    request: Request,
    bank_id: str,
    max_n: int = Query(5, ge=1, le=50),
) -> list[EvidenceEngramDTO]:
    neo4j = _neo4j(request)
    pool = _pool(request)
    model = await get_schema_node(neo4j, schema_id, label="Schema")
    if model is None:
        raise HTTPException(status_code=404, detail=f"Schema {schema_id} not found")
    if not model.evidence_engram_ids:
        return []
    rows = await fetch_active_engrams_by_ids(pool, model.evidence_engram_ids, bank_id)
    out: list[EvidenceEngramDTO] = []
    for eid in model.evidence_engram_ids:
        if len(out) >= max_n:
            break
        row = rows.get(eid)
        if row is None:
            continue
        out.append(
            EvidenceEngramDTO(
                id=eid,
                text=row["text"] or "",
                fact_type=row["fact_type"],
                context=row["context"],
                strength=row["strength"],
                tags=list(row["tags"] or []),
            )
        )
    return out


@router.get(
    "/banks/{bank_id}/hyper-schemas",
    response_model=list[HyperSchemaDTO],
    summary="List HyperSchemas with their :SPECIALIZES children",
)
async def list_bank_hyper_schemas(
    bank_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> list[HyperSchemaDTO]:
    neo4j = _neo4j(request)
    # Cypher 5: after a `collect(...)` aggregation, only RETURNed names are
    # visible — ORDER BY h.last_reinforced_at would fail. Hoist the order
    # key into the projection so the planner keeps it accessible.
    cypher = (
        "MATCH (h:HyperSchema {status: 'active'}) "
        "OPTIONAL MATCH (s:Schema)-[:SPECIALIZES]->(h) "
        "RETURN properties(h) AS hp, collect(s.id) AS child_ids, "
        "       h.last_reinforced_at AS last_reinforced_at "
        "ORDER BY last_reinforced_at DESC "
        "LIMIT $limit"
    )
    rows = await neo4j.run_cypher(cypher, params={"limit": int(limit)})

    out: list[HyperSchemaDTO] = []
    for row in rows:
        try:
            hyper = HyperSchemaModel.from_neo4j_props(row["hp"])
        except Exception as exc:  # noqa: BLE001 — best-effort, keep the explorer alive
            logger.warning("[CP] hyper-schema parse failed: %s", exc)
            continue
        # Bank filter — same logic as schemas list
        props = hyper.properties or {}
        bank_ok = (
            not props
            or props.get("bank_id") == bank_id
            or (isinstance(props.get("source_bank_ids"), list) and bank_id in props["source_bank_ids"])
            or ("bank_id" not in props and "source_bank_ids" not in props)
        )
        if not bank_ok:
            continue

        child_ids = [UUID(cid) for cid in (row.get("child_ids") or []) if cid]
        out.append(
            HyperSchemaDTO(
                id=hyper.id,
                description=hyper.description,
                evidence_count=hyper.evidence_count,
                cycles_survived=hyper.cycles_survived,
                status=hyper.status,
                last_reinforced_at=hyper.last_reinforced_at,
                children_ids=child_ids,
                properties=dict(hyper.properties),
            )
        )
    return out


@router.get(
    "/banks/{bank_id}/schemas/centroid-2d",
    response_model=list[CentroidPoint2DDTO],
    summary="2-D scatter projection of schema centroids (UMAP)",
    description=(
        "Projects all active schema centroids in the bank into 2D for plotting. "
        "Method is currently fixed to UMAP; the parameter is reserved for future "
        "PCA / t-SNE alternatives. Requires Qdrant; returns 503 when Qdrant is "
        "not configured. Returns 200 with an empty list when the bank has fewer "
        "than 2 schemas (UMAP needs ≥ 2 points)."
    ),
)
async def schemas_centroid_2d(
    bank_id: str,
    request: Request,
    method: str = Query("umap", pattern="^(umap)$"),
    limit: int = Query(200, ge=2, le=2000),
) -> list[CentroidPoint2DDTO]:
    qdrant = _qdrant(request)
    if qdrant is None:
        raise HTTPException(status_code=503, detail="Qdrant client not initialised")
    neo4j = _neo4j(request)

    schemas = await list_active_schemas(neo4j, label="Schema", limit=limit)
    schemas = _filter_bank([s for s in schemas if isinstance(s, SchemaModel)], bank_id)
    if len(schemas) < 2:
        return []

    schema_ids = [str(s.centroid_qdrant_id) for s in schemas if s.centroid_qdrant_id is not None]
    points = await qdrant.retrieve_many(schema_ids)
    if len(points) < 2:
        return []

    import numpy as np

    vectors = np.asarray([p["vector"] for p in points if p.get("vector") is not None], dtype=np.float32)
    if len(vectors) < 2:
        return []

    try:
        import umap  # type: ignore[import-not-found]
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="UMAP not installed in this build — install umap-learn to enable centroid-2d.",
        ) from None

    reducer = umap.UMAP(n_components=2, random_state=42, metric="cosine")
    coords = reducer.fit_transform(vectors)

    by_id = {str(p.get("engram_id") or p.get("payload", {}).get("schema_id")): p for p in points}
    out: list[CentroidPoint2DDTO] = []
    for s, (x, y) in zip(schemas, coords, strict=False):
        cid = str(s.centroid_qdrant_id) if s.centroid_qdrant_id else None
        if cid is None or cid not in by_id:
            continue
        out.append(
            CentroidPoint2DDTO(
                schema_id=s.id,
                description=s.description,
                x=float(x),
                y=float(y),
            )
        )
    return out
