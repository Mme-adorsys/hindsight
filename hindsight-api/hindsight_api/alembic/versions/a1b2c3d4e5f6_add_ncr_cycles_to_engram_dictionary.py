"""add_ncr_cycles_to_engram_dictionary

Revision ID: a1b2c3d4e5f6
Revises: f1b2c3d4e5f6
Create Date: 2026-04-06

Add ncr_cycles_survived and promoted_at columns to engram_dictionary.

ncr_cycles_survived: counts how many NCR cycles an Engram has survived without
  being archived. Used by NCR Phase 2 (Strengthen) as a promotion criterion.
  Biological mapping: synaptic consolidation requires repeated reactivation
  across multiple slow-wave sleep cycles (LTP Late).

promoted_at: timestamp when an Engram was promoted from buffer to neocortex.
  Audit trail for the consolidation pipeline.

Epic 12, Story 03 — NCR Phase 2: Strengthen.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    schema = context.get_x_argument(as_dictionary=True).get("schema", None)
    schema_prefix = f"{schema}." if schema else ""
    table = f"{schema_prefix}engram_dictionary"

    op.execute(f"""
        ALTER TABLE {table}
        ADD COLUMN IF NOT EXISTS ncr_cycles_survived INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    schema = context.get_x_argument(as_dictionary=True).get("schema", None)
    schema_prefix = f"{schema}." if schema else ""
    table = f"{schema_prefix}engram_dictionary"

    op.execute(f"""
        ALTER TABLE {table}
        DROP COLUMN IF EXISTS ncr_cycles_survived,
        DROP COLUMN IF EXISTS promoted_at
    """)
