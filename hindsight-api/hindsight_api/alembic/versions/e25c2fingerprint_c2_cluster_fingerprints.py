"""add c2_cluster_fingerprints table for R2 maturation

Revision ID: e25c2fingerprint
Revises: e25a02layer
Create Date: 2026-05-07

Epic 25 — CLS Architecture Refactor, Story 05.

R1 (Story 04) finds clusters in a single C2 run. R2 (Maturation, concept §13)
demands that a cluster survives ≥ 2 C2 cycles before it becomes a schema
candidate — one-shot clusters are filtered out as noise. This table holds
per-bank cluster fingerprints (centroid + dominant tags) so a fresh R1
candidate can be matched against prior runs via cosine similarity ≥ 0.85.

The fingerprint store sits in PostgreSQL rather than Neo4j because it's a
transient working-state (Buffer/Hippocampus side), not a cortical schema.
Stale fingerprints (``last_seen_at`` > 7d) are pruned by the repository.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "e25c2fingerprint"
down_revision: str | Sequence[str] | None = "e25a02layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 384


def _target_schema() -> str:
    schema = context.config.get_main_option("target_schema")
    return schema if schema else "public"


def upgrade() -> None:
    target_schema = _target_schema()

    op.create_table(
        "c2_cluster_fingerprints",
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
        sa.Column(
            "centroid",
            sa.dialects.postgresql.ARRAY(sa.Float()),  # placeholder — replaced by vector via raw DDL below
            nullable=False,
        ),
        sa.Column("dominant_tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cycles_survived", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=target_schema,
    )

    # Replace the placeholder column with the real pgvector type.
    # SQLAlchemy core doesn't ship a Vector ddl outside the pgvector plugin,
    # which means importing it would force the migration to depend on the
    # plugin at autogenerate time. Raw DDL keeps the migration self-contained.
    schema_prefix = f'"{target_schema}".' if target_schema and target_schema != "public" else ""
    table = f"{schema_prefix}c2_cluster_fingerprints"
    op.execute(f"ALTER TABLE {table} ALTER COLUMN centroid TYPE vector({EMBEDDING_DIMENSION}) USING centroid::vector")

    op.create_index(
        "idx_c2_cluster_fingerprints_bank",
        "c2_cluster_fingerprints",
        ["bank_id"],
        schema=target_schema,
    )
    op.create_index(
        "idx_c2_cluster_fingerprints_last_seen",
        "c2_cluster_fingerprints",
        ["last_seen_at"],
        schema=target_schema,
    )
    # HNSW cosine index — ``match_or_create`` ranks fingerprints by 1-cos.
    op.create_index(
        "idx_c2_cluster_fingerprints_centroid",
        "c2_cluster_fingerprints",
        ["centroid"],
        schema=target_schema,
        postgresql_using="hnsw",
        postgresql_ops={"centroid": "vector_cosine_ops"},
    )


def downgrade() -> None:
    target_schema = _target_schema()
    op.drop_index(
        "idx_c2_cluster_fingerprints_centroid",
        table_name="c2_cluster_fingerprints",
        schema=target_schema,
    )
    op.drop_index(
        "idx_c2_cluster_fingerprints_last_seen",
        table_name="c2_cluster_fingerprints",
        schema=target_schema,
    )
    op.drop_index(
        "idx_c2_cluster_fingerprints_bank",
        table_name="c2_cluster_fingerprints",
        schema=target_schema,
    )
    op.drop_table("c2_cluster_fingerprints", schema=target_schema)
