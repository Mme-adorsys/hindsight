"""
Retrieval module for 4-way parallel search.

Implements:
1. Semantic retrieval (vector similarity)
2. BM25 retrieval (keyword/full-text search)
3. Graph retrieval (via pluggable GraphRetriever interface)
4. Temporal retrieval (time-aware search with spreading)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional

from ...config import get_config
from ..db_utils import acquire_with_retry
from ..memory_engine import fq_table
from .graph_retrieval import BFSGraphRetriever, GraphRetriever
from .mpfp_retrieval import MPFPGraphRetriever
from .types import MPFPTimings, RetrievalResult

logger = logging.getLogger(__name__)


@dataclass
class ParallelRetrievalResult:
    """Result from parallel retrieval across all methods."""

    semantic: list[RetrievalResult]
    bm25: list[RetrievalResult]
    graph: list[RetrievalResult]
    temporal: list[RetrievalResult] | None
    timings: dict[str, float] = field(default_factory=dict)
    temporal_constraint: tuple | None = None  # (start_date, end_date)
    mpfp_timings: list[MPFPTimings] = field(default_factory=list)  # MPFP sub-step timings per fact type


# Default graph retriever instance (can be overridden)
_default_graph_retriever: GraphRetriever | None = None


def get_default_graph_retriever() -> GraphRetriever:
    """Get or create the default graph retriever based on config."""
    global _default_graph_retriever
    if _default_graph_retriever is None:
        config = get_config()
        retriever_type = config.graph_retriever.lower()
        if retriever_type == "mpfp":
            _default_graph_retriever = MPFPGraphRetriever()
            logger.info("Using MPFP graph retriever")
        elif retriever_type == "bfs":
            _default_graph_retriever = BFSGraphRetriever()
            logger.info("Using BFS graph retriever")
        else:
            logger.warning(f"Unknown graph retriever '{retriever_type}', falling back to MPFP")
            _default_graph_retriever = MPFPGraphRetriever()
    return _default_graph_retriever


def set_default_graph_retriever(retriever: GraphRetriever) -> None:
    """Set the default graph retriever (for configuration/testing)."""
    global _default_graph_retriever
    _default_graph_retriever = retriever


async def retrieve_semantic(
    conn,
    query_emb_str: str,
    bank_id: str,
    limit: int,
    tags: list[str] | None = None,
    similarity_threshold: float = 0.5,
) -> list[RetrievalResult]:
    """
    Semantic retrieval via vector similarity.

    Args:
        conn: Database connection
        query_emb_str: Query embedding as string
        bank_id: bank ID
        limit: Maximum results to return
        tags: Optional tag filter — only return Engrams whose tags contain all given values.
        similarity_threshold: Minimum cosine similarity to include (mode-dependent).

    Returns:
        List of RetrievalResult objects
    """
    params = [query_emb_str, bank_id]
    join_clause = ""
    tag_filter = ""
    if tags:
        params.append(tags)
        join_clause = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
        tag_filter = "AND ed.tags @> $3::jsonb"
    params.append(limit)

    results = await conn.fetch(
        f"""
        SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
               mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
               1 - (mu.embedding <=> $1::vector) AS similarity
        FROM {fq_table("memory_units")} mu
        {join_clause}
        WHERE mu.bank_id = $2
          AND mu.embedding IS NOT NULL
          {tag_filter}
          AND (1 - (mu.embedding <=> $1::vector)) >= {similarity_threshold}
        ORDER BY mu.embedding <=> $1::vector
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [RetrievalResult.from_db_row(dict(r)) for r in results]


