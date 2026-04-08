"""Add working_memory table for persistent PFC-equivalent working memory.

Epic 18 — Working Memory Persistence & Cache Layer (Story 02)
Adds working_memory table that stores the persistent portion of the Working
Context (goal_stack, active_engrams, confirmed_inferences, session_history)
as JSONB so it survives across session boundaries (Priming Effect).

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-04-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create working_memory table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.create_table(
        "working_memory",
        sa.Column("bank_id", sa.String(), nullable=False),
        sa.Column(
            "state",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("bank_id"),
        schema=schema,
    )
    op.create_index(
        "ix_working_memory_updated_at",
        "working_memory",
        ["updated_at"],
        schema=schema,
    )


def downgrade() -> None:
    """Drop working_memory table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None
    op.drop_index("ix_working_memory_updated_at", table_name="working_memory", schema=schema)
    op.drop_table("working_memory", schema=schema)
