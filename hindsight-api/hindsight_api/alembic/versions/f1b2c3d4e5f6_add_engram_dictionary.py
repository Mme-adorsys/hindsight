"""add_engram_dictionary

Revision ID: f1b2c3d4e5f6
Revises: e0a1b2c3d4e5
Create Date: 2026-04-03

Add engram_dictionary table as Hippocampal Pointer Index (Epic 01, Story 03).

This table is a lightweight metadata lookup store — it knows WHERE information
lives (engram_id links to Qdrant + Neo4j) and HOW STRONG / VALUED it is
(strength, thalamus scores, layer). No text content, no embeddings, no relationships.

Enables fast pre-filter queries (e.g. "all Engrams with strength >= 0.5 and
layer='neocortex'") before expensive Qdrant/Neo4j operations.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_schema_prefix() -> str:
    """Get schema prefix for table names (e.g., 'tenant_x.' or '' for public)."""
    schema = context.config.get_main_option("target_schema")
    return f'"{schema}".' if schema else ""


def _get_target_schema() -> str:
    """Get the target schema name (tenant schema or 'public')."""
    schema = context.config.get_main_option("target_schema")
    return schema if schema else "public"


def upgrade() -> None:
    """Create engram_dictionary table with all indexes."""
    schema_prefix = _get_schema_prefix()
    target_schema = _get_target_schema()

    op.create_table(
        "engram_dictionary",
        sa.Column("engram_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bank_id", sa.Text(), sa.ForeignKey(f"{target_schema}.banks.bank_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("strength", sa.Float(), server_default="0.0"),
        sa.Column(
            "layer",
            sa.Text(),
            sa.CheckConstraint("layer IN ('buffer', 'neocortex')", name="engram_dictionary_layer_check"),
        ),
        sa.Column("abstraction_level", sa.Float(), server_default="0.0"),
        sa.Column("tags", postgresql.ARRAY(sa.Text())),
        sa.Column("novelty", sa.Float()),
        sa.Column("surprise", sa.Float()),
        sa.Column("task_relevance", sa.Float()),
        sa.Column("emotional_valence", sa.Float()),
        sa.Column("thalamus_overall", sa.Float()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_accessed", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("access_count", sa.Integer(), server_default="0"),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('active', 'archived', 'decayed')",
                name="engram_dictionary_status_check",
            ),
            server_default="active",
        ),
        sa.Column("confidence_score", sa.Float()),
        sa.Column("session_ref", postgresql.UUID(as_uuid=True)),
        schema=target_schema,
    )

    # B-Tree indexes for scalar filter fields
    op.create_index(
        "idx_engram_dictionary_strength",
        "engram_dictionary",
        ["strength"],
        schema=target_schema,
    )
    op.create_index(
        "idx_engram_dictionary_layer",
        "engram_dictionary",
        ["layer"],
        schema=target_schema,
    )
    op.create_index(
        "idx_engram_dictionary_status",
        "engram_dictionary",
        ["status"],
        schema=target_schema,
    )
    op.create_index(
        "idx_engram_dictionary_thalamus",
        "engram_dictionary",
        ["thalamus_overall"],
        schema=target_schema,
    )

    # GIN index for array field tags
    op.create_index(
        "idx_engram_dictionary_tags",
        "engram_dictionary",
        ["tags"],
        schema=target_schema,
        postgresql_using="gin",
    )

    # Composite indexes for common pre-filter patterns
    op.create_index(
        "idx_engram_dictionary_bank_layer_status",
        "engram_dictionary",
        ["bank_id", "layer", "status"],
        schema=target_schema,
    )
    op.create_index(
        "idx_engram_dictionary_bank_strength",
        "engram_dictionary",
        ["bank_id", "strength"],
        schema=target_schema,
    )


def downgrade() -> None:
    """Drop engram_dictionary table and all its indexes."""
    target_schema = _get_target_schema()

    op.drop_index("idx_engram_dictionary_bank_strength", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_bank_layer_status", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_tags", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_thalamus", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_status", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_layer", table_name="engram_dictionary", schema=target_schema)
    op.drop_index("idx_engram_dictionary_strength", table_name="engram_dictionary", schema=target_schema)
    op.drop_table("engram_dictionary", schema=target_schema)
