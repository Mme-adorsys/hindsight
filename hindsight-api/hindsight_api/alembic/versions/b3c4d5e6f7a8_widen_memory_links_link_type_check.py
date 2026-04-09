"""widen memory_links link_type check constraint for engram link types

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-04-09 10:45:00.000000

The legacy Hindsight `memory_links` table has a CHECK constraint that only
allows 7 old link types (temporal, semantic, entity, causes, caused_by,
enables, prevents). The Engram pipeline writes additional link types
(temporal_proximity, co_activated, causal, prediction_error, schema,
contradiction) via `retain/link_creation.py` and `retain/link_utils.py`.

Without this migration, the first retain call on a fresh database fails
with `memory_links_link_type_check constraint violation` as soon as a
temporal_proximity link is inserted.

This migration drops the existing constraint and re-creates it with all
13 allowed link types. The constraint name is preserved so future checks
and drops continue to find it.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


# Legacy Hindsight link types (prior to the Engram extension).
_LEGACY_TYPES = (
    "temporal",
    "semantic",
    "entity",
    "causes",
    "caused_by",
    "enables",
    "prevents",
)

# Additional Engram link types written by retain/link_creation.py +
# retain/link_utils.py. Kept in sync with Neo4j RELATIONSHIP_TYPES
# (hindsight_api/engine/neo4j_client.py) where applicable.
_ENGRAM_TYPES = (
    "temporal_proximity",
    "co_activated",
    "causal",
    "prediction_error",
    "schema",
    "contradiction",
)

_ALL_TYPES = _LEGACY_TYPES + _ENGRAM_TYPES
_CONSTRAINT_NAME = "memory_links_link_type_check"
_TABLE_NAME = "memory_links"


def _qualified_table() -> str:
    """Return the fully qualified table name including the target schema."""
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema")
    if schema:
        return f'"{schema}".{_TABLE_NAME}'
    return _TABLE_NAME


def _check_clause(types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{t}'" for t in types)
    return f"link_type IN ({values})"


def upgrade() -> None:
    table = _qualified_table()
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK ({_check_clause(_ALL_TYPES)})")


def downgrade() -> None:
    # Best-effort downgrade: revert to the 7 legacy types. This is
    # destructive if any rows using the extended types exist — the
    # ADD CONSTRAINT will fail. Operators should clean up such rows
    # manually before downgrading.
    table = _qualified_table()
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK ({_check_clause(_LEGACY_TYPES)})")
