"""Add ncr_runs table for NCR run history persistence.

Epic 21 — CP Engram Lifecycle & NCR Dashboard (Story 03)
Persists the per-run NCRReport so the Control Plane NCR Dashboard can display
the last N runs per bank after server restarts. Phase-level statistics are
stored as JSONB for schema flexibility.

Revision ID: a2b3c4d5e6f7
Revises: f0a1b2c3d4e5
Create Date: 2026-04-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ncr_runs table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.create_table(
        "ncr_runs",
        sa.Column(
            "run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bank_id", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column(
            "consolidation_stats",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "decay_stats",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "strengthen_stats",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "schema_stats",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "promotion_stats",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "errors",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.bank_id"], ondelete="CASCADE"),
        sa.CheckConstraint("trigger IN ('manual', 'scheduled')", name="ck_ncr_runs_trigger"),
        schema=schema,
    )
    op.create_index(
        "idx_ncr_runs_bank_started",
        "ncr_runs",
        ["bank_id", "started_at"],
        schema=schema,
    )


def downgrade() -> None:
    """Drop ncr_runs table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None
    op.drop_index("idx_ncr_runs_bank_started", table_name="ncr_runs", schema=schema)
    op.drop_table("ncr_runs", schema=schema)
