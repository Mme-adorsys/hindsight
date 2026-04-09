"""
RecallOrchestrator — Memory retrieval pipeline.

Extracted from MemoryEngine (God Object decomposition).
All methods delegate shared infrastructure to EngineContext.

Covers:
  - recall() sync wrapper
  - recall_async() — main retrieval with WorkingContext + Construction + PE detection
  - _search_with_retries() — 4-way parallel retrieval, RRF, reranking, token budget
  - _filter_by_token_budget() — tiktoken-based budget enforcement

Bio mapping:
- 4-way parallel retrieval → CA3 pattern completion from multiple cues
- RRF merge → hippocampal binding of co-active representations
- Cross-encoder reranking → PFC relevance evaluation
- Construction pipeline → PFC semantic integration
- PE detection → dopaminergic mismatch signal
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from .response_models import (
    VALID_RECALL_FACT_TYPES,
    EntityObservation,
    EntityState,
    MemoryFact,
)
from .response_models import RecallResult as RecallResultModel
from .utils import Budget, _get_tiktoken_encoding, acquire_with_retry, fq_table, utcnow

if TYPE_CHECKING:
    from hindsight_api.models import RequestContext

    from .engine_context import EngineContext
    from .response_models import Session
    from .search.types import ScoredResult

logger = logging.getLogger(__name__)


class RecallOrchestrator:
    # Maximum retry attempts for transient connection errors during search
    MAX_SEARCH_RETRIES = 3

    def __init__(self, ctx: "EngineContext") -> None:
        self._ctx = ctx

    def recall(
        self,
        bank_id: str,
        query: str,
        fact_type: str,
        budget: Budget = Budget.MID,
        max_tokens: int = 4096,
        enable_trace: bool = False,
    ) -> tuple[list[dict[str, Any]], Any | None]:
        """
        Recall memories using 4-way parallel retrieval (synchronous wrapper).

        This is a synchronous wrapper around recall_async() for convenience.
        For best performance, use recall_async() directly.

        Args:
            bank_id: bank ID to recall for
            query: Recall query
            fact_type: Required filter for fact type ('world', 'experience', or 'opinion')
            budget: Budget level for graph traversal (low=100, mid=300, high=600 units)
            max_tokens: Maximum tokens to return (counts only 'text' field, default 4096)
            enable_trace: If True, returns detailed trace object

        Returns:
            Tuple of (results, trace)
        """
        # Run async version synchronously - deprecated sync method, passing None for request_context
        from hindsight_api.models import RequestContext

        return asyncio.run(
            self.recall_async(
                bank_id,
                query,
                budget=budget,
                max_tokens=max_tokens,
                enable_trace=enable_trace,
                fact_type=[fact_type],
                request_context=RequestContext(),
            )
        )

    async def recall_async(
        self,
        bank_id: str,
        query: str,
        *,
        budget: Budget | None = None,
        max_tokens: int = 4096,
        enable_trace: bool = False,
        fact_type: list[str] | None = None,
        question_date: datetime | None = None,
        include_entities: bool = False,
        max_entity_tokens: int = 500,
        include_chunks: bool = False,
        max_chunk_tokens: int = 8192,
        session: "Session | None" = None,
        request_context: "RequestContext",
        tags: list[str] | None = None,
        shared_bank_id: str | None = None,
        expectation: str | None = None,
    ) -> RecallResultModel:
        """
        Recall memories using 4-way parallel retrieval with optional Engram tag filtering.

        This implements the core RECALL operation:
        1. Retrieval: Single 4-way parallel retrieval (semantic vector, BM25 keyword, graph activation, temporal graph)
        2. Merge: Combine using Reciprocal Rank Fusion (RRF)
        3. Rerank: Score using selected reranker (heuristic or cross-encoder)
        4. Diversify: Apply MMR for diversity
        5. Token Filter: Return results up to max_tokens budget

        Args:
            bank_id: bank ID to recall for
            query: Recall query
            fact_type: List of fact types to recall (e.g., ['world', 'experience'])
            budget: Budget level for graph traversal (low=100, mid=300, high=600 units)
            max_tokens: Maximum tokens to return (counts only 'text' field, default 4096)
                       Results are returned until token budget is reached, stopping before
                       including a fact that would exceed the limit
            enable_trace: Whether to return trace for debugging (deprecated)
            question_date: Optional date when question was asked (for temporal filtering)
            include_entities: Whether to include entity observations in the response
            max_entity_tokens: Maximum tokens for entity observations (default 500)
            include_chunks: Whether to include raw chunks in the response
            max_chunk_tokens: Maximum tokens for chunks (default 8192)

        Returns:
            RecallResultModel containing:
            - results: List of MemoryFact objects
            - trace: Optional trace information for debugging
            - entities: Optional dict of entity states (if include_entities=True)
            - chunks: Optional dict of chunks (if include_chunks=True)
        """
        # Authenticate tenant and set schema in context (for fq_table())
        await self._ctx.authenticate_tenant(request_context)

        # Resolve ModeConfig for session-aware retrieval (Epic 06 wire-through; used in Epic 07)
        mode_config = self._ctx.resolve_session_config(session)
        retrieval_mode = session.mode if session else None
        logger.debug(
            "Recall mode_config: mode=%s strength_pre_filter=%.2f weak_links=%s traversal=%s bank=%s",
            retrieval_mode.value if retrieval_mode else "precision",
            mode_config.strength_pre_filter,
            mode_config.weak_link_policy,
            mode_config.traversal_depth,
            bank_id,
        )

        # Default to all fact types if not specified
        if fact_type is None:
            fact_type = list(VALID_RECALL_FACT_TYPES)

        # Validate fact types early
        invalid_types = set(fact_type) - VALID_RECALL_FACT_TYPES
        if invalid_types:
            raise ValueError(
                f"Invalid fact type(s): {', '.join(sorted(invalid_types))}. "
                f"Must be one of: {', '.join(sorted(VALID_RECALL_FACT_TYPES))}"
            )

        # Validate operation if validator is configured
        if self._ctx.operation_validator:
            from hindsight_api.extensions import RecallContext

            ctx = RecallContext(
                bank_id=bank_id,
                query=query,
                request_context=request_context,
                budget=budget,
                max_tokens=max_tokens,
                enable_trace=enable_trace,
                fact_types=list(fact_type),
                question_date=question_date,
                include_entities=include_entities,
                max_entity_tokens=max_entity_tokens,
                include_chunks=include_chunks,
                max_chunk_tokens=max_chunk_tokens,
            )
            await self._ctx.validate_operation(self._ctx.operation_validator.validate_recall(ctx))

        # Map budget enum to thinking_budget number (default to MID if None)
        budget_mapping = {Budget.LOW: 100, Budget.MID: 300, Budget.HIGH: 1000}
        effective_budget = budget if budget is not None else Budget.MID
        thinking_budget = budget_mapping[effective_budget]

        # Backpressure: limit concurrent recalls to prevent overwhelming the database
        result = None
        _top_scored: list = []
        error_msg = None
        async with self._ctx.search_semaphore:
            # Retry loop for connection errors
            max_retries = self.MAX_SEARCH_RETRIES
            for attempt in range(max_retries + 1):
                try:
                    result, _top_scored = await self._search_with_retries(
                        bank_id,
                        query,
                        fact_type,
                        thinking_budget,
                        max_tokens,
                        enable_trace,
                        question_date,
                        include_entities,
                        max_entity_tokens,
                        include_chunks,
                        max_chunk_tokens,
                        request_context,
                        tags=tags,
                        mode=retrieval_mode,
                        shared_bank_id=shared_bank_id,
                    )
                    break  # Success - exit retry loop
                except Exception as e:
                    # Check if it's a connection error
                    is_connection_error = (
                        isinstance(e, asyncpg.TooManyConnectionsError)
                        or isinstance(e, asyncpg.CannotConnectNowError)
                        or (isinstance(e, asyncpg.PostgresError) and "connection" in str(e).lower())
                    )

                    if is_connection_error and attempt < max_retries:
                        # Wait with exponential backoff before retry
                        wait_time = 0.5 * (2**attempt)  # 0.5s, 1s, 2s
                        logger.warning(
                            f"Connection error on search attempt {attempt + 1}/{max_retries + 1}: {str(e)}. "
                            f"Retrying in {wait_time:.1f}s..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        # Not a connection error or out of retries - call post-hook and raise
                        error_msg = str(e)
                        if self._ctx.operation_validator:
                            from hindsight_api.extensions.operation_validator import RecallResult

                            result_ctx = RecallResult(
                                bank_id=bank_id,
                                query=query,
                                request_context=request_context,
                                budget=budget,
                                max_tokens=max_tokens,
                                enable_trace=enable_trace,
                                fact_types=list(fact_type),
                                question_date=question_date,
                                include_entities=include_entities,
                                max_entity_tokens=max_entity_tokens,
                                include_chunks=include_chunks,
                                max_chunk_tokens=max_chunk_tokens,
                                result=None,
                                success=False,
                                error=error_msg,
                            )
                            try:
                                await self._ctx.operation_validator.on_recall_complete(result_ctx)
                            except Exception as hook_err:
                                logger.warning(f"Post-recall hook error (non-fatal): {hook_err}")
                        raise
            else:
                # Exceeded max retries
                error_msg = "Exceeded maximum retries for search due to connection errors."
                if self._ctx.operation_validator:
                    from hindsight_api.extensions.operation_validator import RecallResult

                    result_ctx = RecallResult(
                        bank_id=bank_id,
                        query=query,
                        request_context=request_context,
                        budget=budget,
                        max_tokens=max_tokens,
                        enable_trace=enable_trace,
                        fact_types=list(fact_type),
                        question_date=question_date,
                        include_entities=include_entities,
                        max_entity_tokens=max_entity_tokens,
                        include_chunks=include_chunks,
                        max_chunk_tokens=max_chunk_tokens,
                        result=None,
                        success=False,
                        error=error_msg,
                    )
                    try:
                        await self._ctx.operation_validator.on_recall_complete(result_ctx)
                    except Exception as hook_err:
                        logger.warning(f"Post-recall hook error (non-fatal): {hook_err}")
                raise Exception(error_msg)

        # Populate WorkingContext if a session is active (Epic 08 S2)
        if session is not None and result is not None and _top_scored:
            sm = self._ctx.get_session_manager()
            wc = sm.get_working_context(session.session_id)
            sc = sm.get_session_cache(session.session_id)
            if wc is not None:
                active_goal = wc.goal_stack[-1] if wc.goal_stack else None
                wc.populate_from_recall(_top_scored, active_goal=active_goal)

                # Co-activation tracking — T3/T4 (Epic 09 S1)
                # Extract IDs from Focus + Supporting tiers (high-activation only).
                # Bio mapping: Hebbian learning — only strongly activated representations
                # form new synaptic associations; peripheral activation is too weak.
                if sc is not None:
                    focus_ids = [ref.engram_id for ref in wc.active_engrams.focus]
                    supporting_ids = [ref.engram_id for ref in wc.active_engrams.supporting]
                    active_ids = focus_ids + supporting_ids
                    if active_ids:
                        sc.co_activation_tracker.track_recall(active_ids)

                    # Resolve Neo4j client once for both flush operations below.
                    # neo4j_client comes from EngramRetriever if active; None → graceful no-op.
                    _neo4j_client = None
                    _needs_neo4j = sc.co_activation_tracker.should_flush() or sc.association_window.should_flush()
                    if _needs_neo4j:
                        from .search.engram_retrieval import EngramRetriever
                        from .search.retrieval import get_default_graph_retriever

                        _retriever = get_default_graph_retriever()
                        _neo4j_client = _retriever.neo4j_client if isinstance(_retriever, EngramRetriever) else None

                    # Periodic flush: write eligible CO_ACTIVATED links to Neo4j every N recalls.
                    if sc.co_activation_tracker.should_flush():
                        await sc.co_activation_tracker.flush_to_neo4j(_neo4j_client)

                    # Association Window — T4 (Epic 09 S3)
                    # Check temporal proximity between Focus+Supporting Engrams (STC mechanism).
                    # Periodic: every check_every_n recalls to limit DB writes.
                    sc.association_window.check_associations(wc.active_engrams)
                    if sc.association_window.should_flush():
                        await sc.association_window.flush_to_neo4j(_neo4j_client)

                # Construction Pipeline (Epic 11 S2) — builds ConstructedAnswer from scored results.
                # Runs after WorkingContext population so WC already reflects the current recall.
                # Bio mapping: PFC semantic integration — retrieved fragments reconstructed into
                # a coherent answer with inferences and gap detection.
                try:
                    from .constructive.pipeline import ConstructionPipeline

                    mode_config = self._ctx.resolve_session_config(session)
                    _pipeline = ConstructionPipeline(
                        llm=self._ctx.llm_registry.get_llm("reflect", "constructive_memory_inference"),
                        mode_config=mode_config,
                    )
                    result.constructed_answer = await _pipeline.construct(
                        scored_results=_top_scored,
                        query=query,
                        working_context=wc,
                    )
                except Exception as _pipeline_err:
                    logger.warning("[CONSTRUCTION] Pipeline failed (non-fatal): %s", _pipeline_err)

                # Prediction Error Detection (Epic 11 S3) — runs after construction when
                # session.current_expectation is set.
                # Bio mapping: dopaminergic PE signal — mismatch drives reconsolidation + mode shift.
                if (
                    result.constructed_answer is not None
                    and session.current_expectation
                    and self._ctx.reflect_llm_config is not None
                ):
                    try:
                        from .constructive.prediction_error import (
                            PredictionErrorDetector,
                            apply_prediction_error_feedback,
                        )

                        _pe_detector = PredictionErrorDetector(
                            llm=self._ctx.llm_registry.get_llm("reflect", "prediction_error_detection")
                        )
                        _pe = await _pe_detector.detect(
                            answer=result.constructed_answer,
                            expectation=session.current_expectation,
                        )
                        if _pe is not None:
                            _sm = self._ctx.get_session_manager()
                            _pe_registry = _sm.get_prediction_error_registry(session.session_id)
                            if _pe_registry is None:
                                from .reflect.prediction_error_registry import PredictionErrorRegistry as _PEReg

                                _pe_registry = _PEReg()
                            await apply_prediction_error_feedback(
                                error=_pe,
                                session_id=session.session_id,
                                session_manager=_sm,
                                prediction_error_registry=_pe_registry,
                            )
                            result.constructed_answer.construction_metadata["prediction_error"] = {
                                "severity": _pe.severity,
                                "level": _pe.level,
                                "description": _pe.description,
                                "conflicting_fact_ids": _pe.conflicting_fact_ids,
                                "expected_summary": _pe.expected_summary,
                                "actual_summary": _pe.actual_summary,
                            }
                    except Exception as _pe_err:
                        logger.warning("[PE] Prediction error detection failed (non-fatal): %s", _pe_err)

        # Call post-operation hook for success
        if self._ctx.operation_validator and result is not None:
            from hindsight_api.extensions.operation_validator import RecallResult

            result_ctx = RecallResult(
                bank_id=bank_id,
                query=query,
                request_context=request_context,
                budget=budget,
                max_tokens=max_tokens,
                enable_trace=enable_trace,
                fact_types=list(fact_type),
                question_date=question_date,
                include_entities=include_entities,
                max_entity_tokens=max_entity_tokens,
                include_chunks=include_chunks,
                max_chunk_tokens=max_chunk_tokens,
                result=result,
                success=True,
                error=None,
            )
            try:
                await self._ctx.operation_validator.on_recall_complete(result_ctx)
            except Exception as e:
                logger.warning(f"Post-recall hook error (non-fatal): {e}")

        return result

    async def _search_with_retries(
        self,
        bank_id: str,
        query: str,
        fact_type: list[str],
        thinking_budget: int,
        max_tokens: int,
        enable_trace: bool,
        question_date: datetime | None = None,
        include_entities: bool = False,
        max_entity_tokens: int = 500,
        include_chunks: bool = False,
        max_chunk_tokens: int = 8192,
        request_context: "RequestContext" = None,
        tags: list[str] | None = None,
        mode=None,  # RetrievalMode | None — forwarded to retrieve_parallel for MPFP pattern selection
        shared_bank_id: str | None = None,  # Optional second bank for dual-bank parallel query (S5)
    ) -> tuple[RecallResultModel, list[ScoredResult]]:
        """
        Search implementation with modular retrieval and reranking.

        Architecture:
        1. Retrieval: 4-way parallel (semantic, keyword, graph, temporal graph)
        2. Merge: RRF to combine ranked lists
        3. Reranking: Pluggable strategy (heuristic or cross-encoder)
        4. Diversity: MMR with λ=0.5
        5. Token Filter: Limit results to max_tokens budget

        Args:
            bank_id: bank IDentifier
            query: Search query
            fact_type: Deprecated — kept for backward compat, no longer used for retrieval filtering
            thinking_budget: Nodes to explore in graph traversal
            max_tokens: Maximum tokens to return (counts only 'text' field)
            enable_trace: Whether to return search trace (deprecated)
            include_entities: Whether to include entity observations
            max_entity_tokens: Maximum tokens for entity observations
            include_chunks: Whether to include raw chunks
            max_chunk_tokens: Maximum tokens for chunks
            tags: Optional Engram tag filter — only return Engrams whose tags contain all given values.

        Returns:
            RecallResultModel with results, trace, optional entities, and optional chunks
        """
        # Initialize tracer if requested
        from .search.tracer import SearchTracer

        tracer = SearchTracer(query, thinking_budget, max_tokens) if enable_trace else None
        if tracer:
            tracer.start()

        pool = await self._ctx.get_pool()
        recall_start = time.time()

        # Buffer logs for clean output in concurrent scenarios
        recall_id = f"{bank_id[:8]}-{int(time.time() * 1000) % 100000}"
        log_buffer = []
        log_buffer.append(
            f"[RECALL {recall_id}] Query: '{query[:50]}...' (budget={thinking_budget}, max_tokens={max_tokens})"
        )

        try:
            # Step 1: Generate query embedding (for semantic search)
            step_start = time.time()
            from .retain import embedding_utils

            query_embedding = embedding_utils.generate_embedding(self._ctx.embeddings, query)
            step_duration = time.time() - step_start
            log_buffer.append(f"  [1] Generate query embedding: {step_duration:.3f}s")

            if tracer:
                tracer.record_query_embedding(query_embedding)
                tracer.add_phase_metric("generate_query_embedding", step_duration)

            # Step 2: N*4-Way Parallel Retrieval (N fact types × 4 retrieval methods)
            step_start = time.time()
            query_embedding_str = str(query_embedding)

            from .search.bank_merge import merge_parallel_results
            from .search.retrieval import get_default_graph_retriever, retrieve_parallel
            from .search.temporal_extraction import extract_temporal_constraint

            # Track each retrieval start time
            retrieval_start = time.time()

            # Pre-extract temporal constraint once (shared across all fact types)
            tc_start = time.time()
            temporal_constraint = extract_temporal_constraint(
                query, reference_date=question_date, analyzer=self._ctx.query_analyzer
            )
            tc_duration = time.time() - tc_start

            # 4-way parallel retrieval — optionally dual-bank (S5)
            # Disposition does NOT influence retrieval (only reflect_async); mode drives everything.
            parallel_start = time.time()
            if shared_bank_id:
                # CLS dual-bank: agent bank (episodic) + shared bank (semantic) in parallel
                agent_rr, shared_rr = await asyncio.gather(
                    retrieve_parallel(
                        pool,
                        query,
                        query_embedding_str,
                        bank_id,
                        thinking_budget,
                        question_date,
                        self._ctx.query_analyzer,
                        temporal_constraint=temporal_constraint,
                        tags=tags,
                        mode=mode,
                    ),
                    retrieve_parallel(
                        pool,
                        query,
                        query_embedding_str,
                        shared_bank_id,
                        thinking_budget,
                        question_date,
                        self._ctx.query_analyzer,
                        temporal_constraint=temporal_constraint,
                        tags=tags,
                        mode=mode,
                    ),
                )
                rr = merge_parallel_results(agent_rr, shared_rr, mode)
            else:
                rr = await retrieve_parallel(
                    pool,
                    query,
                    query_embedding_str,
                    bank_id,
                    thinking_budget,
                    question_date,
                    self._ctx.query_analyzer,
                    temporal_constraint=temporal_constraint,
                    tags=tags,
                    mode=mode,
                )
            parallel_duration = time.time() - parallel_start

            semantic_results = rr.semantic
            bm25_results = rr.bm25
            graph_results = rr.graph
            temporal_results = rr.temporal
            aggregated_timings = rr.timings
            all_mpfp_timings = rr.mpfp_timings
            detected_temporal_constraint = rr.temporal_constraint

            logger.debug(
                "[RECALL %s] Retrieval: semantic=%d, bm25=%d, graph=%d, temporal=%d",
                recall_id,
                len(semantic_results),
                len(bm25_results),
                len(graph_results),
                len(temporal_results) if temporal_results else 0,
            )

            # Sort by score (descending) for stable RRF input ordering
            semantic_results.sort(key=lambda r: r.similarity or 0.0, reverse=True)
            bm25_results.sort(key=lambda r: r.bm25_score or 0.0, reverse=True)
            graph_results.sort(key=lambda r: r.activation or 0.0, reverse=True)
            if temporal_results:
                temporal_results.sort(key=lambda r: r.temporal_score or 0.0, reverse=True)

            retrieval_duration = time.time() - retrieval_start

            step_duration = time.time() - step_start
            # Format per-method timings (these are the actual parallel retrieval times)
            timing_parts = [
                f"semantic={len(semantic_results)}({aggregated_timings['semantic']:.3f}s)",
                f"bm25={len(bm25_results)}({aggregated_timings['bm25']:.3f}s)",
                f"graph={len(graph_results)}({aggregated_timings['graph']:.3f}s)",
            ]
            temporal_info = ""
            if detected_temporal_constraint:
                start_dt, end_dt = detected_temporal_constraint
                temporal_count = len(temporal_results) if temporal_results else 0
                timing_parts.append(f"temporal={temporal_count}({aggregated_timings['temporal']:.3f}s)")
                temporal_info = f" | temporal_range={start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}"
            # Only tc is sequential setup now (adjacency loads in parallel with retrieval)
            setup_info = f", tc={tc_duration:.3f}s" if tc_duration > 0.01 else ""
            log_buffer.append(
                f"  [2] Parallel retrieval: {', '.join(timing_parts)} in {parallel_duration:.3f}s{setup_info}{temporal_info}"
            )

            # Log MPFP timing breakdown if available
            if all_mpfp_timings:
                mpfp_total = all_mpfp_timings[0]  # Take first fact type's timing as representative
                mpfp_parts = [
                    f"db_queries={mpfp_total.db_queries}",
                    f"edge_load={mpfp_total.edge_load_time:.3f}s",
                    f"edges={mpfp_total.edge_count}",
                    f"patterns={mpfp_total.pattern_count}",
                ]
                if mpfp_total.seeds_time > 0.01:
                    mpfp_parts.append(f"seeds={mpfp_total.seeds_time:.3f}s")
                log_buffer.append(f"      [MPFP] {', '.join(mpfp_parts)}")

            # Record retrieval results for tracer
            if tracer:
                # Convert RetrievalResult to old tuple format for tracer
                def to_tuple_format(results):
                    return [(r.id, r.__dict__) for r in results]

                tracer.add_retrieval_results(
                    method_name="semantic",
                    results=to_tuple_format(semantic_results),
                    duration_seconds=aggregated_timings.get("semantic", 0.0),
                    score_field="similarity",
                    metadata={"limit": thinking_budget},
                )
                tracer.add_retrieval_results(
                    method_name="bm25",
                    results=to_tuple_format(bm25_results),
                    duration_seconds=aggregated_timings.get("bm25", 0.0),
                    score_field="bm25_score",
                    metadata={"limit": thinking_budget},
                )
                tracer.add_retrieval_results(
                    method_name="graph",
                    results=to_tuple_format(graph_results),
                    duration_seconds=aggregated_timings.get("graph", 0.0),
                    score_field="activation",
                    metadata={"budget": thinking_budget},
                )
                if temporal_results is not None:
                    tracer.add_retrieval_results(
                        method_name="temporal",
                        results=to_tuple_format(temporal_results),
                        duration_seconds=aggregated_timings.get("temporal", 0.0),
                        score_field="temporal_score",
                        metadata={"budget": thinking_budget},
                    )

                # Record entry points (from semantic results) for legacy graph view
                for rank, retrieval in enumerate(semantic_results[:10], start=1):  # Top 10 as entry points
                    tracer.add_entry_point(retrieval.id, retrieval.text, retrieval.similarity or 0.0, rank)

                tracer.add_phase_metric(
                    "parallel_retrieval",
                    step_duration,
                    {
                        "semantic_count": len(semantic_results),
                        "bm25_count": len(bm25_results),
                        "graph_count": len(graph_results),
                        "temporal_count": len(temporal_results) if temporal_results else 0,
                    },
                )

            # Step 3: Merge with RRF
            step_start = time.time()
            from .search.fusion import reciprocal_rank_fusion

            # Merge 3 or 4 result lists depending on temporal constraint
            if temporal_results:
                merged_candidates = reciprocal_rank_fusion(
                    [semantic_results, bm25_results, graph_results, temporal_results]
                )
            else:
                merged_candidates = reciprocal_rank_fusion([semantic_results, bm25_results, graph_results])

            step_duration = time.time() - step_start
            log_buffer.append(f"  [3] RRF merge: {len(merged_candidates)} unique candidates in {step_duration:.3f}s")

            if tracer:
                # Convert MergedCandidate to old tuple format for tracer
                tracer_merged = [
                    (mc.id, mc.retrieval.__dict__, {"rrf_score": mc.rrf_score, **mc.source_ranks})
                    for mc in merged_candidates
                ]
                tracer.add_rrf_merged(tracer_merged)
                tracer.add_phase_metric("rrf_merge", step_duration, {"candidates_merged": len(merged_candidates)})

            # Step 3.5: Fetch engram_dictionary data (strength + thalamus) + strength pre-filter
            # Bio-mapping: PFC-gated pre-filter removes low-consolidation Engrams before expensive CE stage.
            # Default strength=0.5 is used when no engram_dictionary row exists (e.g. legacy facts).
            from .search.scoring import (
                calculate_combined_score,
                calculate_recency_weight,
                calculate_strength_weight,
                calculate_thalamus_weight,
            )
            from .session.mode_config import get_mode_config as _get_mode_config

            engram_data: dict[str, dict] = {}
            scoring_weights = None
            mode_boost_dim: str | None = None
            if merged_candidates:
                candidate_ids = [mc.id for mc in merged_candidates]
                async with acquire_with_retry(pool) as conn:
                    rows = await conn.fetch(
                        """
                        SELECT engram_id::text, strength,
                               novelty, surprise, task_relevance, emotional_valence
                        FROM engram_dictionary
                        WHERE bank_id = $1 AND engram_id::text = ANY($2::text[])
                        """,
                        bank_id,
                        candidate_ids,
                    )
                for row in rows:
                    engram_data[row["engram_id"]] = dict(row)

            if mode is not None:
                _mc = _get_mode_config(mode)
                scoring_weights = _mc.scoring_weights
                mode_boost_dim = _mc.thalamus_boost_dimension
                threshold = _mc.strength_pre_filter
                if threshold > 0.0 and merged_candidates:
                    before = len(merged_candidates)
                    merged_candidates = [
                        mc for mc in merged_candidates if engram_data.get(mc.id, {}).get("strength", 0.5) >= threshold
                    ]
                    filtered = before - len(merged_candidates)
                    if filtered:
                        log_buffer.append(
                            f"  [3.5] Strength pre-filter: removed {filtered}/{before} below threshold={threshold}"
                        )

            # Step 4: Rerank using cross-encoder (MergedCandidate -> ScoredResult)
            step_start = time.time()
            reranker_instance = self._ctx.cross_encoder_reranker

            # Ensure reranker is initialized (for lazy initialization mode)
            await reranker_instance.ensure_initialized()

            # Rerank using cross-encoder
            scored_results = await reranker_instance.rerank(query, merged_candidates)

            step_duration = time.time() - step_start
            log_buffer.append(f"  [4] Reranking: {len(scored_results)} candidates scored in {step_duration:.3f}s")

            # Step 4.5: Combine cross-encoder score with retrieval signals
            # Extended scoring: w1×CE + w2×RRF + w3×Temporal + w4×Recency(strength-modulated)
            #                   + w5×Engram_Strength + w6×Thalamus_Weighted  (concept.md § 8)
            # Fallback (no mode): original 60/20/10/10 split for backward compat.
            if scored_results:
                # Normalize RRF scores to [0, 1] range using min-max normalization
                rrf_scores = [sr.candidate.rrf_score for sr in scored_results]
                max_rrf = max(rrf_scores) if rrf_scores else 0.0
                min_rrf = min(rrf_scores) if rrf_scores else 0.0
                rrf_range = max_rrf - min_rrf  # Don't force to 1.0, let fallback handle it

                now = utcnow()
                for sr in scored_results:
                    # Normalize RRF score (0-1 range, 0.5 if all same)
                    if rrf_range > 0:
                        sr.rrf_normalized = (sr.candidate.rrf_score - min_rrf) / rrf_range
                    else:
                        sr.rrf_normalized = 0.5

                    # Populate engram strength + thalamus from pre-fetched data
                    edata = engram_data.get(sr.id, {})
                    sr.engram_strength = edata.get("strength", 0.5)
                    thalamus_dims = (
                        {k: edata.get(k, 0.0) for k in ("novelty", "surprise", "task_relevance", "emotional_valence")}
                        if edata
                        else None
                    )
                    sr.thalamus_score = calculate_thalamus_weight(thalamus_dims, mode_boost_dim)

                    # Recency with strength-modulated half-life (concept.md § S4)
                    sr.recency = 0.5  # default for missing dates
                    if sr.retrieval.occurred_start:
                        occurred = sr.retrieval.occurred_start
                        if hasattr(occurred, "tzinfo") and occurred.tzinfo is None:
                            occurred = occurred.replace(tzinfo=UTC)
                        days_ago = (now - occurred).total_seconds() / 86400
                        sr.recency = calculate_recency_weight(days_ago, strength=sr.engram_strength)

                    # Get temporal proximity if available (already 0-1)
                    sr.temporal = (
                        sr.retrieval.temporal_proximity if sr.retrieval.temporal_proximity is not None else 0.5
                    )

                    if scoring_weights is not None:
                        # Extended 6-term formula with mode-specific weights
                        sr.combined_score = calculate_combined_score(
                            ce=sr.cross_encoder_score_normalized,
                            rrf=sr.rrf_normalized,
                            temporal=sr.temporal,
                            recency=sr.recency,
                            strength_weight=calculate_strength_weight(sr.engram_strength),
                            thalamus_weight=sr.thalamus_score,
                            weights=scoring_weights,
                        )
                    else:
                        # Fallback: original Hindsight weights (60/20/10/10)
                        sr.combined_score = (
                            0.6 * sr.cross_encoder_score_normalized
                            + 0.2 * sr.rrf_normalized
                            + 0.1 * sr.temporal
                            + 0.1 * sr.recency
                        )
                    sr.weight = sr.combined_score

                # Fix 2: Apply weak-link boost to combined_score for Analogy mode.
                # The 1.5× boost was applied to activation_map in EngramRetriever but is lost
                # after enrichment (scoring uses CE/RRF, not activation). Re-apply here so
                # weak-link results actually rank higher in Analogy mode output.
                # Bio mapping: Analogy retrieval preferentially follows associative bridges —
                # weak-link hits should surface above equivalent strong-link hits.
                if mode is not None and mode.value == "analogy":
                    from .search.engram_retrieval import WEAK_LINK_PREFER_BOOST

                    for sr in scored_results:
                        if getattr(sr.candidate.retrieval, "traversal_source", None) == "weak_link":
                            sr.combined_score = min(sr.combined_score * WEAK_LINK_PREFER_BOOST, 1.0)
                            sr.weight = sr.combined_score

                # Re-sort by combined score
                scored_results.sort(key=lambda x: x.weight, reverse=True)

            # Add reranked results to tracer AFTER combined scoring (so normalized values are included)
            if tracer:
                results_dict = [sr.to_dict() for sr in scored_results]
                tracer_merged = [
                    (mc.id, mc.retrieval.__dict__, {"rrf_score": mc.rrf_score, **mc.source_ranks})
                    for mc in merged_candidates
                ]
                tracer.add_reranked(results_dict, tracer_merged)
                tracer.add_phase_metric(
                    "reranking",
                    step_duration,
                    {"reranker_type": "cross-encoder", "candidates_reranked": len(scored_results)},
                )

            # Step 5: Truncate to thinking_budget * 2 for token filtering
            rerank_limit = thinking_budget * 2
            top_scored = scored_results[:rerank_limit]

            # Step 6: Token budget filtering
            step_start = time.time()

            # Convert to dict for token filtering (backward compatibility)
            top_dicts = [sr.to_dict() for sr in top_scored]
            filtered_dicts, total_tokens = self._filter_by_token_budget(top_dicts, max_tokens)

            # Convert back to list of IDs and filter scored_results
            filtered_ids = {d["id"] for d in filtered_dicts}
            top_scored = [sr for sr in top_scored if sr.id in filtered_ids]

            step_duration = time.time() - step_start
            log_buffer.append(
                f"  [5] Token filtering: {len(top_scored)} results, {total_tokens}/{max_tokens} tokens in {step_duration:.3f}s"
            )

            if tracer:
                tracer.add_phase_metric(
                    "token_filtering",
                    step_duration,
                    {"results_selected": len(top_scored), "tokens_used": total_tokens, "max_tokens": max_tokens},
                )

            # Record visits for all retrieved nodes
            if tracer:
                for sr in scored_results:
                    tracer.visit_node(
                        node_id=sr.id,
                        text=sr.retrieval.text,
                        context=sr.retrieval.context or "",
                        event_date=sr.retrieval.occurred_start,
                        access_count=sr.retrieval.access_count,
                        is_entry_point=(sr.id in [ep.node_id for ep in tracer.entry_points]),
                        parent_node_id=None,  # In parallel retrieval, there's no clear parent
                        link_type=None,
                        link_weight=None,
                        activation=sr.candidate.rrf_score,  # Use RRF score as activation
                        semantic_similarity=sr.retrieval.similarity or 0.0,
                        recency=sr.recency,
                        frequency=0.0,
                        final_weight=sr.weight,
                    )

            # Step 8: Queue access count updates for visited nodes
            visited_ids = list(set([sr.id for sr in scored_results[:50]]))  # Top 50
            if visited_ids:
                await self._ctx.task_backend.submit_task({"type": "access_count_update", "node_ids": visited_ids})

            # Log fact_type distribution in results
            fact_type_counts = {}
            for sr in top_scored:
                ft = sr.retrieval.fact_type
                fact_type_counts[ft] = fact_type_counts.get(ft, 0) + 1

            fact_type_summary = ", ".join([f"{ft}={count}" for ft, count in sorted(fact_type_counts.items())])

            # Convert ScoredResult to dicts with ISO datetime strings
            top_results_dicts = []
            for sr in top_scored:
                result_dict = sr.to_dict()
                # Convert datetime objects to ISO strings for JSON serialization
                if result_dict.get("occurred_start"):
                    occurred_start = result_dict["occurred_start"]
                    result_dict["occurred_start"] = (
                        occurred_start.isoformat() if hasattr(occurred_start, "isoformat") else occurred_start
                    )
                if result_dict.get("occurred_end"):
                    occurred_end = result_dict["occurred_end"]
                    result_dict["occurred_end"] = (
                        occurred_end.isoformat() if hasattr(occurred_end, "isoformat") else occurred_end
                    )
                if result_dict.get("mentioned_at"):
                    mentioned_at = result_dict["mentioned_at"]
                    result_dict["mentioned_at"] = (
                        mentioned_at.isoformat() if hasattr(mentioned_at, "isoformat") else mentioned_at
                    )
                top_results_dicts.append(result_dict)

            # Get entities for each fact if include_entities is requested
            step_start = time.time()
            fact_entity_map = {}  # unit_id -> list of (entity_id, entity_name)
            if include_entities and top_scored:
                unit_ids = [uuid.UUID(sr.id) for sr in top_scored]
                if unit_ids:
                    async with acquire_with_retry(pool) as entity_conn:
                        entity_rows = await entity_conn.fetch(
                            f"""
                            SELECT ue.unit_id, e.id as entity_id, e.canonical_name
                            FROM {fq_table("unit_entities")} ue
                            JOIN {fq_table("entities")} e ON ue.entity_id = e.id
                            WHERE ue.unit_id = ANY($1::uuid[])
                            """,
                            unit_ids,
                        )
                        for row in entity_rows:
                            unit_id = str(row["unit_id"])
                            if unit_id not in fact_entity_map:
                                fact_entity_map[unit_id] = []
                            fact_entity_map[unit_id].append(
                                {"entity_id": str(row["entity_id"]), "canonical_name": row["canonical_name"]}
                            )
            entity_map_duration = time.time() - step_start

            # Phase A4 — Batch-load retain provenance from engram_dictionary
            # for every fact that will appear in the response. The retrieval
            # pipelines (Qdrant/Neo4j/4-way) don't carry the provenance
            # columns, so we fetch them here in one extra round-trip.
            provenance_map: dict[str, dict] = {}
            if top_results_dicts:
                result_ids = [str(r.get("id")) for r in top_results_dicts if r.get("id") is not None]
                if result_ids:
                    async with acquire_with_retry(pool) as conn:
                        provenance_rows = await conn.fetch(
                            f"""
                            SELECT engram_id, tags, expectation, outcome,
                                   session_mode, task_context, retain_context
                            FROM {fq_table("engram_dictionary")}
                            WHERE bank_id = $1 AND engram_id::text = ANY($2::text[])
                            """,
                            bank_id,
                            result_ids,
                        )
                    for prow in provenance_rows:
                        rc_raw = prow["retain_context"]
                        if isinstance(rc_raw, str):
                            try:
                                import json as _json

                                rc_raw = _json.loads(rc_raw)
                            except Exception:
                                rc_raw = None
                        provenance_map[str(prow["engram_id"])] = {
                            "tags": list(prow["tags"]) if prow["tags"] else None,
                            "expectation": prow["expectation"],
                            "outcome": prow["outcome"],
                            "session_mode": prow["session_mode"],
                            "task_context": prow["task_context"],
                            "retain_context": rc_raw,
                        }

            # Convert results to MemoryFact objects
            memory_facts = []
            for result_dict in top_results_dicts:
                result_id = str(result_dict.get("id"))
                # Get entity names for this fact
                entity_names = None
                if include_entities and result_id in fact_entity_map:
                    entity_names = [e["canonical_name"] for e in fact_entity_map[result_id]]

                prov = provenance_map.get(result_id, {})

                memory_facts.append(
                    MemoryFact(
                        id=result_id,
                        text=result_dict.get("text"),
                        fact_type=result_dict.get("fact_type", "world"),
                        entities=entity_names,
                        context=result_dict.get("context"),
                        occurred_start=result_dict.get("occurred_start"),
                        occurred_end=result_dict.get("occurred_end"),
                        mentioned_at=result_dict.get("mentioned_at"),
                        document_id=result_dict.get("document_id"),
                        chunk_id=result_dict.get("chunk_id"),
                        tags=prov.get("tags"),
                        expectation=prov.get("expectation"),
                        outcome=prov.get("outcome"),
                        session_mode=prov.get("session_mode"),
                        task_context=prov.get("task_context"),
                        retain_context=prov.get("retain_context"),
                    )
                )

            # Fetch entity observations if requested
            step_start = time.time()
            entities_dict = None
            total_entity_tokens = 0
            total_chunk_tokens = 0
            if include_entities and fact_entity_map:
                # Collect unique entities in order of fact relevance (preserving order from top_scored)
                # Use a list to maintain order, but track seen entities to avoid duplicates
                entities_ordered = []  # list of (entity_id, entity_name) tuples
                seen_entity_ids = set()

                # Iterate through facts in relevance order
                for sr in top_scored:
                    unit_id = sr.id
                    if unit_id in fact_entity_map:
                        for entity in fact_entity_map[unit_id]:
                            entity_id = entity["entity_id"]
                            entity_name = entity["canonical_name"]
                            if entity_id not in seen_entity_ids:
                                entities_ordered.append((entity_id, entity_name))
                                seen_entity_ids.add(entity_id)

                # Fetch all observations in a single batched query
                entity_ids = [eid for eid, _ in entities_ordered]
                async with acquire_with_retry(pool) as conn:
                    rows = await conn.fetch(
                        f"""
                        WITH ranked AS (
                            SELECT
                                ue.entity_id,
                                mu.text,
                                mu.mentioned_at,
                                ROW_NUMBER() OVER (PARTITION BY ue.entity_id ORDER BY mu.mentioned_at DESC) as rn
                            FROM {fq_table("memory_units")} mu
                            JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
                            WHERE mu.bank_id = $1
                              AND mu.fact_type = 'observation'
                              AND ue.entity_id = ANY($2::uuid[])
                        )
                        SELECT entity_id, text, mentioned_at
                        FROM ranked
                        WHERE rn <= $3
                        ORDER BY entity_id, rn
                        """,
                        bank_id,
                        [uuid.UUID(eid) for eid in entity_ids],
                        5,  # limit_per_entity
                    )
                all_observations: dict[str, list] = {eid: [] for eid in entity_ids}
                for row in rows:
                    entity_id_str = str(row["entity_id"])
                    mentioned_at = row["mentioned_at"].isoformat() if row["mentioned_at"] else None
                    all_observations[entity_id_str].append(
                        EntityObservation(text=row["text"], mentioned_at=mentioned_at)
                    )

                # Build entities_dict respecting token budget, in relevance order
                entities_dict = {}
                encoding = _get_tiktoken_encoding()

                for entity_id, entity_name in entities_ordered:
                    if total_entity_tokens >= max_entity_tokens:
                        break

                    observations = all_observations.get(entity_id, [])

                    # Calculate tokens for this entity's observations
                    entity_tokens = 0
                    included_observations = []
                    for obs in observations:
                        obs_tokens = len(encoding.encode(obs.text))
                        if total_entity_tokens + entity_tokens + obs_tokens <= max_entity_tokens:
                            included_observations.append(obs)
                            entity_tokens += obs_tokens
                        else:
                            break

                    if included_observations:
                        entities_dict[entity_name] = EntityState(
                            entity_id=entity_id, canonical_name=entity_name, observations=included_observations
                        )
                        total_entity_tokens += entity_tokens
            entity_obs_duration = time.time() - step_start

            # Fetch chunks if requested
            step_start = time.time()
            chunks_dict = None
            if include_chunks and top_scored:
                from .response_models import ChunkInfo

                # Collect chunk_ids in order of fact relevance (preserving order from top_scored)
                # Use a list to maintain order, but track seen chunks to avoid duplicates
                chunk_ids_ordered = []
                seen_chunk_ids = set()
                for sr in top_scored:
                    chunk_id = sr.retrieval.chunk_id
                    if chunk_id and chunk_id not in seen_chunk_ids:
                        chunk_ids_ordered.append(chunk_id)
                        seen_chunk_ids.add(chunk_id)

                if chunk_ids_ordered:
                    # Fetch chunk data from database using chunk_ids (no ORDER BY to preserve input order)
                    async with acquire_with_retry(pool) as conn:
                        chunks_rows = await conn.fetch(
                            f"""
                            SELECT chunk_id, chunk_text, chunk_index
                            FROM {fq_table("chunks")}
                            WHERE chunk_id = ANY($1::text[])
                            """,
                            chunk_ids_ordered,
                        )

                    # Create a lookup dict for fast access
                    chunks_lookup = {row["chunk_id"]: row for row in chunks_rows}

                    # Apply token limit and build chunks_dict in the order of chunk_ids_ordered
                    chunks_dict = {}
                    encoding = _get_tiktoken_encoding()

                    for chunk_id in chunk_ids_ordered:
                        if chunk_id not in chunks_lookup:
                            continue

                        row = chunks_lookup[chunk_id]
                        chunk_text = row["chunk_text"]
                        chunk_tokens = len(encoding.encode(chunk_text))

                        # Check if adding this chunk would exceed the limit
                        if total_chunk_tokens + chunk_tokens > max_chunk_tokens:
                            # Truncate the chunk to fit within the remaining budget
                            remaining_tokens = max_chunk_tokens - total_chunk_tokens
                            if remaining_tokens > 0:
                                # Truncate to remaining tokens
                                truncated_text = encoding.decode(encoding.encode(chunk_text)[:remaining_tokens])
                                chunks_dict[chunk_id] = ChunkInfo(
                                    chunk_text=truncated_text, chunk_index=row["chunk_index"], truncated=True
                                )
                                total_chunk_tokens = max_chunk_tokens
                            # Stop adding more chunks once we hit the limit
                            break
                        else:
                            chunks_dict[chunk_id] = ChunkInfo(
                                chunk_text=chunk_text, chunk_index=row["chunk_index"], truncated=False
                            )
                            total_chunk_tokens += chunk_tokens
            chunks_duration = time.time() - step_start

            # Log entity/chunk fetch timing (only if any enrichment was requested)
            log_buffer.append(
                f"  [6] Response enrichment: entity_map={entity_map_duration:.3f}s, entity_obs={entity_obs_duration:.3f}s, chunks={chunks_duration:.3f}s"
            )

            # Finalize trace if enabled
            trace_dict = None
            if tracer:
                trace = tracer.finalize(top_results_dicts)
                trace_dict = trace.to_dict() if trace else None

            # Log final recall stats
            total_time = time.time() - recall_start
            num_chunks = len(chunks_dict) if chunks_dict else 0
            num_entities = len(entities_dict) if entities_dict else 0
            log_buffer.append(
                f"[RECALL {recall_id}] Complete: {len(top_scored)} facts ({total_tokens} tok), {num_chunks} chunks ({total_chunk_tokens} tok), {num_entities} entities ({total_entity_tokens} tok) | {fact_type_summary} | {total_time:.3f}s"
            )
            logger.info("\n" + "\n".join(log_buffer))

            # MIN-3: Batch update last_accessed for returned Engrams (best-effort).
            # Bio mapping: LTP-Late — accessing a consolidated Engram resets its decay clock.
            # This keeps last_accessed current so NCR Decay computes correct forgetting-curve values.
            if top_scored:
                try:
                    from hindsight_api.engine.utils import fq_table as _fq_table

                    async with acquire_with_retry(pool) as _access_conn:
                        engram_ids_for_access = [sr.id for sr in top_scored if sr.id]
                        if engram_ids_for_access:
                            await _access_conn.execute(
                                f"""
                                UPDATE {_fq_table("engram_dictionary")}
                                SET access_count = access_count + 1,
                                    last_accessed = NOW()
                                WHERE engram_id = ANY($1::uuid[])
                                """,
                                engram_ids_for_access,
                            )
                except Exception as _access_err:
                    logger.debug("[RECALL] update_access batch failed (non-fatal): %s", _access_err)

            return (
                RecallResultModel(results=memory_facts, trace=trace_dict, entities=entities_dict, chunks=chunks_dict),
                top_scored,
            )

        except Exception as e:
            log_buffer.append(f"[RECALL {recall_id}] ERROR after {time.time() - recall_start:.3f}s: {str(e)}")
            logger.error("\n" + "\n".join(log_buffer))
            raise Exception(f"Failed to search memories: {str(e)}")

    def _filter_by_token_budget(
        self, results: list[dict[str, Any]], max_tokens: int
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Filter results to fit within token budget.

        Counts tokens only for the 'text' field using tiktoken (cl100k_base encoding).
        Stops before including a fact that would exceed the budget.

        Args:
            results: List of search results
            max_tokens: Maximum tokens allowed

        Returns:
            Tuple of (filtered_results, total_tokens_used)
        """
        encoding = _get_tiktoken_encoding()

        filtered_results = []
        total_tokens = 0

        for result in results:
            text = result.get("text", "")
            text_tokens = len(encoding.encode(text))

            # Check if adding this result would exceed budget
            if total_tokens + text_tokens <= max_tokens:
                filtered_results.append(result)
                total_tokens += text_tokens
            else:
                # Stop before including a fact that would exceed limit
                break

        return filtered_results, total_tokens
