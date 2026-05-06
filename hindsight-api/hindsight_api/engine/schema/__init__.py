"""Schema entities and repository (Epic 25 — CLS Architecture Refactor).

Schemas live exclusively in the cortex layer (Neo4j) as standalone
:Schema and :HyperSchema nodes — decoupled from individual Engrams.
Engrams reference schemas indirectly via Top-N evidence_engram_ids.
"""

from .models import HyperSchemaModel, SchemaModel
from .schema_repository import (
    archive_schema,
    create_schema,
    get_schema,
    link_specialization,
    list_active_schemas,
    materialize_schema_node,
    update_schema,
)

__all__ = [
    "HyperSchemaModel",
    "SchemaModel",
    "archive_schema",
    "create_schema",
    "get_schema",
    "link_specialization",
    "list_active_schemas",
    "materialize_schema_node",
    "update_schema",
]
