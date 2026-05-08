"""Pydantic models for :Schema and :HyperSchema cortex nodes.

Field set per concept §4.2 (CLS — schemas live exclusively in the cortex,
referencing buffer engrams indirectly via the Top-N evidence_engram_ids
property; cf. Teyler & DiScenna 1986 indexing theory).

Story 01 deviates from the originally suggested module path
(`hindsight_api/models/schema.py`): `models.py` is the SQLAlchemy ORM module,
not a Pydantic package, so colocating the schema Pydantic models with the
repository under `engine/schema/` mirrors `engine/constructive/models.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

DEFAULT_EVIDENCE_TOP_N: int = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialise_props_for_neo4j(properties: dict[str, Any]) -> str:
    """Neo4j node properties cannot be nested dicts — serialise to JSON.

    Why JSON instead of a flat top-level fan-out: keeps the node schema stable
    across schemas with heterogeneous property keys, and round-trip parsing is
    cheap. Counterpart in `from_neo4j_props`.
    """
    return json.dumps(properties, default=str, sort_keys=True)


def _deserialise_props_from_neo4j(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class _SchemaBase(BaseModel):
    """Shared fields between Schema and HyperSchema (concept §4.2).

    Both node types carry identical property sets; the distinction is the
    Neo4j label and the :SPECIALIZES edge that may bind them.
    """

    id: UUID = Field(default_factory=uuid4)
    description: str = Field(default="", description="Human-readable summary (LLM SMALL or template).")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Statistically aggregated key/value pattern (e.g. participant_count, mood).",
    )
    centroid_qdrant_id: UUID | None = Field(
        default=None,
        description="Qdrant point id of the centroid vector (mean of evidence embeddings).",
    )
    evidence_engram_ids: list[UUID] = Field(
        default_factory=list,
        description="Top-N strongest evidence engrams (default N=5). Index-only; no Neo4j edge.",
    )
    evidence_count: int = Field(default=0, ge=0)
    cycles_survived: int = Field(default=0, ge=0)
    status: str = Field(default="active", description="'active' or 'archived' (R5 Schema Death).")
    created_at: datetime = Field(default_factory=_utcnow)
    last_reinforced_at: datetime | None = Field(default=None)
    # Story 21 — Recall reactivates the schema's representation. ``access_count``
    # mirrors the engram-side counter (concept §4.1) so HybridRetriever
    # reconsolidation has somewhere to write the recall hit; ``last_accessed``
    # is the wallclock equivalent. Persisted as Neo4j primitives.
    access_count: int = Field(default=0, ge=0)
    last_accessed: datetime | None = Field(default=None)
    # Story 22 — Drift tracking. ``drift_count`` is incremented every time
    # Validation-mode reconsolidation actually nudges the centroid; the
    # rolling 24h window resets it back to zero (see throttle in
    # reflect/schema_reconsolidation.py). ``last_drifted_at`` carries the
    # wallclock of the most recent successful drift event.
    drift_count: int = Field(default=0, ge=0)
    last_drifted_at: datetime | None = Field(default=None)

    def to_neo4j_props(self) -> dict[str, Any]:
        """Serialise to Neo4j-compatible primitives.

        Neo4j accepts: str, int, float, bool, list of those, datetime; not UUID
        objects, not nested dicts. We convert UUIDs to strings, datetimes to
        ISO 8601, and the freeform `properties` map to a JSON string.
        """
        return {
            "id": str(self.id),
            "description": self.description,
            "properties_json": _serialise_props_for_neo4j(self.properties),
            "centroid_qdrant_id": str(self.centroid_qdrant_id) if self.centroid_qdrant_id else None,
            "evidence_engram_ids": [str(eid) for eid in self.evidence_engram_ids],
            "evidence_count": self.evidence_count,
            "cycles_survived": self.cycles_survived,
            "status": self.status,
            "created_at": _to_iso(self.created_at),
            "last_reinforced_at": _to_iso(self.last_reinforced_at),
            "access_count": self.access_count,
            "last_accessed": _to_iso(self.last_accessed),
            "drift_count": self.drift_count,
            "last_drifted_at": _to_iso(self.last_drifted_at),
        }

    @classmethod
    def from_neo4j_props(cls, props: dict[str, Any]) -> _SchemaBase:
        evidence_raw = props.get("evidence_engram_ids") or []
        return cls(
            id=UUID(props["id"]) if not isinstance(props.get("id"), UUID) else props["id"],
            description=props.get("description", "") or "",
            properties=_deserialise_props_from_neo4j(props.get("properties_json")),
            centroid_qdrant_id=(UUID(props["centroid_qdrant_id"]) if props.get("centroid_qdrant_id") else None),
            evidence_engram_ids=[UUID(e) if not isinstance(e, UUID) else e for e in evidence_raw],
            evidence_count=int(props.get("evidence_count", 0) or 0),
            cycles_survived=int(props.get("cycles_survived", 0) or 0),
            status=props.get("status", "active") or "active",
            created_at=_from_iso(props.get("created_at")) or _utcnow(),
            last_reinforced_at=_from_iso(props.get("last_reinforced_at")),
            access_count=int(props.get("access_count", 0) or 0),
            last_accessed=_from_iso(props.get("last_accessed")),
            drift_count=int(props.get("drift_count", 0) or 0),
            last_drifted_at=_from_iso(props.get("last_drifted_at")),
        )


class SchemaModel(_SchemaBase):
    """Standalone cortex node (Neo4j label `:Schema`).

    Replaces the legacy representation of a schema as an Engram with
    `layer='neocortex'`. Engram `layer` is constrained to {working, buffer}
    in Story 02.
    """


class HyperSchemaModel(_SchemaBase):
    """Cortex node (Neo4j label `:HyperSchema`) produced by C3 R3.

    A HyperSchema generalises a cluster of related Schemas (Centroid-Cosine
    ≥ 0.7 with systematic property differences). Linked via
    `(:Schema)-[:SPECIALIZES]->(:HyperSchema)`.
    """
