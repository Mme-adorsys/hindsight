"""
Fact storage for retain pipeline.

Handles insertion of facts into the database.
"""

import json
import logging
import uuid

from ..memory_engine import fq_table
from .types import ProcessedFact

logger = logging.getLogger(__name__)


async def insert_facts_batch(
    conn,
    bank_id: str,
    facts: list[ProcessedFact],
    document_id: str | None = None,
    *,
    bank_op_count: int = 0,
) -> list[str]:
    """
    Insert facts into the database in batch.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        facts: List of ProcessedFact objects to insert
        document_id: Optional document ID to associate with facts
        bank_op_count: Current bank.op_count — passed through to engram_dictionary
            as created_at_op for composite strength scoring (Epic 24).

    Returns:
        List of unit IDs (UUIDs as strings) for the inserted facts
    """
    if not facts:
        return []

    # Prepare data for batch insert
    fact_texts = []
    embeddings = []
    event_dates = []
    occurred_starts = []
    occurred_ends = []
    mentioned_ats = []
    contexts = []
    fact_types = []
    confidence_scores = []
    access_counts = []
    metadata_jsons = []
    chunk_ids = []
    document_ids = []

    for fact in facts:
        fact_texts.append(fact.fact_text)
        # Convert embedding to string for asyncpg vector type
        embeddings.append(str(fact.embedding))
        # event_date: Use occurred_start if available, otherwise use mentioned_at
        # This maintains backward compatibility while handling None occurred_start
        event_dates.append(fact.occurred_start if fact.occurred_start is not None else fact.mentioned_at)
        occurred_starts.append(fact.occurred_start)
        occurred_ends.append(fact.occurred_end)
        mentioned_ats.append(fact.mentioned_at)
        contexts.append(fact.context)
        fact_types.append(fact.fact_type)
        # confidence_score is only for opinion facts
        confidence_scores.append(1.0 if fact.fact_type == "opinion" else None)
        access_counts.append(0)  # Initial access count
        metadata_jsons.append(json.dumps(fact.metadata))
        chunk_ids.append(fact.chunk_id)
        # Use per-fact document_id if available, otherwise fallback to batch-level document_id
        document_ids.append(fact.document_id if fact.document_id else document_id)

    # Batch insert all facts
    results = await conn.fetch(
        f"""
        INSERT INTO {fq_table("memory_units")} (bank_id, text, embedding, event_date, occurred_start, occurred_end, mentioned_at,
                                 context, fact_type, confidence_score, access_count, metadata, chunk_id, document_id)
        SELECT $1, * FROM unnest(
            $2::text[], $3::vector[], $4::timestamptz[], $5::timestamptz[], $6::timestamptz[], $7::timestamptz[],
            $8::text[], $9::text[], $10::float[], $11::int[], $12::jsonb[], $13::text[], $14::text[]
        )
        RETURNING id
        """,
        bank_id,
        fact_texts,
        embeddings,
        event_dates,  # event_date: occurred_start if available, else mentioned_at
        occurred_starts,
        occurred_ends,
        mentioned_ats,
        contexts,
        fact_types,
        confidence_scores,
        access_counts,
        metadata_jsons,
        chunk_ids,
        document_ids,
    )

    unit_ids = [str(row["id"]) for row in results]

    # Write tags + thalamus scores to engram_dictionary (same transaction).
    # memory_units.id == engram_dictionary.engram_id — the shared Engram ID.
    await _insert_engram_dictionary_batch(conn, bank_id, unit_ids, facts, bank_op_count=bank_op_count)

    return unit_ids


