"""tighten engram_dictionary.layer to {'working','buffer'} + audit-stamp neocortex migrations

Revision ID: e25a02layer
Revises: d6e7f8a9b0c1
Create Date: 2026-05-06

Epic 25 — CLS Architecture Refactor, Story 02. Engrams now live exclusively in
the hippocampal buffer (concept §1, §4). The neocortex layer no longer holds
individual engrams — it holds standalone :Schema/:HyperSchema nodes (Story 01).

Migration semantics:
- Existing rows with `layer='neocortex'` are folded back to `'buffer'` (they
  were never real schemas — schema emergence happens via C2 over buffer
  engrams). A new audit column `migrated_from_neocortex_at` is set to NOW()
  for every such row so the provenance is preserved.
- The CHECK constraint is tightened to `('working','buffer')`. Any subsequent
  insert of `'neocortex'` is rejected at the DB layer; the Pydantic validator
  rejects it at the application layer.
- Legacy NCR / multi-bank promotion paths that still write `'neocortex'` are
  out of scope here — they will fail at runtime until Story 18 cleanup.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e25a02layer"
down_revision: Union[str, Sequence[str], None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _qualified_table() -> str:
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema") if ctx.config else None
    if schema:
        return f'"{schema}".engram_dictionary'
    return "engram_dictionary"


def _schema_kwarg() -> dict[str, str]:
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema") if ctx.config else None
    return {"schema": schema} if schema else {}


def upgrade() -> None:
    table = _qualified_table()

    op.add_column(
        "engram_dictionary",
        sa.Column("migrated_from_neocortex_at", sa.TIMESTAMP(timezone=True), nullable=True),
        **_schema_kwarg(),
    )

    op.execute(f"UPDATE {table} SET layer = 'buffer', migrated_from_neocortex_at = NOW() WHERE layer = 'neocortex'")

    op.execute("ALTER TABLE engram_dictionary DROP CONSTRAINT IF EXISTS engram_dictionary_layer_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT engram_dictionary_layer_check CHECK (layer IN ('working', 'buffer'))"
    )


def downgrade() -> None:
    table = _qualified_table()

    op.execute("ALTER TABLE engram_dictionary DROP CONSTRAINT IF EXISTS engram_dictionary_layer_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT engram_dictionary_layer_check "
        f"CHECK (layer IN ('working', 'buffer', 'neocortex'))"
    )

    op.execute(
        f"UPDATE {table} "
        f"SET layer = 'neocortex', migrated_from_neocortex_at = NULL "
        f"WHERE migrated_from_neocortex_at IS NOT NULL"
    )

    op.drop_column("engram_dictionary", "migrated_from_neocortex_at", **_schema_kwarg())
