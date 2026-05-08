"""add schema_drift_events audit table

Revision ID: e25s22drift
Revises: e25c2fingerprint
Create Date: 2026-05-08

Epic 25 — CLS Architecture Refactor, Story 22.

Story 21 introduced Validation-mode centroid drift on schema reconsolidation
(α=0.05 nudge towards the query embedding). Story 22 makes that change
auditable and rate-limited:

  - Per drift event we append a row here with bank/schema/alpha/mode/
    query-hash so the audit trail can replay how a schema's centroid
    moved over Recalls.
  - The schema's own ``drift_count`` + ``last_drifted_at`` properties
    (Neo4j-side) drive the rolling 24h throttle. C2 reinforce_schema
    resets ``drift_count`` back to zero when fresh evidence lands.

The audit table is bank-scoped and has a ``timestamp DESC`` index so the
throttle path can read recent events efficiently if it ever needs to
cross-check the per-schema counter.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "e25s22drift"
down_revision: str | Sequence[str] | None = "e25c2fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _target_schema() -> str:
    schema = context.config.get_main_option("target_schema")
    return schema if schema else "public"


def upgrade() -> None:
    target_schema = _target_schema()

    op.create_table(
        "schema_drift_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bank_id",
            sa.Text(),
            sa.ForeignKey(f"{target_schema}.banks.bank_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alpha", sa.Float(), nullable=False),
        sa.Column("query_hash", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=target_schema,
    )

    op.create_index(
        "idx_schema_drift_events_bank",
        "schema_drift_events",
        ["bank_id"],
        schema=target_schema,
    )
    op.create_index(
        "idx_schema_drift_events_schema",
        "schema_drift_events",
        ["schema_id"],
        schema=target_schema,
    )
    op.create_index(
        "idx_schema_drift_events_recent",
        "schema_drift_events",
        ["schema_id", sa.text("occurred_at DESC")],
        schema=target_schema,
    )


def downgrade() -> None:
    target_schema = _target_schema()
    op.drop_index(
        "idx_schema_drift_events_recent",
        table_name="schema_drift_events",
        schema=target_schema,
    )
    op.drop_index(
        "idx_schema_drift_events_schema",
        table_name="schema_drift_events",
        schema=target_schema,
    )
    op.drop_index(
        "idx_schema_drift_events_bank",
        table_name="schema_drift_events",
        schema=target_schema,
    )
    op.drop_table("schema_drift_events", schema=target_schema)
