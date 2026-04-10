"""add 'working' to layer CHECK constraint + backfill NULL → 'working'

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-10

Epic 24, Story 04. The Consolidation1 refactor requires `layer='working'`
as a valid value so that unconsolidated Engrams can stay in Working Memory
explicitly. Previously they had layer=NULL and were unconditionally promoted.

Also backfills existing NULL-layer active entries to 'working' so the new
Consolidation1 filter (`WHERE layer = 'working'`) finds them.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _qualified_table() -> str:
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema")
    if schema:
        return f'"{schema}".engram_dictionary'
    return "engram_dictionary"


def upgrade() -> None:
    table = _qualified_table()

    # 1. Drop old CHECK constraint (only allows 'buffer', 'neocortex')
    op.execute("ALTER TABLE engram_dictionary DROP CONSTRAINT IF EXISTS engram_dictionary_layer_check")

    # 2. Add widened CHECK (allows 'working', 'buffer', 'neocortex')
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT engram_dictionary_layer_check "
        f"CHECK (layer IN ('working', 'buffer', 'neocortex'))"
    )

    # 3. Backfill NULL → 'working' for active entries
    op.execute(f"UPDATE {table} SET layer = 'working' WHERE layer IS NULL AND status = 'active'")


def downgrade() -> None:
    table = _qualified_table()

    # Revert 'working' → NULL
    op.execute(f"UPDATE {table} SET layer = NULL WHERE layer = 'working'")

    # Restore old CHECK
    op.execute("ALTER TABLE engram_dictionary DROP CONSTRAINT IF EXISTS engram_dictionary_layer_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT engram_dictionary_layer_check CHECK (layer IN ('buffer', 'neocortex'))"
    )