async def retrieve_bm25(
    conn, query_text: str, bank_id: str, limit: int, tags: list[str] | None = None
) -> list[RetrievalResult]:
    """
    BM25 keyword retrieval via full-text search.

    Args:
        conn: Database connection
        query_text: Query text
        bank_id: bank ID
        limit: Maximum results to return
        tags: Optional tag filter — only return Engrams whose tags contain all given values.

    Returns:
        List of RetrievalResult objects
    """
    import re

    # Sanitize query text: remove special characters that have meaning in tsquery
    # Keep only alphanumeric characters and spaces
    sanitized_text = re.sub(r"[^\w\s]", " ", query_text.lower())

    # Split and filter empty strings
    tokens = [token for token in sanitized_text.split() if token]

    if not tokens:
        # If no valid tokens, return empty results
        return []

    # Convert query to tsquery using OR for more flexible matching
    # This prevents empty results when some terms are missing
    query_tsquery = " | ".join(tokens)

    params = [query_tsquery, bank_id]
    join_clause = ""
    tag_filter = ""
    if tags:
        params.append(tags)
        join_clause = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
        tag_filter = "AND ed.tags @> $3::jsonb"
    params.append(limit)

    results = await conn.fetch(
        f"""
        SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
               mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
               ts_rank_cd(mu.search_vector, to_tsquery('english', $1)) AS bm25_score
        FROM {fq_table("memory_units")} mu
        {join_clause}
        WHERE mu.bank_id = $2
          {tag_filter}
          AND mu.search_vector @@ to_tsquery('english', $1)
        ORDER BY bm25_score DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [RetrievalResult.from_db_row(dict(r)) for r in results]


async def retrieve_temporal(
    conn,
    query_emb_str: str,
    bank_id: str,
    start_date: datetime,
    end_date: datetime,
    budget: int,
    semantic_threshold: float = 0.1,
    tags: list[str] | None = None,
) -> list[RetrievalResult]:
    """
    Temporal retrieval with spreading activation.

    Strategy:
    1. Find entry points (facts in date range with semantic relevance)
    2. Spread through temporal links to related facts
    3. Score by temporal proximity + semantic similarity + link weight

    Args:
        conn: Database connection
        query_emb_str: Query embedding as string
        agent_id: bank ID
        fact_type: Fact type to filter
        start_date: Start of time range
        end_date: End of time range
        budget: Node budget for spreading
        semantic_threshold: Minimum semantic similarity to include

    Returns:
        List of RetrievalResult objects with temporal scores
    """

    # Ensure start_date and end_date are timezone-aware (UTC) to match database datetimes
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=UTC)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=UTC)

    ep_params = [query_emb_str, bank_id, start_date, end_date, semantic_threshold]
    ep_join = ""
    ep_tag_filter = ""
    if tags:
        ep_params.insert(2, tags)
        ep_join = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
        ep_tag_filter = "AND ed.tags @> $3::jsonb"
        # With tags: $3=tags, $4=start, $5=end, $6=threshold
        sd_idx, ed_idx, st_idx = 4, 5, 6
    else:
        # Without tags: $3=start, $4=end, $5=threshold
        sd_idx, ed_idx, st_idx = 3, 4, 5

    entry_points = await conn.fetch(
        f"""
        SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
               mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
               1 - (mu.embedding <=> $1::vector) AS similarity
        FROM {fq_table("memory_units")} mu
        {ep_join}
        WHERE mu.bank_id = $2
          {ep_tag_filter}
          AND mu.embedding IS NOT NULL
          AND (
              (mu.occurred_start IS NOT NULL AND mu.occurred_end IS NOT NULL
               AND mu.occurred_start <= ${ed_idx} AND mu.occurred_end >= ${sd_idx})
              OR (mu.mentioned_at IS NOT NULL AND mu.mentioned_at BETWEEN ${sd_idx} AND ${ed_idx})
              OR (mu.occurred_start IS NOT NULL AND mu.occurred_start BETWEEN ${sd_idx} AND ${ed_idx})
              OR (mu.occurred_end IS NOT NULL AND mu.occurred_end BETWEEN ${sd_idx} AND ${ed_idx})
          )
          AND (1 - (mu.embedding <=> $1::vector)) >= ${st_idx}
        ORDER BY COALESCE(mu.occurred_start, mu.mentioned_at, mu.occurred_end) DESC,
                 (mu.embedding <=> $1::vector) ASC
        LIMIT 10
        """,
        *ep_params,
    )

    if not entry_points:
        return []

    # Calculate temporal scores for entry points
    total_days = (end_date - start_date).total_seconds() / 86400
    mid_date = start_date + (end_date - start_date) / 2  # Calculate once for all comparisons
    results = []
    visited = set()

    for ep in entry_points:
        unit_id = str(ep["id"])
        visited.add(unit_id)

        # Calculate temporal proximity using the most relevant date
        # Priority: occurred_start/end (event time) > mentioned_at (mention time)
        best_date = None
        if ep["occurred_start"] is not None and ep["occurred_end"] is not None:
            # Use midpoint of occurred range
            best_date = ep["occurred_start"] + (ep["occurred_end"] - ep["occurred_start"]) / 2
        elif ep["occurred_start"] is not None:
            best_date = ep["occurred_start"]
        elif ep["occurred_end"] is not None:
            best_date = ep["occurred_end"]
        elif ep["mentioned_at"] is not None:
            best_date = ep["mentioned_at"]

        # Temporal proximity score (closer to range center = higher score)
        if best_date:
            days_from_mid = abs((best_date - mid_date).total_seconds() / 86400)
            temporal_proximity = 1.0 - min(days_from_mid / (total_days / 2), 1.0) if total_days > 0 else 1.0
        else:
            temporal_proximity = 0.5  # Fallback if no dates (shouldn't happen due to WHERE clause)

        # Create RetrievalResult with temporal scores
        ep_result = RetrievalResult.from_db_row(dict(ep))
        ep_result.temporal_score = temporal_proximity
        ep_result.temporal_proximity = temporal_proximity
        results.append(ep_result)

    # Spread through temporal links using BATCHED neighbor fetching
    # Map node_id -> (semantic_sim, temporal_score) for propagation
    node_scores = {str(ep["id"]): (ep["similarity"], 1.0) for ep in entry_points}
    frontier = list(node_scores.keys())  # Current batch of nodes to expand
    budget_remaining = budget - len(entry_points)
    batch_size = 20  # Process this many nodes per DB query

    while frontier and budget_remaining > 0:
        # Take a batch from frontier
        batch_ids = frontier[:batch_size]
        frontier = frontier[batch_size:]

        # Batch fetch all neighbors (optional tag filter via engram_dictionary JOIN)
        n_params = [query_emb_str, batch_ids, semantic_threshold]
        n_ed_join = ""
        n_tag_filter = ""
        if tags:
            n_params.append(tags)
            n_ed_join = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
            n_tag_filter = "AND ed.tags @> $4::jsonb"
            n_sim_idx, n_limit_idx = 3, 5
        else:
            n_sim_idx, n_limit_idx = 3, 4
        n_params.append(batch_size * 10)
        neighbors = await conn.fetch(
            f"""
            SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
                   mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
                   ml.weight, ml.link_type, ml.from_unit_id,
                   1 - (mu.embedding <=> $1::vector) AS similarity
            FROM {fq_table("memory_links")} ml
            JOIN {fq_table("memory_units")} mu ON ml.to_unit_id = mu.id
            {n_ed_join}
            WHERE ml.from_unit_id = ANY($2::uuid[])
              AND ml.link_type IN ('temporal', 'causes', 'caused_by', 'enables', 'prevents')
              AND ml.weight >= 0.1
              {n_tag_filter}
              AND mu.embedding IS NOT NULL
              AND (1 - (mu.embedding <=> $1::vector)) >= ${n_sim_idx}
            ORDER BY ml.weight DESC
            LIMIT ${n_limit_idx}
            """,
            *n_params,
        )

        for n in neighbors:
            neighbor_id = str(n["id"])
            if neighbor_id in visited:
                continue

            visited.add(neighbor_id)
            budget_remaining -= 1

            # Get parent's scores for propagation
            parent_id = str(n["from_unit_id"])
            _, parent_temporal_score = node_scores.get(parent_id, (0.5, 0.5))

            # Calculate temporal score for neighbor using best available date
            neighbor_best_date = None
            if n["occurred_start"] is not None and n["occurred_end"] is not None:
                neighbor_best_date = n["occurred_start"] + (n["occurred_end"] - n["occurred_start"]) / 2
            elif n["occurred_start"] is not None:
                neighbor_best_date = n["occurred_start"]
            elif n["occurred_end"] is not None:
                neighbor_best_date = n["occurred_end"]
            elif n["mentioned_at"] is not None:
                neighbor_best_date = n["mentioned_at"]

            if neighbor_best_date:
                days_from_mid = abs((neighbor_best_date - mid_date).total_seconds() / 86400)
                neighbor_temporal_proximity = (
                    1.0 - min(days_from_mid / (total_days / 2), 1.0) if total_days > 0 else 1.0
                )
            else:
                neighbor_temporal_proximity = 0.3  # Lower score if no temporal data

            # Boost causal links (same as graph retrieval)
            link_type = n["link_type"]
            if link_type in ("causes", "caused_by"):
                causal_boost = 2.0
            elif link_type in ("enables", "prevents"):
                causal_boost = 1.5
            else:
                causal_boost = 1.0

            # Propagate temporal score through links (decay, with causal boost)
            propagated_temporal = parent_temporal_score * n["weight"] * causal_boost * 0.7

            # Combined temporal score
            combined_temporal = max(neighbor_temporal_proximity, propagated_temporal)

            # Create RetrievalResult with temporal scores
            neighbor_result = RetrievalResult.from_db_row(dict(n))
            neighbor_result.temporal_score = combined_temporal
            neighbor_result.temporal_proximity = neighbor_temporal_proximity
            results.append(neighbor_result)

            # Track scores for propagation and add to frontier
            if budget_remaining > 0 and combined_temporal > 0.2:
                node_scores[neighbor_id] = (n["similarity"], combined_temporal)
                frontier.append(neighbor_id)

            if budget_remaining <= 0:
                break

    return results


async def retrieve_parallel(
    pool,
    query_text: str,
    query_embedding_str: str,
    bank_id: str,
    thinking_budget: int,
    question_date: datetime | None = None,
    query_analyzer: Optional["QueryAnalyzer"] = None,
    graph_retriever: GraphRetriever | None = None,
    temporal_constraint: tuple | None = None,  # Pre-extracted temporal constraint
    tags: list[str] | None = None,
    mode=None,  # RetrievalMode | None — forwarded to graph retriever for mode-aware patterns
    registry: "RetrieverRegistry | None" = None,  # Bank-aware retriever registry (T6)
    similarity_threshold: float = 0.5,  # Mode-dependent minimum cosine similarity
) -> ParallelRetrievalResult:
    """
    Run 3-way or 4-way parallel retrieval (adds temporal if detected).

    Args:
        pool: Database connection pool
        query_text: Query text
        query_embedding_str: Query embedding as string
        bank_id: Bank ID
        thinking_budget: Budget for graph traversal and retrieval limits
        question_date: Optional date when question was asked (for temporal filtering)
        query_analyzer: Query analyzer to use (defaults to TransformerQueryAnalyzer)
        graph_retriever: Graph retrieval strategy (defaults to configured retriever)
        temporal_constraint: Pre-extracted temporal constraint (optional)
        tags: Optional tag filter — only return Engrams whose tags contain all given values.
        mode: Optional RetrievalMode — enables mode-aware MPFP pattern selection.

    Returns:
        ParallelRetrievalResult with semantic, bm25, graph, temporal results and timings
    """
    # Extract temporal constraint if not pre-provided
    if temporal_constraint is None:
        from .temporal_extraction import extract_temporal_constraint

        temporal_constraint = extract_temporal_constraint(
            query_text, reference_date=question_date, analyzer=query_analyzer
        )

    # Resolve retriever: explicit arg → registry (bank-aware) → global default
    if graph_retriever is not None:
        retriever = graph_retriever
    elif registry is not None:
        retriever = registry.get(bank_id)
    else:
        retriever = get_default_graph_retriever()

    if retriever.name == "mpfp":
        return await _retrieve_parallel_mpfp(
            pool,
            query_text,
            query_embedding_str,
            bank_id,
            thinking_budget,
            temporal_constraint,
            retriever,
            tags=tags,
            mode=mode,
            similarity_threshold=similarity_threshold,
        )
    else:
        return await _retrieve_parallel_bfs(
            pool,
            query_text,
            query_embedding_str,
            bank_id,
            thinking_budget,
            temporal_constraint,
            retriever,
            tags=tags,
            mode=mode,
            similarity_threshold=similarity_threshold,
        )


@dataclass
class _TimedResult:
    """Internal result with timing."""

    results: list[RetrievalResult]
    time: float


async def _retrieve_parallel_mpfp(
    pool,
    query_text: str,
    query_embedding_str: str,
    bank_id: str,
    thinking_budget: int,
    temporal_constraint: tuple | None,
    retriever: GraphRetriever,
    tags: list[str] | None = None,
    mode=None,  # RetrievalMode | None
    similarity_threshold: float = 0.5,
) -> ParallelRetrievalResult:
    """
    MPFP retrieval with true parallelization.

    All methods run independently in parallel:
    - Semantic: vector similarity search
    - BM25: keyword search
    - Graph: MPFP traversal (does its own semantic seeds internally)
    - Temporal: date-range search (if constraint detected)

    Graph does its own semantic query for seeds, avoiding chain dependency.
    """
    import time

    async def run_semantic() -> _TimedResult:
        """Independent semantic retrieval."""
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_semantic(
                conn,
                query_embedding_str,
                bank_id,
                limit=thinking_budget,
                tags=tags,
                similarity_threshold=similarity_threshold,
            )
        return _TimedResult(results, time.time() - start)

    async def run_bm25() -> _TimedResult:
        """Independent BM25 retrieval."""
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_bm25(conn, query_text, bank_id, limit=thinking_budget, tags=tags)
        return _TimedResult(results, time.time() - start)

    async def run_graph() -> tuple[list[RetrievalResult], float, MPFPTimings | None]:
        """Independent graph retrieval - does its own semantic seeds."""
        start = time.time()

        # Get temporal seeds if needed (graph uses them for temporal patterns)
        temporal_seeds = None
        if temporal_constraint:
            tc_start, tc_end = temporal_constraint
            async with acquire_with_retry(pool) as conn:
                temporal_seeds = await _get_temporal_entry_points(
                    conn, query_embedding_str, bank_id, tc_start, tc_end, limit=20, tags=tags
                )

        # MPFP does its own semantic seeds via _find_semantic_seeds
        results, mpfp_timing = await retriever.retrieve(
            pool=pool,
            query_embedding_str=query_embedding_str,
            bank_id=bank_id,
            tags=tags,
            budget=thinking_budget,
            query_text=query_text,
            semantic_seeds=None,  # Let MPFP find its own seeds
            temporal_seeds=temporal_seeds,
            mode=mode,
        )
        return results, time.time() - start, mpfp_timing

    async def run_temporal(tc_start, tc_end) -> _TimedResult:
        """Independent temporal retrieval."""
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_temporal(
                conn,
                query_embedding_str,
                bank_id,
                tc_start,
                tc_end,
                budget=thinking_budget,
                semantic_threshold=0.1,
                tags=tags,
            )
        return _TimedResult(results, time.time() - start)

    # Run all methods in parallel (no chain dependencies)
    if temporal_constraint:
        tc_start, tc_end = temporal_constraint
        semantic_result, bm25_result, graph_result, temporal_result = await asyncio.gather(
            run_semantic(),
            run_bm25(),
            run_graph(),
            run_temporal(tc_start, tc_end),
        )
        graph_results, graph_time, mpfp_timing = graph_result
        return ParallelRetrievalResult(
            semantic=semantic_result.results,
            bm25=bm25_result.results,
            graph=graph_results,
            temporal=temporal_result.results,
            timings={
                "semantic": semantic_result.time,
                "bm25": bm25_result.time,
                "graph": graph_time,
                "temporal": temporal_result.time,
            },
            temporal_constraint=temporal_constraint,
            mpfp_timings=[mpfp_timing] if mpfp_timing else [],
        )
    else:
        semantic_result, bm25_result, graph_result = await asyncio.gather(
            run_semantic(),
            run_bm25(),
            run_graph(),
        )
        graph_results, graph_time, mpfp_timing = graph_result
        return ParallelRetrievalResult(
            semantic=semantic_result.results,
            bm25=bm25_result.results,
            graph=graph_results,
            temporal=None,
            timings={
                "semantic": semantic_result.time,
                "bm25": bm25_result.time,
                "graph": graph_time,
            },
            temporal_constraint=None,
            mpfp_timings=[mpfp_timing] if mpfp_timing else [],
        )


async def _get_temporal_entry_points(
    conn,
    query_embedding_str: str,
    bank_id: str,
    start_date: datetime,
    end_date: datetime,
    limit: int = 20,
    semantic_threshold: float = 0.1,
    tags: list[str] | None = None,
) -> list[RetrievalResult]:
    """Get temporal entry points (facts in date range with semantic relevance)."""

    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=UTC)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=UTC)

    params = [query_embedding_str, bank_id, start_date, end_date, semantic_threshold, limit]
    join_clause = ""
    tag_filter = ""
    if tags:
        params.insert(2, tags)
        join_clause = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
        tag_filter = "AND ed.tags @> $3::jsonb"
        # With tags: $3=tags, $4=start, $5=end, $6=threshold, $7=limit
        sd_idx, ed_idx, st_idx, lim_idx = 4, 5, 6, 7
    else:
        # Without tags: $3=start, $4=end, $5=threshold, $6=limit
        sd_idx, ed_idx, st_idx, lim_idx = 3, 4, 5, 6

    rows = await conn.fetch(
        f"""
        SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
               mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
               1 - (mu.embedding <=> $1::vector) AS similarity
        FROM {fq_table("memory_units")} mu
        {join_clause}
        WHERE mu.bank_id = $2
          {tag_filter}
          AND mu.embedding IS NOT NULL
          AND (
              (mu.occurred_start IS NOT NULL AND mu.occurred_end IS NOT NULL
               AND mu.occurred_start <= ${ed_idx} AND mu.occurred_end >= ${sd_idx})
              OR (mu.mentioned_at IS NOT NULL AND mu.mentioned_at BETWEEN ${sd_idx} AND ${ed_idx})
              OR (mu.occurred_start IS NOT NULL AND mu.occurred_start BETWEEN ${sd_idx} AND ${ed_idx})
              OR (mu.occurred_end IS NOT NULL AND mu.occurred_end BETWEEN ${sd_idx} AND ${ed_idx})
          )
          AND (1 - (mu.embedding <=> $1::vector)) >= ${st_idx}
        ORDER BY COALESCE(mu.occurred_start, mu.mentioned_at, mu.occurred_end) DESC,
                 (mu.embedding <=> $1::vector) ASC
        LIMIT ${lim_idx}
        """,
        *params,
    )

    results = []
    total_days = max((end_date - start_date).total_seconds() / 86400, 1)
    mid_date = start_date + (end_date - start_date) / 2

    for row in rows:
        result = RetrievalResult.from_db_row(dict(row))

        # Calculate temporal proximity score
        best_date = None
        if row["occurred_start"] and row["occurred_end"]:
            best_date = row["occurred_start"] + (row["occurred_end"] - row["occurred_start"]) / 2
        elif row["occurred_start"]:
            best_date = row["occurred_start"]
        elif row["occurred_end"]:
            best_date = row["occurred_end"]
        elif row["mentioned_at"]:
            best_date = row["mentioned_at"]

        if best_date:
            days_from_mid = abs((best_date - mid_date).total_seconds() / 86400)
            result.temporal_proximity = 1.0 - min(days_from_mid / (total_days / 2), 1.0)
        else:
            result.temporal_proximity = 0.5

        result.temporal_score = result.temporal_proximity
        results.append(result)

    return results


async def _retrieve_parallel_bfs(
    pool,
    query_text: str,
    query_embedding_str: str,
    bank_id: str,
    thinking_budget: int,
    temporal_constraint: tuple | None,
    retriever: GraphRetriever,
    tags: list[str] | None = None,
    mode=None,  # RetrievalMode | None — forwarded to retriever (no-op for BFS)
    similarity_threshold: float = 0.5,
) -> ParallelRetrievalResult:
    """BFS retrieval: all methods run in parallel (original behavior)."""
    import time

    async def run_semantic() -> _TimedResult:
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_semantic(
                conn,
                query_embedding_str,
                bank_id,
                limit=thinking_budget,
                tags=tags,
                similarity_threshold=similarity_threshold,
            )
        return _TimedResult(results, time.time() - start)

    async def run_bm25() -> _TimedResult:
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_bm25(conn, query_text, bank_id, limit=thinking_budget, tags=tags)
        return _TimedResult(results, time.time() - start)

    async def run_graph() -> _TimedResult:
        start = time.time()
        results, _ = await retriever.retrieve(
            pool=pool,
            query_embedding_str=query_embedding_str,
            bank_id=bank_id,
            tags=tags,
            budget=thinking_budget,
            query_text=query_text,
            mode=mode,
        )
        return _TimedResult(results, time.time() - start)

    async def run_temporal(tc_start, tc_end) -> _TimedResult:
        start = time.time()
        async with acquire_with_retry(pool) as conn:
            results = await retrieve_temporal(
                conn,
                query_embedding_str,
                bank_id,
                tc_start,
                tc_end,
                budget=thinking_budget,
                semantic_threshold=0.1,
                tags=tags,
            )
        return _TimedResult(results, time.time() - start)

    if temporal_constraint:
        tc_start, tc_end = temporal_constraint
        semantic_r, bm25_r, graph_r, temporal_r = await asyncio.gather(
            run_semantic(),
            run_bm25(),
            run_graph(),
            run_temporal(tc_start, tc_end),
        )
        return ParallelRetrievalResult(
            semantic=semantic_r.results,
            bm25=bm25_r.results,
            graph=graph_r.results,
            temporal=temporal_r.results,
            timings={
                "semantic": semantic_r.time,
                "bm25": bm25_r.time,
                "graph": graph_r.time,
                "temporal": temporal_r.time,
            },
            temporal_constraint=temporal_constraint,
        )
    else:
        semantic_r, bm25_r, graph_r = await asyncio.gather(
            run_semantic(),
            run_bm25(),
            run_graph(),
        )
        return ParallelRetrievalResult(
            semantic=semantic_r.results,
            bm25=bm25_r.results,
            graph=graph_r.results,
            temporal=None,
            timings={
                "semantic": semantic_r.time,
                "bm25": bm25_r.time,
                "graph": graph_r.time,
            },
            temporal_constraint=None,
        )


# ---------------------------------------------------------------------------
# T6 — RetrieverRegistry
# Maps bank_id strings to GraphRetriever instances. Configurable, not hardcoded.
# Bio-mapping: Session Layer (PFC) routes retrieval to the appropriate strategy
# based on bank type — Agent Session Bank → MPFP/BFS, Shared Bank → EngramRetriever.
# ---------------------------------------------------------------------------


class RetrieverRegistry:
    """
    Maps bank_id → GraphRetriever instance for bank-aware retrieval routing.

    Usage:
        registry = RetrieverRegistry(default=MPFPGraphRetriever())
        registry.register("shared-bank-id", EngramRetriever(qdrant, neo4j))
        retriever = registry.get(bank_id)  # returns override or default
    """

    def __init__(self, default: GraphRetriever) -> None:
        self._default = default
        self._overrides: dict[str, GraphRetriever] = {}

    def register(self, bank_id: str, retriever: GraphRetriever) -> None:
        """Register a bank-specific retriever override."""
        self._overrides[bank_id] = retriever

    def get(self, bank_id: str) -> GraphRetriever:
        """Return the retriever for bank_id, or the default if not registered."""
        return self._overrides.get(bank_id, self._default)


def build_retriever_registry(
    default: GraphRetriever | None = None,
    overrides: dict[str, GraphRetriever] | None = None,
) -> RetrieverRegistry:
    """
    Build a RetrieverRegistry with an optional set of bank-specific overrides.

    Args:
        default: Default retriever (falls back to get_default_graph_retriever()).
        overrides: Dict of {bank_id: retriever} for explicit routing.

    Returns:
        Configured RetrieverRegistry.
    """
    registry = RetrieverRegistry(default=default or get_default_graph_retriever())
    for bank_id, retriever in (overrides or {}).items():
        registry.register(bank_id, retriever)
    return registry
