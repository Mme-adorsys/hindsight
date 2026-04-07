"""
Main orchestrator for the retain pipeline.

Coordinates all retain pipeline modules to store memories efficiently.
"""

import logging
import time
import uuid
from datetime import UTC, datetime

from ...config import get_config
from ..db_utils import acquire_with_retry
from ..llm_routing import LLMRegistry
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

    # Buffer all logs
    log_buffer = []
    log_buffer.append(f"{'=' * 60}")
    log_buffer.append(f"RETAIN_BATCH START: {bank_id}")
    log_buffer.append(f"Batch size: {len(contents_dicts)} content items, {total_chars:,} chars")
    log_buffer.append(f"{'=' * 60}")

    # Get bank profile
    profile = await bank_utils.get_bank_profile(pool, bank_id)
    agent_name = profile["name"]

    # Convert dicts to RetainContent objects
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

    extracted_facts, chunks, usage = await fact_extraction.extract_facts_from_contents(
        contents, llm_registry.get_llm("retain", "fact_extraction"), agent_name, extract_opinions
    )
    log_buffer.append(
        f"[1] Extract facts: {len(extracted_facts)} facts, {len(chunks)} chunks from {len(contents)} contents in {time.time() - step_start:.3f}s"
    )

    # Apply gate ThalamusScores to facts where the LLM did not produce scores.
    # Gate scores (heuristic) are the baseline; LLM-provided scores take precedence.
    for fact in extracted_facts:
        if fact.content_index < len(contents_dicts):
            content_dict = contents_dicts[fact.content_index]
            if fact.thalamus_scores is None:
                gate_scores = content_dict.get("thalamus_scores")
                if gate_scores is not None:
                    fact.thalamus_scores = gate_scores
            # Propagate API Enrichment fields (Epic 15)
            if fact.expectation is None and content_dict.get("expectation"):
                fact.expectation = content_dict["expectation"]
            if fact.outcome is None and content_dict.get("outcome"):
                fact.outcome = content_dict["outcome"]
            if not fact.user_tags and content_dict.get("tags"):
                fact.user_tags = content_dict["tags"]

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
        return [[] for _ in contents], usage

    # Apply fact_type_override if provided
    if fact_type_override:
        for fact in extracted_facts:
            fact.fact_type = fact_type_override

    # Step 2: Augment texts and generate embeddings
    step_start = time.time()
    augmented_texts = embedding_processing.augment_texts_with_context(extracted_facts, format_date_fn, session=session)
    embeddings = await embedding_processing.generate_embeddings_batch(embeddings_model, augmented_texts)
    log_buffer.append(f"[2] Generate embeddings: {len(embeddings)} embeddings in {time.time() - step_start:.3f}s")

    # Step 3: Convert to ProcessedFact objects (without chunk_ids yet)
    processed_facts = [
        ProcessedFact.from_extracted_fact(extracted_fact, embedding)
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
            log_buffer.append(
                f"[4] Deduplication: {drop_count} dropped, {len(replacement_actions)} replaced"
                f" in {time.time() - step_start:.3f}s"
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
            new_unit_ids = await fact_storage.insert_facts_batch(conn, bank_id, keep_facts)
            log_buffer.append(f"[5] Insert facts: {len(new_unit_ids)} units in {time.time() - step_start:.3f}s")

            # Build unified unit_ids in facts_to_store order (KEEP → new id, REPLACE → existing id)
            fact_id_map: dict[int, str] = {}
            for fact, uid in zip(keep_facts, new_unit_ids):
                fact_id_map[id(fact)] = uid
            for ra in replacement_actions:
                fact_id_map[id(ra.new_fact)] = ra.existing_unit_id
            unit_ids = [fact_id_map[id(f)] for f in facts_to_store]

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
                llm=llm_registry.get_llm("retain", "entity_disambiguation") if llm_registry else None,
            )
            log_buffer.append(f"[6] Process entities: {len(entity_links)} links in {time.time() - step_start:.3f}s")

            # Create temporal links
            step_start = time.time()
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
                _embeddings_for_schema = [fact.embedding for fact in non_duplicate_facts]
                _schema_links = await _check_schema_fit_batch(neo4j_client, unit_ids, _embeddings_for_schema)

                # Parallel writes: regular links + schema links
                await _asyncio.gather(
                    _write_links_to_neo4j(neo4j_client, _neo4j_links),
                    _write_schema_links(neo4j_client, _schema_links),
                )
                log_buffer.append(
                    f"[12] Neo4j dual-write: {len(_neo4j_links)} links, {len(_schema_links)} schema links"
                )

            # Regenerate observations - sync (in transaction) or async (background task)
            config = get_config()
            if config.retain_observations_async:
                # Queue for async processing after transaction commits
                entity_ids_for_async = list(set(link.entity_id for link in entity_links)) if entity_links else []
                log_buffer.append(
                    f"[11] Observations: queued {len(entity_ids_for_async)} entities for async processing"
                )
            else:
                # Run synchronously inside transaction for atomicity
                await observation_regeneration.regenerate_observations_batch(
                    conn,
                    embeddings_model,
                    llm_registry.get_llm("retain", "observation_synthesis"),
                    bank_id,
                    entity_links,
                    log_buffer,
                )
                entity_ids_for_async = []

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
