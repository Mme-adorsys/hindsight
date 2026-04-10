"""add banks.op_count and engram_dictionary.created_at_op

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-10

Epic 24 — Selective Consolidation, Story 01.

Two new integer columns that enable the composite strength formula
to compute cycles_alive = bank.op_count - engram.created_at_op:

- banks.op_count: atomically incremented on every retain + recall
  operation. Represents the total "session cycle count" for this bank.
- engram_dictionary.created_at_op: snapshot of bank.op_count at the
  time the engram was created. Together with op_count this gives us
  a session-relative age metric (not wall-clock time).

Both default to 0 so existing rows are backwards-compatible.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.add_column(
        "banks",
        sa.Column("op_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "engram_dictionary",
        sa.Column("created_at_op", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )


def downgrade() -> None:
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.drop_column("engram_dictionary", "created_at_op", schema=schema)
    op.drop_column("banks", "op_count", schema=schema)
