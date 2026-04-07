"""Add bank_model_config table for per-bank LLM budget profile configuration.

Epic 17 — Konfigurierbare Modell-Zuweisung (Story 02)
Merges the two diverged heads (bank tier + expectation/outcome) and adds
bank_model_config for per-bank budget profile and per-step overrides.

Revision ID: e5f6a7b8c9d0
Revises: b1c2d3e4f5a6, c3d4e5f6a7b8
Create Date: 2026-04-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = ("b1c2d3e4f5a6", "c3d4e5f6a7b8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create bank_model_config table."""
    # Determine schema from context
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.create_table(
        "bank_model_config",
        sa.Column("bank_id", sa.String(), nullable=False),
        sa.Column("budget_profile", sa.String(), nullable=False, server_default="mid"),
        sa.Column(
            "step_overrides",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("bank_id"),
        schema=schema,
    )


def downgrade() -> None:
    """Drop bank_model_config table."""
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None
    op.drop_table("bank_model_config", schema=schema)
