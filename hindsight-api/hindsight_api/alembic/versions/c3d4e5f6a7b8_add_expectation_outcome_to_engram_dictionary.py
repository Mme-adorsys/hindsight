"""Add expectation and outcome columns to engram_dictionary.

Epic 15 — API & Retain Enrichment (Story 01)
Stores Expectation→Outcome pairs for Experience-Engram creation and Thalamus Surprise scoring.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op  # noqa: E402

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _get_table(schema: str) -> str:
    return f"{schema}engram_dictionary" if schema else "engram_dictionary"


def upgrade() -> None:
    connection = op.get_bind()
    schema_result = connection.execute(sa.text("SELECT current_schema()")).scalar()
    schema = f"{schema_result}." if schema_result and schema_result != "public" else ""
    table = _get_table(schema)

    op.execute(
        sa.text(
            f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS expectation TEXT,
            ADD COLUMN IF NOT EXISTS outcome TEXT
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    schema_result = connection.execute(sa.text("SELECT current_schema()")).scalar()
    schema = f"{schema_result}." if schema_result and schema_result != "public" else ""
    table = _get_table(schema)

    op.execute(
        sa.text(
            f"""
            ALTER TABLE {table}
            DROP COLUMN IF EXISTS expectation,
            DROP COLUMN IF EXISTS outcome
            """
        )
    )
