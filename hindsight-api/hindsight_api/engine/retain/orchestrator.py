"""
Main orchestrator for the retain pipeline.

Coordinates all retain pipeline modules to store memories efficiently.
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from ...config import get_config
from ..db_utils import acquire_with_retry
from ..engram_types import ThalamusScores
from ..llm_routing import LLMRegistry
from ..tracer import PipelineTrace, PipelineTracer
from . import bank_utils


def utcnow():
    """Get current UTC time."""
    return datetime.now(UTC)


from ..response_models import TokenUsage
from . import (
    chunk_storage,
    deduplication,
    embedding_processing,
    entity_processing,
    fact_extraction,
    fact_storage,
    link_creation,
    observation_regeneration,
)
from .types import ExtractedFact, ProcessedFact, RetainContent, RetainContentDict

logger = logging.getLogger(__name__)


async def retain_batch(
    pool,
    embeddings_model,
    llm_registry: LLMRegistry,
    entity_resolver,
    task_backend,
    format_date_fn,
    duplicate_checker_fn,
    bank_id: str,
    contents_dicts: list[RetainContentDict],
    document_id: str | None = None,
    is_first_batch: bool = True,
    fact_type_override: str | None = None,
    confidence_score: float | None = None,
    session=None,
    neo4j_client=None,
    qdrant_client=None,
    budget=None,
    budget_profile=None,
) -> tuple[list[list[str]], TokenUsage]:
    """
    Process a batch of content through the retain pipeline.

    Args:
        pool: Database connection pool
        embeddings_model: Embeddings model for generating embeddings
        llm_registry: LLMRegistry for per-subtask LLM resolution
        entity_resolver: Entity resolver for entity processing
        task_backend: Task backend for background jobs
        format_date_fn: Function to format datetime to readable string
        duplicate_checker_fn: Function to check for duplicate facts
        bank_id: Bank identifier
        contents_dicts: List of content dictionaries
        document_id: Optional document ID
        is_first_batch: Whether this is the first batch
        fact_type_override: Override fact type for all facts
        confidence_score: Confidence score for opinions

    Returns:
        Tuple of (unit ID lists, token usage for fact extraction)
    """
    start_time = time.time()
    total_chars = sum(len(item.get("content", "")) for item in contents_dicts)

    # Observability Phase B — pipeline tracer for the whole retain_batch.
    # Records one TraceStep per pipeline phase via tracer.record_step() at
    # each existing log_buffer.append boundary (see B3). The trace is
    # persisted to the retain_traces table after the PostgreSQL transaction
    # commits — persist failures are logged but never propagated.
    tracer = PipelineTracer(pipeline="retain", bank_id=bank_id)
    tracer.set_metadata("batch_size", len(contents_dicts))
    tracer.set_metadata("total_chars", total_chars)
    if document_id is not None:
        tracer.set_metadata("document_id", document_id)

    # Epic 24: increment bank.op_count and capture the new value so that
    # every engram created in this batch gets created_at_op = current op_count.
    from ..engram_dictionary import get_bank_session_count as _get_sc
    from ..engram_dictionary import increment_bank_op_count as _inc_op

    try:
        _bank_op_count = await _inc_op(pool, bank_id)
    except Exception:
        _bank_op_count = 0

    # Epic 24 Story 06: capture session_count for created_at_session. Session
    # count is only bumped at session close (by MemoryEngine.end_session_async),
    # so reading it here is safe and cheap.
    try:
        _bank_session_count = await _get_sc(pool, bank_id)
    except Exception:
        _bank_session_count = 0

    # Buffer all logs
    log_buffer = []
    log_buffer.append(f"{'=' * 60}")
    log_buffer.append(f"RETAIN_BATCH START: {bank_id}")
    log_buffer.append(f"Batch size: {len(contents_dicts)} content items, {total_chars:,} chars")
    log_buffer.append(f"{'=' * 60}")

    # Get bank profile
    profile = await bank_utils.get_bank_profile(pool, bank_id)
    agent_name = profile["name"]

    # Step R0: Sequence Analysis — decompose rich content into structured units (Epic 15)
    # Runs BEFORE the RetainContent conversion so contents_dicts and contents stay in sync.
    # T5 skip-logic: items with explicit expectation or outcome are passed through as-is.
    if budget is not None:
        from ..utils import Budget as _Budget
        from .sequence_analysis import analyze_sequence, units_to_content_dicts

        if budget_profile is not None:
            # Budget profile path: tier comes from profile, not budget-to-subtask mapping
            _r0_llm = llm_registry.resolve_with_profile("retain", "sequence_analysis", budget_profile)
        else:
            # Backward-compat: use manual budget-to-subtask mapping
            _budget_to_subtask = {
                _Budget.LOW: "sequence_analysis_low",
                _Budget.MID: "sequence_analysis",
                _Budget.HIGH: "sequence_analysis_high",
            }
            _subtask = _budget_to_subtask.get(budget, "sequence_analysis")
            _r0_llm = llm_registry.get_llm("retain", _subtask)

        r0_start = time.time()
        original_count = len(contents_dicts)
        expanded: list[RetainContentDict] = []
        r0_total_units = 0
        r0_usage = TokenUsage()

        for content_dict in contents_dicts:
            if content_dict.get("expectation") or content_dict.get("outcome"):
                # Already structured — pass through as-is
                expanded.append(content_dict)
            else:
                units, unit_usage = await analyze_sequence(
                    content=content_dict.get("content", ""),
                    budget=budget,
                    llm=_r0_llm,
                )
                r0_usage = r0_usage + unit_usage
                r0_total_units += len(units)
                new_dicts = units_to_content_dicts(units, content_dict)
                expanded.extend(new_dicts)

        contents_dicts = expanded
        _r0_duration_ms = (time.time() - r0_start) * 1000
        log_buffer.append(
            f"[R0] Sequence Analysis: {r0_total_units} units from {original_count} items "
            f"(budget={budget.value if hasattr(budget, 'value') else budget}) "
            f"in {_r0_duration_ms / 1000:.3f}s"
        )
        tracer.record_step(
            name="r0_sequence_analysis",
            duration_ms=_r0_duration_ms,
            inputs={
                "num_contents": original_count,
                "budget": budget.value if hasattr(budget, "value") else str(budget),
            },
            outputs={"num_units": r0_total_units, "expanded_count": len(expanded)},
            rationale=(
                f"Decomposed {original_count} contents into {r0_total_units} structured units via sequence analysis LLM"
            ),
        )
    else:
        tracer.record_step(
            name="r0_sequence_analysis",
            duration_ms=0.0,
            status="skipped",
            rationale="no budget configured — sequence analysis disabled",
        )

    # Convert dicts to RetainContent objects (after R0 expansion)
    contents = []
    for item in contents_dicts:
        content = RetainContent(
            content=item["content"],
            context=item.get("context", ""),
            event_date=item.get("event_date") or utcnow(),
            metadata=item.get("metadata", {}),
            entities=item.get("entities", []),
        )
        contents.append(content)

    # Step 1: Extract facts from all contents
    step_start = time.time()
    extract_opinions = fact_type_override == "opinion"

    _r1_llm = (
        llm_registry.resolve_with_profile("retain", "fact_extraction", budget_profile)
        if budget_profile is not None
        else llm_registry.get_llm("retain", "fact_extraction")
    )
    extracted_facts, chunks, usage = await fact_extraction.extract_facts_from_contents(
        contents, _r1_llm, agent_name, extract_opinions
    )
    _r1_duration_ms = (time.time() - step_start) * 1000
    log_buffer.append(
        f"[1] Extract facts: {len(extracted_facts)} facts, {len(chunks)} chunks from {len(contents)} contents in {_r1_duration_ms / 1000:.3f}s"
    )
    tracer.record_step(
        name="r1_fact_extraction",
        duration_ms=_r1_duration_ms,
        inputs={
            "num_contents": len(contents),
            "extract_opinions": extract_opinions,
        },
        outputs={
            "num_facts": len(extracted_facts),
            "num_chunks": len(chunks),
            "llm_usage_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
        },
        rationale=(
            f"LLM extracted {len(extracted_facts)} facts and {len(chunks)} chunks from {len(contents)} contents"
        ),
    )

    # Hybrid Thalamus scoring: merge gate (embedding-based) and LLM scores.
    #
    # - novelty, task_relevance: always from gate (embedding distance is more
    #   reliable than LLM estimation for these dimensions).
    # - surprise, emotional_valence: from gate when expectation+outcome were
    #   provided (real prediction-error computation). When they were NOT
    #   provided the gate falls back to static defaults (0.5 / 0.3) which
    #   carry no signal — in that case we keep the LLM-estimated scores
    #   from fact extraction (no extra cost, they come with the extraction
    #   call). This lets emotionally charged content ("critical incident")
    #   score higher than neutral content ("set timeout to 5s") even without
    #   explicit expectation/outcome.
    for fact in extracted_facts:
        if fact.content_index < len(contents_dicts):
            content_dict = contents_dicts[fact.content_index]
            gate_scores = content_dict.get("thalamus_scores")
            if gate_scores is not None:
                llm_scores = fact.thalamus_scores
                gate_computed_pe = (
                    gate_scores.rationale is not None
                    and gate_scores.rationale.surprise_expectation_provided
                    and gate_scores.rationale.surprise_outcome_provided
                )

                if llm_scores is None or gate_computed_pe:
                    # No LLM scores to fall back to, or gate actually computed
                    # surprise/emotional from real embeddings → use gate entirely
                    fact.thalamus_scores = gate_scores
                else:
                    # Gate used fallback defaults for surprise/emotional —
                    # keep LLM estimates for those, use gate for the rest
                    fact.thalamus_scores = ThalamusScores(
                        novelty=gate_scores.novelty,
                        task_relevance=gate_scores.task_relevance,
                        surprise=llm_scores.surprise,
                        emotional_valence=llm_scores.emotional_valence,
                        overall=gate_scores.overall,
                        rationale=gate_scores.rationale,
                    )
            # Propagate API Enrichment fields (Epic 15)
            if fact.expectation is None and content_dict.get("expectation"):
                fact.expectation = content_dict["expectation"]
            if fact.outcome is None and content_dict.get("outcome"):
                fact.outcome = content_dict["outcome"]
            if not fact.user_tags and content_dict.get("tags"):
                fact.user_tags = content_dict["tags"]

            # Per-content-dict fact_type override (Sequence Analysis, Epic 15 S04):
            # Structured units (EXPERIENCE/ACTION_EFFECT) deterministically carry
            # fact_type='experience', regardless of how the LLM classified the
            # text. See units_to_content_dicts for where the override is set.
            per_dict_override = content_dict.get("_fact_type_override")
            if per_dict_override:
                fact.fact_type = per_dict_override

    if not extracted_facts:
        # Still need to create document if document_id was provided
        async with acquire_with_retry(pool) as conn:
            async with conn.transaction():
                await fact_storage.ensure_bank_exists(conn, bank_id)

                # Handle document tracking even with no facts
                if document_id:
                    combined_content = "\n".join([c.get("content", "") for c in contents_dicts])
                    retain_params = {}
                    if contents_dicts:
                        first_item = contents_dicts[0]
                        if first_item.get("context"):
                            retain_params["context"] = first_item["context"]
                        if first_item.get("event_date"):
                            retain_params["event_date"] = (
                                first_item["event_date"].isoformat()
                                if hasattr(first_item["event_date"], "isoformat")
                                else str(first_item["event_date"])
                            )
                        if first_item.get("metadata"):
                            retain_params["metadata"] = first_item["metadata"]
                    await fact_storage.handle_document_tracking(
                        conn, bank_id, document_id, combined_content, is_first_batch, retain_params
                    )
                else:
                    # Check for per-item document_ids
                    from collections import defaultdict

                    contents_by_doc = defaultdict(list)
                    for idx, content_dict in enumerate(contents_dicts):
                        doc_id = content_dict.get("document_id")
                        if doc_id:
                            contents_by_doc[doc_id].append((idx, content_dict))

                    for doc_id, doc_contents in contents_by_doc.items():
                        combined_content = "\n".join([c.get("content", "") for _, c in doc_contents])
                        retain_params = {}
                        if doc_contents:
                            first_item = doc_contents[0][1]
                            if first_item.get("context"):
                                retain_params["context"] = first_item["context"]
                            if first_item.get("event_date"):
                                retain_params["event_date"] = (
                                    first_item["event_date"].isoformat()
                                    if hasattr(first_item["event_date"], "isoformat")
                                    else str(first_item["event_date"])
                                )
                            if first_item.get("metadata"):
                                retain_params["metadata"] = first_item["metadata"]
                        await fact_storage.handle_document_tracking(
                            conn, bank_id, doc_id, combined_content, is_first_batch, retain_params
                        )

        total_time = time.time() - start_time
        logger.info(
            f"RETAIN_BATCH COMPLETE: 0 facts extracted from {len(contents)} contents in {total_time:.3f}s (document tracked, no facts)"
        )
        tracer.set_metadata("num_units_created", 0)
        tracer.set_metadata("early_return", "no_facts_extracted")
        await _persist_retain_trace(pool, bank_id, tracer.finalize())
        return [[] for _ in contents], usage

    # Apply fact_type_override if provided
    if fact_type_override:
        for fact in extracted_facts:
            fact.fact_type = fact_type_override

    # Step 2: Augment texts and generate embeddings
    step_start = time.time()
    augmented_texts = embedding_processing.augment_texts_with_context(extracted_facts, format_date_fn, session=session)
    embeddings = await embedding_processing.generate_embeddings_batch(embeddings_model, augmented_texts)
    _r2_duration_ms = (time.time() - step_start) * 1000
    log_buffer.append(f"[2] Generate embeddings: {len(embeddings)} embeddings in {_r2_duration_ms / 1000:.3f}s")
    tracer.record_step(
        name="r2_embeddings",
        duration_ms=_r2_duration_ms,
        inputs={"num_texts": len(augmented_texts)},
        outputs={
            "num_embeddings": len(embeddings),
            "embedding_dim": len(embeddings[0]) if embeddings else 0,
        },
        rationale=f"batch-embedded {len(embeddings)} augmented texts",
    )

    # Step 3: Convert to ProcessedFact objects (without chunk_ids yet).
    # Capture retain-time provenance from the active Session so it lands on
    # the engram_dictionary row (Phase A3 — Apr 2026):
    #   - session_mode  : the mode the agent was in when this fact was retained
    #   - task_context  : the Session.task_context snapshot
    # Both are None when there is no session (e.g. background ingests) and
    # render as "—" in the CP memory detail panel.
    session_mode_str: str | None = None
    session_task_context: str | None = None
    if session is not None:
        # session.mode is a RetrievalMode enum — .value gives us the lower-case
        # string we want in the DB column.
        mode_attr = getattr(session, "mode", None)
        if mode_attr is not None:
            session_mode_str = getattr(mode_attr, "value", str(mode_attr))
        session_task_context = getattr(session, "task_context", None)

    processed_facts = [
        ProcessedFact.from_extracted_fact(
            extracted_fact,
            embedding,
            session_mode=session_mode_str,
            task_context=session_task_context,
        )
        for extracted_fact, embedding in zip(extracted_facts, embeddings)
    ]

    # Track document IDs for logging
    document_ids_added = []

    # Group contents by document_id for document tracking and chunk storage
    from collections import defaultdict

    contents_by_doc = defaultdict(list)
    for idx, content_dict in enumerate(contents_dicts):
        doc_id = content_dict.get("document_id")
        contents_by_doc[doc_id].append((idx, content_dict))

    # Step 4: Database transaction
    async with acquire_with_retry(pool) as conn:
        async with conn.transaction():
            # Ensure bank exists
            await fact_storage.ensure_bank_exists(conn, bank_id)

            # Handle document tracking for all documents
            step_start = time.time()
            # Map None document_id to generated UUIDs
            doc_id_mapping = {}  # Maps original doc_id (including None) to actual doc_id used

            if document_id:
                # Legacy: single document_id parameter
                combined_content = "\n".join([c.get("content", "") for c in contents_dicts])
                retain_params = {}
                if contents_dicts:
                    first_item = contents_dicts[0]
                    if first_item.get("context"):
                        retain_params["context"] = first_item["context"]
                    if first_item.get("event_date"):
                        retain_params["event_date"] = (
                            first_item["event_date"].isoformat()
                            if hasattr(first_item["event_date"], "isoformat")
                            else str(first_item["event_date"])
                        )
                    if first_item.get("metadata"):
                        retain_params["metadata"] = first_item["metadata"]

                await fact_storage.handle_document_tracking(
                    conn, bank_id, document_id, combined_content, is_first_batch, retain_params
                )
                document_ids_added.append(document_id)
                doc_id_mapping[None] = document_id  # For backwards compatibility
            else:
                # Handle per-item document_ids (create documents if any item has document_id or if chunks exist)
                has_any_doc_ids = any(item.get("document_id") for item in contents_dicts)

                if has_any_doc_ids or chunks:
                    for original_doc_id, doc_contents in contents_by_doc.items():
                        actual_doc_id = original_doc_id

                        # Only create document record if:
                        # 1. Item has explicit document_id, OR
                        # 2. There are chunks (need document for chunk storage)
                        should_create_doc = (original_doc_id is not None) or chunks

                        if should_create_doc:
                            if actual_doc_id is None:
                                # No document_id but have chunks - generate one
                                actual_doc_id = str(uuid.uuid4())

                            # Store mapping for later use
                            doc_id_mapping[original_doc_id] = actual_doc_id

                            # Combine content for this document
                            combined_content = "\n".join([c.get("content", "") for _, c in doc_contents])

                            # Extract retain params from first content item
                            retain_params = {}
                            if doc_contents:
                                first_item = doc_contents[0][1]
                                if first_item.get("context"):
                                    retain_params["context"] = first_item["context"]
                                if first_item.get("event_date"):
                                    retain_params["event_date"] = (
                                        first_item["event_date"].isoformat()
                                        if hasattr(first_item["event_date"], "isoformat")
                                        else str(first_item["event_date"])
                                    )
                                if first_item.get("metadata"):
                                    retain_params["metadata"] = first_item["metadata"]

                            await fact_storage.handle_document_tracking(
                                conn, bank_id, actual_doc_id, combined_content, is_first_batch, retain_params
                            )
                            document_ids_added.append(actual_doc_id)

            if document_ids_added:
                log_buffer.append(
                    f"[2.5] Document tracking: {len(document_ids_added)} documents in {time.time() - step_start:.3f}s"
                )

            # Store chunks and map to facts for all documents
            step_start = time.time()
            chunk_id_map_by_doc = {}  # Maps (doc_id, chunk_index) -> chunk_id

            if chunks:
                # Group chunks by their source document
                chunks_by_doc = defaultdict(list)
                for chunk in chunks:
                    # chunk.content_index tells us which content this chunk came from
                    original_doc_id = contents_dicts[chunk.content_index].get("document_id")
                    # Map to actual document_id (handles None -> generated UUID mapping)
                    actual_doc_id = doc_id_mapping.get(original_doc_id, original_doc_id)
                    if actual_doc_id is None and document_id:
                        actual_doc_id = document_id
                    chunks_by_doc[actual_doc_id].append(chunk)

                # Store chunks for each document
                for doc_id, doc_chunks in chunks_by_doc.items():
                    chunk_id_map = await chunk_storage.store_chunks_batch(conn, bank_id, doc_id, doc_chunks)
                    # Store mapping with document context
                    for chunk_idx, chunk_id in chunk_id_map.items():
                        chunk_id_map_by_doc[(doc_id, chunk_idx)] = chunk_id

                log_buffer.append(
                    f"[3] Store chunks: {len(chunks)} chunks for {len(chunks_by_doc)} documents in {time.time() - step_start:.3f}s"
                )

                # Map chunk_ids and document_ids to facts
                for fact, processed_fact in zip(extracted_facts, processed_facts):
                    # Get the original document_id for this fact's source content
                    original_doc_id = contents_dicts[fact.content_index].get("document_id")
                    # Map to actual document_id (handles None -> generated UUID mapping)
                    actual_doc_id = doc_id_mapping.get(original_doc_id, original_doc_id)
                    if actual_doc_id is None and document_id:
                        actual_doc_id = document_id

                    # Set document_id on the fact
                    processed_fact.document_id = actual_doc_id

                    # Map chunk_id if this fact came from a chunk
                    if fact.chunk_index is not None:
                        # Look up chunk_id using (doc_id, chunk_index)
                        chunk_id = chunk_id_map_by_doc.get((actual_doc_id, fact.chunk_index))
                        if chunk_id:
                            processed_fact.chunk_id = chunk_id
            else:
                # No chunks - still need to set document_id on facts
                for fact, processed_fact in zip(extracted_facts, processed_facts):
                    original_doc_id = contents_dicts[fact.content_index].get("document_id")
                    # Map to actual document_id (handles None -> generated UUID mapping)
                    actual_doc_id = doc_id_mapping.get(original_doc_id, original_doc_id)
                    if actual_doc_id is None and document_id:
                        actual_doc_id = document_id
                    processed_fact.document_id = actual_doc_id

            # Deduplication (score-aware)
            step_start = time.time()
            dup_results = await deduplication.check_duplicates_batch(
                conn, bank_id, processed_facts, duplicate_checker_fn
            )
            facts_to_store, replacement_actions = deduplication.resolve_duplicates_batch(processed_facts, dup_results)
            drop_count = sum(1 for dr in dup_results if dr.is_duplicate) - len(replacement_actions)
            _r3_duration_ms = (time.time() - step_start) * 1000
            log_buffer.append(
                f"[4] Deduplication: {drop_count} dropped, {len(replacement_actions)} replaced"
                f" in {_r3_duration_ms / 1000:.3f}s"
            )
            tracer.record_step(
                name="r3_deduplication",
                duration_ms=_r3_duration_ms,
                inputs={"num_facts": len(processed_facts)},
                outputs={
                    "num_new": len(facts_to_store) - len(replacement_actions),
                    "num_dropped": drop_count,
                    "num_replaced": len(replacement_actions),
                },
                rationale=(
                    f"score-aware dedup kept {len(facts_to_store)}, dropped {drop_count}, "
                    f"replaced {len(replacement_actions)}"
                ),
            )

            non_duplicate_facts = facts_to_store
            if not non_duplicate_facts:
                return [[] for _ in contents], usage

            # REPLACE: update existing Engrams in place
            if replacement_actions:
                await fact_storage.update_facts_batch(conn, bank_id, replacement_actions)

            # KEEP: insert genuinely new facts
            replace_set = {id(ra.new_fact) for ra in replacement_actions}
            keep_facts = [f for f in facts_to_store if id(f) not in replace_set]

            step_start = time.time()
            new_unit_ids = await fact_storage.insert_facts_batch(
                conn,
                bank_id,
                keep_facts,
                bank_op_count=_bank_op_count,
                bank_session_count=_bank_session_count,
            )
            _r5_duration_ms = (time.time() - step_start) * 1000
            log_buffer.append(f"[5] Insert facts: {len(new_unit_ids)} units in {_r5_duration_ms / 1000:.3f}s")
            tracer.record_step(
                name="r5_insert_batch",
                duration_ms=_r5_duration_ms,
                inputs={"num_to_insert": len(keep_facts)},
                outputs={
                    "num_inserted": len(new_unit_ids),
                    # Truncate id list — trace_data must stay small.
                    "unit_ids_sample": [str(u) for u in new_unit_ids[:5]],
                },
                rationale=f"persisted {len(new_unit_ids)} engrams to memory_units + engram_dictionary",
            )

            # Build unified unit_ids in facts_to_store order (KEEP → new id, REPLACE → existing id)
            fact_id_map: dict[int, str] = {}
            for fact, uid in zip(keep_facts, new_unit_ids):
                fact_id_map[id(fact)] = uid
            for ra in replacement_actions:
                fact_id_map[id(ra.new_fact)] = ra.existing_unit_id
            unit_ids = [fact_id_map[id(f)] for f in facts_to_store]

            # Mirror engram embeddings into Qdrant (concept §3 — engram
            # points carry payload.kind="engram"). Without this the
            # Thalamus novelty check and C2 cluster detection both run
            # blind against an empty collection. Best-effort: a Qdrant
            # failure does not roll back the PG transaction; the next
            # successful retain re-upserts the same point id.
            if qdrant_client is not None and new_unit_ids:
                points = [
                    {
                        "engram_id": uid,
                        "embedding": list(fact.embedding),
                        "payload": {
                            "bank_id": bank_id,
                            "text": (fact.fact_text or "")[:200],
                            "fact_type": fact.fact_type,
                            "tags": list(getattr(fact, "tags", None) or []),
                        },
                    }
                    for uid, fact in zip(new_unit_ids, keep_facts)
                    if fact.embedding is not None
                ]
                if points:
                    try:
                        await qdrant_client.batch_upsert(points)
                    except Exception as exc:  # noqa: BLE001 — best-effort mirror
                        logger.warning(
                            "Retain-side Qdrant batch_upsert failed for bank=%s n=%d: %s",
                            bank_id,
                            len(points),
                            exc,
                        )

            # Process entities
            step_start = time.time()
            # Build map of content_index -> user entities for merging
            user_entities_per_content = {
                idx: content.entities for idx, content in enumerate(contents) if content.entities
            }
            entity_links = await entity_processing.process_entities_batch(
                entity_resolver,
                conn,
                bank_id,
                unit_ids,
                non_duplicate_facts,
                log_buffer,
                user_entities_per_content=user_entities_per_content,
                llm=(
                    llm_registry.resolve_with_profile("retain", "entity_disambiguation", budget_profile)
                    if (llm_registry and budget_profile is not None)
                    else (llm_registry.get_llm("retain", "entity_disambiguation") if llm_registry else None)
                ),
            )
            _r6_duration_ms = (time.time() - step_start) * 1000
            log_buffer.append(f"[6] Process entities: {len(entity_links)} links in {_r6_duration_ms / 1000:.3f}s")
            tracer.record_step(
                name="r6_entity_processing",
                duration_ms=_r6_duration_ms,
                inputs={"num_facts": len(non_duplicate_facts)},
                outputs={
                    "num_entity_links": len(entity_links),
                    "unique_entities": len({link.entity_id for link in entity_links}) if entity_links else 0,
                },
                rationale=(
                    f"resolved {len({link.entity_id for link in entity_links}) if entity_links else 0} "
                    f"unique entities, created {len(entity_links)} unit-entity links"
                ),
            )

            # Create temporal links
            _r7_start = time.time()
            step_start = _r7_start
            temporal_link_count = await link_creation.create_temporal_links_batch(conn, bank_id, unit_ids)
            log_buffer.append(f"[7] Temporal links: {temporal_link_count} links in {time.time() - step_start:.3f}s")

            # Create semantic links
            step_start = time.time()
            embeddings_for_links = [fact.embedding for fact in non_duplicate_facts]
            semantic_link_count = await link_creation.create_semantic_links_batch(
                conn, bank_id, unit_ids, embeddings_for_links
            )
            log_buffer.append(f"[8] Semantic links: {semantic_link_count} links in {time.time() - step_start:.3f}s")

            # Insert entity links (PostgreSQL)
            step_start = time.time()
            if entity_links:
                await entity_processing.insert_entity_links_batch(conn, entity_links)
            log_buffer.append(
                f"[9] Entity links: {len(entity_links) if entity_links else 0} links in {time.time() - step_start:.3f}s"
            )

            # Dual-write: Entity nodes + ENTITY relationships in Neo4j (R4/T4+T5)
            # Runs outside the PostgreSQL transaction — eventual consistency, errors logged only.
            if neo4j_client is not None and entity_links:
                import uuid as _uuid

                from ..memory_engine import fq_table

                unique_entity_ids = list({str(link.entity_id) for link in entity_links})
                try:
                    entity_rows = await conn.fetch(
                        f"SELECT id::text AS entity_id, canonical_name"
                        f" FROM {fq_table('entities')} WHERE id = ANY($1::uuid[])",
                        [_uuid.UUID(eid) for eid in unique_entity_ids],
                    )
                    neo4j_entities = [
                        {"entity_id": r["entity_id"], "canonical_name": r["canonical_name"], "type": "CONCEPT"}
                        for r in entity_rows
                    ]
                    await entity_processing.create_entity_nodes_batch(neo4j_client, neo4j_entities)
                except Exception as _exc:
                    logger.warning(f"Neo4j entity node query/write failed: {_exc}")
                await entity_processing.create_entity_relationships_batch(neo4j_client, entity_links)

            # Create causal links
            step_start = time.time()
            causal_link_count = await link_creation.create_causal_links_batch(conn, unit_ids, non_duplicate_facts)
            log_buffer.append(f"[10] Causal links: {causal_link_count} links in {time.time() - step_start:.3f}s")

            # Create temporal proximity links (STC — intra-session window)
            step_start = time.time()
            tp_link_count = await link_creation.create_temporal_proximity_links_batch(conn, bank_id, unit_ids)
            log_buffer.append(
                f"[11] Temporal proximity links: {tp_link_count} links in {time.time() - step_start:.3f}s"
            )

            _r7_duration_ms = (time.time() - _r7_start) * 1000
            tracer.record_step(
                name="r7_link_creation",
                duration_ms=_r7_duration_ms,
                inputs={"num_units": len(unit_ids)},
                outputs={
                    "temporal": temporal_link_count,
                    "semantic": semantic_link_count,
                    "entity": len(entity_links) if entity_links else 0,
                    "causal": causal_link_count,
                    "temporal_proximity": tp_link_count,
                },
                rationale=(
                    f"wrote {temporal_link_count} temporal, {semantic_link_count} semantic, "
                    f"{len(entity_links) if entity_links else 0} entity, {causal_link_count} causal, "
                    f"{tp_link_count} temporal_proximity links"
                ),
            )

            # Dual-write: all link types to Neo4j (eventual consistency, errors logged only)
            if neo4j_client is not None:
                import asyncio as _asyncio

                from ..memory_engine import fq_table as _fq_table
                from .neo4j_link_writer import LinkRecord as _LinkRecord
                from .neo4j_link_writer import write_links_to_neo4j as _write_links_to_neo4j
                from .schema_links import check_schema_fit_batch as _check_schema_fit_batch
                from .schema_links import write_schema_links as _write_schema_links

                # Fetch all non-entity links for the newly retained unit_ids
                _neo4j_links: list[_LinkRecord] = []
                try:
                    _link_rows = await conn.fetch(
                        f"SELECT from_unit_id::text AS from_id, to_unit_id::text AS to_id,"
                        f"       link_type, weight"
                        f" FROM {_fq_table('memory_links')}"
                        f" WHERE from_unit_id::text = ANY($1)"
                        f"   AND link_type IN ('temporal', 'semantic', 'causal', 'temporal_proximity')",
                        unit_ids,
                    )
                    _neo4j_links = [
                        _LinkRecord(
                            from_id=r["from_id"],
                            to_id=r["to_id"],
                            rel_type=r["link_type"],
                            weight=float(r["weight"]),
                        )
                        for r in _link_rows
                    ]
                except Exception as _exc:
                    logger.warning(f"Neo4j link fetch failed: {_exc}")

                # Check schema fit for newly retained Engrams
                _r8_start = time.time()
                _embeddings_for_schema = [fact.embedding for fact in non_duplicate_facts]
                _schema_links = await _check_schema_fit_batch(neo4j_client, unit_ids, _embeddings_for_schema)

                # Parallel writes: regular links + schema links
                await _asyncio.gather(
                    _write_links_to_neo4j(neo4j_client, _neo4j_links),
                    _write_schema_links(neo4j_client, _schema_links),
                )
                _r8_duration_ms = (time.time() - _r8_start) * 1000
                log_buffer.append(
                    f"[12] Neo4j dual-write: {len(_neo4j_links)} links, {len(_schema_links)} schema links"
                )
                tracer.record_step(
                    name="r8_schema_fit",
                    duration_ms=_r8_duration_ms,
                    inputs={
                        "num_units": len(unit_ids),
                        "num_regular_links": len(_neo4j_links),
                    },
                    outputs={
                        "num_schema_links": len(_schema_links),
                        "num_schemas_touched": len({link.schema_id for link in _schema_links}) if _schema_links else 0,
                    },
                    rationale=(
                        f"incremental R4 reinforcement: {len(_schema_links)} schema-fit links across "
                        f"{len({link.schema_id for link in _schema_links}) if _schema_links else 0} schemas"
                    ),
                )

                # Step 12.5: Experience + causal links
                #   * CAUSAL (ACTION_EFFECT pairs from R0 sequence analysis)
                #   * CAUSAL (LLM-extracted causal_relations mirrored from Postgres
                #     memory_links so free-text causal chains are visible in Neo4j)
                #   * PREDICTION_ERROR (EXPERIENCE pairs with expectation↔outcome divergence)
                # ACTION_EFFECT + PREDICTION_ERROR only run when R0 was active (budget set)
                # and pair-key markers are present; the LLM causal mirror runs whenever
                # the extractor returned any causal_relations — independent of budget.
                from .experience_links import build_causal_links as _build_causal_links
                from .experience_links import build_llm_causal_links as _build_llm_causal_links
                from .experience_links import build_prediction_error_links as _build_prediction_error_links

                # Build content_dict_index → first retained unit_id mapping
                _content_to_uid: dict[int, str] = {}
                for _pf, _ef in zip(processed_facts, extracted_facts):
                    _uid = fact_id_map.get(id(_pf))
                    if _uid and _ef.content_index not in _content_to_uid:
                        _content_to_uid[_ef.content_index] = _uid

                _aligned_eids: list[str | None] = [_content_to_uid.get(i) for i in range(len(contents_dicts))]

                _causal_links = _build_causal_links(contents_dicts, _aligned_eids)

                # Mirror LLM causal_relations to Neo4j. unit_ids and non_duplicate_facts
                # share ordering by construction (see create_causal_links_batch caller).
                _llm_causal_links = _build_llm_causal_links(unit_ids, non_duplicate_facts)

                async def _embed_texts(texts: list[str]) -> list[list[float]]:
                    return await embedding_processing.generate_embeddings_batch(embeddings_model, texts)

                _pe_links = await _build_prediction_error_links(contents_dicts, _aligned_eids, _embed_texts)

                _experience_links = _causal_links + _llm_causal_links + _pe_links
                if _experience_links:
                    await _write_links_to_neo4j(neo4j_client, _experience_links)
                    log_buffer.append(
                        f"[12.5] Experience links: {len(_causal_links)} CAUSAL (action/effect), "
                        f"{len(_llm_causal_links)} CAUSAL (llm), {len(_pe_links)} PREDICTION_ERROR"
                    )

            # Regenerate observations - sync (in transaction) or async (background task)
            _r9_start = time.time()
            config = get_config()
            if config.retain_observations_async:
                # Queue for async processing after transaction commits
                entity_ids_for_async = list(set(link.entity_id for link in entity_links)) if entity_links else []
                log_buffer.append(
                    f"[11] Observations: queued {len(entity_ids_for_async)} entities for async processing"
                )
                tracer.record_step(
                    name="r9_observations",
                    duration_ms=(time.time() - _r9_start) * 1000,
                    inputs={"num_entities": len(entity_ids_for_async)},
                    outputs={"mode": "async"},
                    rationale=(
                        f"queued {len(entity_ids_for_async)} entities for background "
                        f"observation regeneration (non-blocking)"
                    ),
                )
            else:
                # Run synchronously inside transaction for atomicity
                _obs_llm = (
                    llm_registry.resolve_with_profile("retain", "observation_synthesis", budget_profile)
                    if budget_profile is not None
                    else llm_registry.get_llm("retain", "observation_synthesis")
                )
                # Propagate the triggering session's provenance so observations
                # inherit session_mode + task_context on their engram_dictionary row.
                # Also collect all unique parent tags from the facts so observations
                # are discoverable via the same tags as their source facts.
                _parent_tags: list[str] = []
                _seen_tags: set[str] = set()
                for _pf in processed_facts:
                    for _tag in _pf.tags or []:
                        if _tag not in _seen_tags:
                            _seen_tags.add(_tag)
                            _parent_tags.append(_tag)
                await observation_regeneration.regenerate_observations_batch(
                    conn,
                    embeddings_model,
                    _obs_llm,
                    bank_id,
                    entity_links,
                    log_buffer,
                    session_mode=session_mode_str,
                    session_task_context=session_task_context,
                    parent_tags=_parent_tags,
                    bank_op_count=_bank_op_count,
                    bank_session_count=_bank_session_count,
                )
                entity_ids_for_async = []
                tracer.record_step(
                    name="r9_observations",
                    duration_ms=(time.time() - _r9_start) * 1000,
                    inputs={"num_entity_links": len(entity_links) if entity_links else 0},
                    outputs={"mode": "sync"},
                    rationale="regenerated observations synchronously inside transaction",
                )

            # Build dropped flags (DROP=True, KEEP/REPLACE=False) for result mapping
            is_dropped_flags = [
                deduplication.resolve_duplicate(fact, dr) == deduplication.DuplicateResolution.DROP
                for fact, dr in zip(processed_facts, dup_results)
            ]
            # Map results back to original content items
            result_unit_ids = _map_results_to_contents(contents, extracted_facts, is_dropped_flags, unit_ids)

        # Trigger background tasks AFTER transaction commits
        await _trigger_background_tasks(task_backend, bank_id, unit_ids, non_duplicate_facts, entity_ids_for_async)

        # Log final summary
        total_time = time.time() - start_time
        log_buffer.append(f"{'=' * 60}")
        log_buffer.append(f"RETAIN_BATCH COMPLETE: {len(unit_ids)} units in {total_time:.3f}s")
        if document_ids_added:
            log_buffer.append(f"Documents: {', '.join(document_ids_added)}")
        log_buffer.append(f"{'=' * 60}")

        logger.info("\n" + "\n".join(log_buffer) + "\n")

        # Persist the pipeline trace — non-blocking, errors are logged but
        # never propagated so a trace-write failure cannot break retain.
        tracer.set_metadata("num_units_created", len(unit_ids))
        await _persist_retain_trace(pool, bank_id, tracer.finalize())

        return result_unit_ids, usage


def _map_results_to_contents(
    contents: list[RetainContent],
    extracted_facts: list[ExtractedFact],
    is_dropped_flags: list[bool],
    unit_ids: list[str],
) -> list[list[str]]:
    """
    Map created unit IDs back to original content items.

    Accounts for dropped facts (duplicates where existing wins) when mapping back.
    REPLACE facts count as non-dropped — their existing_unit_id is included in unit_ids.
    """
    result_unit_ids = []
    filtered_idx = 0

    # Group facts by content_index
    facts_by_content = {i: [] for i in range(len(contents))}
    for i, fact in enumerate(extracted_facts):
        facts_by_content[fact.content_index].append(i)

    for content_index in range(len(contents)):
        content_unit_ids = []
        for fact_idx in facts_by_content[content_index]:
            if not is_dropped_flags[fact_idx]:
                content_unit_ids.append(unit_ids[filtered_idx])
                filtered_idx += 1
        result_unit_ids.append(content_unit_ids)

    return result_unit_ids


async def _persist_retain_trace(pool, bank_id: str, trace: PipelineTrace) -> None:
    """Write a finalized PipelineTrace into the retain_traces table.

    Non-blocking: on failure the exception is swallowed and logged. A broken
    trace-write must never be able to kill an otherwise-successful retain.
    """
    try:
        from ..memory_engine import fq_table

        duration_ms = int(trace.duration_ms)
        async with acquire_with_retry(pool) as conn:
            await conn.execute(
                f"""
                INSERT INTO {fq_table("retain_traces")}
                    (bank_id, operation_id, started_at, duration_ms, trace_data)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                bank_id,
                None,  # operation_id: no operation-id threading in the current retain_batch signature
                trace.started_at,
                duration_ms,
                json.dumps(trace.to_dict(), default=str),
            )
    except Exception as exc:  # noqa: BLE001 — non-blocking by design
        logger.warning(f"retain_trace persist failed (non-blocking): {exc}")


async def _trigger_background_tasks(
    task_backend,
    bank_id: str,
    unit_ids: list[str],
    facts: list[ProcessedFact],
    entity_ids_for_observations: list[str] | None = None,
) -> None:
    """Trigger background tasks after transaction commits."""
    # Trigger opinion reinforcement if there are entities
    fact_entities = [[e.name for e in fact.entities] for fact in facts]
    if any(fact_entities):
        await task_backend.submit_task(
            {
                "type": "reinforce_opinion",
                "bank_id": bank_id,
                "created_unit_ids": unit_ids,
                "unit_texts": [fact.fact_text for fact in facts],
                "unit_entities": fact_entities,
            }
        )

    # Trigger observation regeneration if async mode is enabled
    if entity_ids_for_observations:
        await task_backend.submit_task(
            {
                "type": "regenerate_observations",
                "bank_id": bank_id,
                "entity_ids": entity_ids_for_observations,
            }
        )
