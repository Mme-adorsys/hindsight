"""add bank tier

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-04-07

Epic 14 Story 01 (B1) — 3-Tier Bank Model.
Adds 'tier' column to 'banks' table with values: 'session' | 'dictionary' | 'shared'.
Default 'session' ensures backward-compatibility with all existing banks.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tier column + index + check constraint to banks table."""
    op.add_column(
        "banks",
        sa.Column("tier", sa.Text(), nullable=False, server_default="session"),
    )
    op.create_index("idx_banks_tier", "banks", ["tier"])
    op.create_check_constraint(
        "ck_banks_tier",
        "banks",
        "tier IN ('session', 'dictionary', 'shared')",
    )


def downgrade() -> None:
    """Remove tier column, index, and check constraint from banks table."""
    op.drop_constraint("ck_banks_tier", "banks", type_="check")
    op.drop_index("idx_banks_tier", table_name="banks")
    op.drop_column("banks", "tier")
