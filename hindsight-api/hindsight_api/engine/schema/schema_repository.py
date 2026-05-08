"""Neo4j CRUD for :Schema and :HyperSchema cortex nodes (Epic 25 Story 01).

All write operations use MERGE for idempotency (existing repo convention,
cf. schema_links.py / neo4j_link_writer.py). Reads return Pydantic models;
unknown ids return None rather than raising.

The label parameter switches between :Schema and :HyperSchema — the property
schema is identical (concept §4.2). The :SPECIALIZES edge is asymmetric and
has its own helper.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .models import HyperSchemaModel, SchemaModel, _SchemaBase

VALID_LABELS: frozenset[str] = frozenset({"Schema", "HyperSchema"})

IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "created_at"})


class SchemaRepositoryError(Exception):
    """Raised on invalid input to a repository helper."""


def _check_label(label: str) -> None:
    if label not in VALID_LABELS:
        raise SchemaRepositoryError(f"Invalid node label {label!r}; expected one of {sorted(VALID_LABELS)}")


def _model_for_label(label: str) -> type[_SchemaBase]:
    return HyperSchemaModel if label == "HyperSchema" else SchemaModel


async def create_schema(
    client: Any,
    model: _SchemaBase,
    *,
    label: str = "Schema",
) -> _SchemaBase:
    """Create or merge a :Schema (or :HyperSchema) node.

    Idempotent via MERGE: existing id is left untouched on identity, properties
    are overwritten via SET (treats existing-id as update — matches Story 01
    T4 `materialize_schema_node` requirement).
    """
    _check_label(label)
    props = model.to_neo4j_props()
    query = (
        f"MERGE (s:{label} {{id: $id}}) "
        "SET s.description = $description, "
        "    s.properties_json = $properties_json, "
        "    s.centroid_qdrant_id = $centroid_qdrant_id, "
        "    s.evidence_engram_ids = $evidence_engram_ids, "
        "    s.evidence_count = $evidence_count, "
        "    s.cycles_survived = $cycles_survived, "
        "    s.status = $status, "
        "    s.last_reinforced_at = $last_reinforced_at, "
        "    s.access_count = $access_count, "
        "    s.last_accessed = $last_accessed, "
        "    s.drift_count = $drift_count, "
        "    s.last_drifted_at = $last_drifted_at "
        "ON CREATE SET s.created_at = $created_at "
        "RETURN properties(s) AS p"
    )
    rows = await client.run_cypher(query, params=props)
    if not rows:
        raise SchemaRepositoryError(f"{label} create returned no rows for id={model.id}")
    return _model_for_label(label).from_neo4j_props(rows[0]["p"])


async def get_schema(
    client: Any,
    schema_id: UUID,
    *,
    label: str = "Schema",
) -> _SchemaBase | None:
    """Look up a node by id; return None when missing."""
    _check_label(label)
    query = f"MATCH (s:{label} {{id: $id}}) RETURN properties(s) AS p"
    rows = await client.run_cypher(query, params={"id": str(schema_id)})
    if not rows:
        return None
    return _model_for_label(label).from_neo4j_props(rows[0]["p"])


async def update_schema(
    client: Any,
    schema_id: UUID,
    partial: dict[str, Any],
    *,
    label: str = "Schema",
) -> _SchemaBase | None:
    """Partial update; rejects mutation of `id` / `created_at`.

    Caller passes a dict in the Neo4j-storage shape (i.e. UUIDs as strings,
    datetimes as ISO strings, free-form properties already JSON-encoded under
    `properties_json`). For most callers, prefer building a full SchemaModel
    and round-tripping through `create_schema` (which also MERGEs).
    """
    _check_label(label)
    illegal = IMMUTABLE_FIELDS & partial.keys()
    if illegal:
        raise SchemaRepositoryError(f"Cannot update immutable fields: {sorted(illegal)}")
    if not partial:
        return await get_schema(client, schema_id, label=label)
    set_clauses = ", ".join(f"s.{k} = ${k}" for k in partial)
    query = f"MATCH (s:{label} {{id: $id}}) SET {set_clauses} RETURN properties(s) AS p"
    params = {**partial, "id": str(schema_id)}
    rows = await client.run_cypher(query, params=params)
    if not rows:
        return None
    return _model_for_label(label).from_neo4j_props(rows[0]["p"])


async def archive_schema(
    client: Any,
    schema_id: UUID,
    *,
    label: str = "Schema",
) -> _SchemaBase | None:
    """Soft-archive: status='archived'. R5 Schema Death decides hard removal."""
    return await update_schema(client, schema_id, {"status": "archived"}, label=label)


async def list_active_schemas(
    client: Any,
    *,
    label: str = "Schema",
    limit: int = 100,
) -> list[_SchemaBase]:
    """List active nodes ordered by `last_reinforced_at` (most recent first)."""
    _check_label(label)
    query = (
        f"MATCH (s:{label} {{status: 'active'}}) "
        "RETURN properties(s) AS p "
        "ORDER BY s.last_reinforced_at DESC "
        "LIMIT $limit"
    )
    rows = await client.run_cypher(query, params={"limit": int(limit)})
    cls = _model_for_label(label)
    return [cls.from_neo4j_props(row["p"]) for row in rows]


async def link_specialization(
    client: Any,
    schema_id: UUID,
    hyper_id: UUID,
) -> None:
    """Create (:Schema)-[:SPECIALIZES]->(:HyperSchema) idempotently."""
    query = "MATCH (s:Schema {id: $schema_id}), (h:HyperSchema {id: $hyper_id}) MERGE (s)-[:SPECIALIZES]->(h)"
    await client.run_cypher(
        query,
        params={"schema_id": str(schema_id), "hyper_id": str(hyper_id)},
    )


async def materialize_schema_node(
    client: Any,
    model: _SchemaBase,
) -> _SchemaBase:
    """Idempotent create-or-update — Story 01 T4.

    Dispatches by model class: `HyperSchemaModel` → `:HyperSchema`, otherwise
    `:Schema`. Uses MERGE under the hood (`create_schema`).
    """
    label = "HyperSchema" if isinstance(model, HyperSchemaModel) else "Schema"
    return await create_schema(client, model, label=label)
