"""switch BM25 search_vector from 'english' to 'simple' config

Revision ID: c1d2e3f4a5b6
Revises: a8b9c0d1e2f3
Create Date: 2026-04-12

The default 'english' text search configuration applies English-specific
stemming and stopword filtering, which produces nonsensical tokens for
German (and other non-English) content. For example,
"Festplattenausfall" stemmed by the English Snowball stemmer becomes
"festplattenausfal" — a useless token that won't match queries for the
exact word.

The 'simple' configuration tokenizes only on whitespace/punctuation and
lowercases — no stemming, no stopword filtering. This works correctly
for any language. Stemming is a nice-to-have; semantic search via
embeddings already handles morphological variation ("Datenbank" vs
"Datenbanken") through similarity in vector space.

This migration drops and recreates the GENERATED column on memory_units
and the materialized view memory_units_bm25.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop and recreate the GENERATED column on memory_units
    op.execute("ALTER TABLE memory_units DROP COLUMN IF EXISTS search_vector")
    op.execute("""
        ALTER TABLE memory_units
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', COALESCE(text, '') || ' ' || COALESCE(context, ''))) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_units_text_search
        ON memory_units USING gin(search_vector)
    """)

    # 2. Drop and recreate the materialized view memory_units_bm25
    op.execute("DROP MATERIALIZED VIEW IF EXISTS memory_units_bm25 CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW memory_units_bm25 AS
        SELECT
            id,
            bank_id,
            text,
            to_tsvector('simple', text) AS text_vector,
            log(1.0 + length(text)::float / NULLIF((SELECT avg(length(text)) FROM memory_units), 0)) AS doc_length_factor
        FROM memory_units
    """)
    op.execute("CREATE INDEX idx_memory_units_bm25_bank ON memory_units_bm25 (bank_id)")
    op.execute("CREATE INDEX idx_memory_units_bm25_text_vector ON memory_units_bm25 USING gin (text_vector)")


def downgrade() -> None:
    # Revert to 'english' config
    op.execute("ALTER TABLE memory_units DROP COLUMN IF EXISTS search_vector")
    op.execute("""
        ALTER TABLE memory_units
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', COALESCE(text, '') || ' ' || COALESCE(context, ''))) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_units_text_search
        ON memory_units USING gin(search_vector)
    """)

    op.execute("DROP MATERIALIZED VIEW IF EXISTS memory_units_bm25 CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW memory_units_bm25 AS
        SELECT
            id,
            bank_id,
            text,
            to_tsvector('english', text) AS text_vector,
            log(1.0 + length(text)::float / NULLIF((SELECT avg(length(text)) FROM memory_units), 0)) AS doc_length_factor
        FROM memory_units
    """)
    op.execute("CREATE INDEX idx_memory_units_bm25_bank ON memory_units_bm25 (bank_id)")
    op.execute("CREATE INDEX idx_memory_units_bm25_text_vector ON memory_units_bm25 USING gin (text_vector)")
