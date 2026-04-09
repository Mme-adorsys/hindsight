"""add trace_data column to ncr_runs

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-09 14:15:00.000000

Observability Phase B, Item B2 (second migration). Adds a JSONB trace_data
column to ncr_runs so the NCR PipelineTracer (B4) can persist its per-phase
trace alongside the existing phase-level stats. Nullable because pre-existing
rows predate the tracer and stay null.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add trace_data JSONB column to ncr_runs."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.add_column(
        "ncr_runs",
        sa.Column(
            "trace_data",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema=schema,
    )


def downgrade() -> None:
    """Drop trace_data column from ncr_runs."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None
    op.drop_column("ncr_runs", "trace_data", schema=schema)
