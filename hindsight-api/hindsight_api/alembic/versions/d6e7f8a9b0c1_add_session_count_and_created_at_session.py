"""add banks.session_count and engram_dictionary.created_at_session

Revision ID: d6e7f8a9b0c1
Revises: c1d2e3f4a5b6
Create Date: 2026-04-15

Epic 24 — Lifecycle Scoring Overhaul, Story 01 (Sessions-Alive Taktgeber).

Two new integer columns that enable sessions-based aging:

- banks.session_count: total completed sessions for this bank. Incremented
  exactly once per session close (not per retain/recall operation). Replaces
  op_count as the aging metric — a session is the natural unit of work, and
  an engram proves its value by being recalled across multiple sessions.
- engram_dictionary.created_at_session: snapshot of bank.session_count at
  engram creation time. Together with session_count this yields
  sessions_alive = session_count - created_at_session.

Both default to 0 so existing rows are backwards-compatible — existing engrams
effectively start at sessions_alive=0 at the time of migration.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.add_column(
        "banks",
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "engram_dictionary",
        sa.Column("created_at_session", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )


def downgrade() -> None:
    config = op.get_context().config
    schema = config.get_main_option("target_schema") if config else None

    op.drop_column("engram_dictionary", "created_at_session", schema=schema)
    op.drop_column("banks", "session_count", schema=schema)
