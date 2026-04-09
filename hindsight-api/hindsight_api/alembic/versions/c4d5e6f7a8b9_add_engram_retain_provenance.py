"""add engram retain provenance columns

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-09 12:00:00.000000

Retain-time provenance fields that previously had nowhere to land in the
database. Fills the observability gap identified during the first end-to-end
session: the CP memory detail panel wants to show *what* was stored and *why*
the Thalamus scored it the way it did, but the inputs (expectation, outcome,
session_mode, task_context) were never persisted — they were dead-code fields
on ProcessedFact (retain/types.py) that never made it into the INSERT in
fact_storage._insert_engram_dictionary_batch.

Adds three new columns to engram_dictionary. `expectation` and `outcome`
already exist from migration c3d4e5f6a7b8 (Epic 15 Story 02) — this
migration only adds what's still missing:

- session_mode  TEXT   — Session.mode at retain time (Precision / Exploration
                         / Analogy / Validation). Nullable: pre-existing rows
                         and retains without an explicit session stay null.
- task_context  TEXT   — Session.task_context snapshot, used by Thalamus
                         task_relevance `cos(embed(content), embed(task))`
- retain_context JSONB — catch-all for Thalamus rationale (novelty_max_similar_id,
                         prediction_error_magnitude, context source label) and
                         any future retain-time metadata we don't want to
                         migrate individually.

A GIN index on retain_context enables JSON-path queries from the CP, e.g.
`retain_context->>'novelty_max_similar_id' = 'abc-123'`.
"""

from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


_TABLE_NAME = "engram_dictionary"


def _qualified_table() -> str:
    """Return the fully qualified table name including the target schema."""
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema")
    if schema:
        return f'"{schema}".{_TABLE_NAME}'
    return _TABLE_NAME


def _schema_kwarg() -> dict:
    ctx = op.get_context()
    schema = ctx.config.get_main_option("target_schema")
    return {"schema": schema} if schema else {}


def upgrade() -> None:
    import sqlalchemy as sa

    schema_kwarg = _schema_kwarg()

    op.add_column(_TABLE_NAME, sa.Column("session_mode", sa.Text(), nullable=True), **schema_kwarg)
    op.add_column(_TABLE_NAME, sa.Column("task_context", sa.Text(), nullable=True), **schema_kwarg)
    op.add_column(_TABLE_NAME, sa.Column("retain_context", JSONB(), nullable=True), **schema_kwarg)

    # GIN index for JSON-path queries on retain_context. `jsonb_path_ops` is
    # smaller and faster than the default ops class when the only queries are
    # the contains-operators (@>, ?, ?& etc) which is our use case.
    table = _qualified_table()
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_engram_dictionary_retain_context "
        f"ON {table} USING gin (retain_context jsonb_path_ops)"
    )


def downgrade() -> None:
    schema_kwarg = _schema_kwarg()

    op.execute("DROP INDEX IF EXISTS idx_engram_dictionary_retain_context")

    op.drop_column(_TABLE_NAME, "retain_context", **schema_kwarg)
    op.drop_column(_TABLE_NAME, "task_context", **schema_kwarg)
    op.drop_column(_TABLE_NAME, "session_mode", **schema_kwarg)
