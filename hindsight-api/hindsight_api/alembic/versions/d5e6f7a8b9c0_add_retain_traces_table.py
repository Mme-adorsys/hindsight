"""add retain_traces table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-09 14:00:00.000000

Observability Phase B, Item B2. Persistence target for the retain-side
PipelineTracer (B3). Each retain_batch invocation writes one row with a
JSONB trace_data blob containing the flat list of TraceStep entries.

Design notes:
- operation_id is nullable because sync retains have no operation record;
  async retains carry the task operation UUID forward.
- bank_id is a FK to banks with ON DELETE CASCADE so deleting a bank cleans
  up its traces automatically.
- trace_data is NOT NULL — we only insert after a trace exists. If persist
  fails, the retain itself still succeeds (non-blocking logging in B3).
- Index on (bank_id, started_at DESC) supports "last N traces per bank" for
  the CP trace explorer. No JSON-path index for now — trace_data is opaque
  storage and querying into it is explicitly out of scope for Phase B.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create retain_traces table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.create_table(
        "retain_traces",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bank_id", sa.Text(), nullable=False),
        sa.Column(
            "operation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "trace_data",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.bank_id"], ondelete="CASCADE"),
        schema=schema,
    )
    op.create_index(
        "idx_retain_traces_bank_started",
        "retain_traces",
        ["bank_id", sa.text("started_at DESC")],
        schema=schema,
    )


def downgrade() -> None:
    """Drop retain_traces table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None
    op.drop_index("idx_retain_traces_bank_started", table_name="retain_traces", schema=schema)
    op.drop_table("retain_traces", schema=schema)