async def _insert_engram_dictionary_batch(
    conn,
    bank_id: str,
    unit_ids: list[str],
    facts: list[ProcessedFact],
    *,
    bank_op_count: int = 0,
) -> None:
    """Write tags + thalamus scores + retain provenance into engram_dictionary.

    Called within the same DB transaction as insert_facts_batch so that
    memory_units and engram_dictionary stay consistent even on rollback.

    Writes the full retain-provenance bundle (Phase A3 — Apr 2026):
      - tags                              (TEXT[])
      - novelty/surprise/task_relevance/emotional_valence/thalamus_overall (FLOAT)
      - expectation / outcome             (TEXT, item-level from RetainContent)
      - session_mode                      (TEXT, Session.mode at retain time)
      - task_context                      (TEXT, effective task_context)
      - retain_context                    (JSONB, Thalamus rationale dump)

    Epic 24 addition:
      - created_at_op                     (INT, snapshot of bank.op_count)

    Args:
        conn: Active DB connection (already inside a transaction).
        bank_id: Bank identifier.
        unit_ids: UUIDs returned from the memory_units INSERT (one per fact).
        facts: ProcessedFact list in the same order as unit_ids.
        bank_op_count: Current bank.op_count value — stored as created_at_op
            so composite strength can compute cycles_alive later.
    """
    for unit_id, fact in zip(unit_ids, facts):
        ts = fact.thalamus_scores

        # Flatten the ThalamusRationale into a JSON-serialisable dict for
        # retain_context. None-values are kept so the UI can tell "no info"
        # apart from "zero value".
        retain_context_dict: dict | None = None
        if ts is not None and ts.rationale is not None:
            r = ts.rationale
            retain_context_dict = {
                "thalamus_rationale": {
                    "novelty_max_similar_id": r.novelty_max_similar_id,
                    "novelty_max_similarity": r.novelty_max_similarity,
                    "surprise_expectation_provided": r.surprise_expectation_provided,
                    "surprise_outcome_provided": r.surprise_outcome_provided,
                    "task_relevance_context_source": r.task_relevance_context_source,
                    "valence_prediction_error": r.valence_prediction_error,
                },
            }

        await conn.execute(
            """
            INSERT INTO engram_dictionary (
                engram_id, bank_id, tags,
                novelty, surprise, task_relevance, emotional_valence, thalamus_overall,
                strength, status,
                expectation, outcome, session_mode, task_context, retain_context,
                created_at_op
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                      $11, $12, $13, $14, $15::jsonb, $16)
            ON CONFLICT (engram_id) DO NOTHING
            """,
            uuid.UUID(unit_id),
            bank_id,
            fact.tags or None,
            ts.novelty if ts else None,
            ts.surprise if ts else None,
            ts.task_relevance if ts else None,
            ts.emotional_valence if ts else None,
            ts.overall if ts else None,
            0.0,
            "active",
            fact.expectation,
            fact.outcome,
            fact.session_mode,
            fact.task_context,
            json.dumps(retain_context_dict) if retain_context_dict is not None else None,
            bank_op_count,
        )


async def update_facts_batch(conn, bank_id: str, replacement_actions: list) -> None:
    """Update memory_units + engram_dictionary rows for REPLACE resolutions.

    Called within the same DB transaction as insert_facts_batch so that
    all writes stay consistent.

    Args:
        conn: Active DB connection (already inside a transaction).
        bank_id: Bank identifier.
        replacement_actions: List of ReplacementAction describing existing Engrams to overwrite.
    """
    for ra in replacement_actions:
        uid = uuid.UUID(ra.existing_unit_id)
        await conn.execute(
            f"UPDATE {fq_table('memory_units')} SET text=$1, embedding=$2 WHERE id=$3 AND bank_id=$4",
            ra.new_fact.fact_text,
            str(ra.new_fact.embedding),
            uid,
            bank_id,
        )
        ts = ra.new_fact.thalamus_scores
        if ts:
            await conn.execute(
                f"""
                UPDATE {fq_table("engram_dictionary")}
                SET novelty=$1, surprise=$2, task_relevance=$3,
                    emotional_valence=$4, thalamus_overall=$5
                WHERE engram_id=$6
                """,
                ts.novelty,
                ts.surprise,
                ts.task_relevance,
                ts.emotional_valence,
                ts.overall,
                uid,
            )


async def ensure_bank_exists(conn, bank_id: str) -> None:
    """
    Ensure bank exists in the database.

    Creates bank with default values if it doesn't exist.

    Args:
        conn: Database connection
        bank_id: Bank identifier
    """
    await conn.execute(
        f"""
        INSERT INTO {fq_table("banks")} (bank_id, disposition, background)
        VALUES ($1, $2::jsonb, $3)
        ON CONFLICT (bank_id) DO UPDATE
        SET updated_at = NOW()
        """,
        bank_id,
        '{"skepticism": 3, "literalism": 3, "empathy": 3}',
        "",
    )


async def handle_document_tracking(
    conn, bank_id: str, document_id: str, combined_content: str, is_first_batch: bool, retain_params: dict | None = None
) -> None:
    """
    Handle document tracking in the database.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        document_id: Document identifier
        combined_content: Combined content text from all content items
        is_first_batch: Whether this is the first batch (for chunked operations)
        retain_params: Optional parameters passed during retain (context, event_date, etc.)
    """
    import hashlib

    # Calculate content hash
    content_hash = hashlib.sha256(combined_content.encode()).hexdigest()

    # Always delete old document first if it exists (cascades to units and links)
    # Only delete on the first batch to avoid deleting data we just inserted
    if is_first_batch:
        await conn.fetchval(
            f"DELETE FROM {fq_table('documents')} WHERE id = $1 AND bank_id = $2 RETURNING id", document_id, bank_id
        )

    # Insert document (or update if exists from concurrent operations)
    await conn.execute(
        f"""
        INSERT INTO {fq_table("documents")} (id, bank_id, original_text, content_hash, metadata, retain_params)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (id, bank_id) DO UPDATE
        SET original_text = EXCLUDED.original_text,
            content_hash = EXCLUDED.content_hash,
            metadata = EXCLUDED.metadata,
            retain_params = EXCLUDED.retain_params,
            updated_at = NOW()
        """,
        document_id,
        bank_id,
        combined_content,
        content_hash,
        json.dumps({}),  # Empty metadata dict
        json.dumps(retain_params) if retain_params else None,
    )
