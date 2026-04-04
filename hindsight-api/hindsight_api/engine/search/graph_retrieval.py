"""
Graph retrieval strategies for memory recall.

This module provides an abstraction for graph-based memory retrieval,
allowing different algorithms (BFS spreading activation, PPR, etc.) to be
swapped without changing the rest of the recall pipeline.
"""

import logging
from abc import ABC, abstractmethod

from ..db_utils import acquire_with_retry
from ..memory_engine import fq_table
from .types import MPFPTimings, RetrievalResult

logger = logging.getLogger(__name__)


class GraphRetriever(ABC):
    """
    Abstract base class for graph-based memory retrieval.

    Implementations traverse the memory graph (entity links, temporal links,
    causal links) to find relevant facts that might not be found by
    semantic or keyword search alone.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return identifier for this retrieval strategy (e.g., 'bfs', 'mpfp')."""
        pass

    @abstractmethod
    async def retrieve(
        self,
        pool,
        query_embedding_str: str,
        bank_id: str,
        budget: int,
        query_text: str | None = None,
        semantic_seeds: list[RetrievalResult] | None = None,
        temporal_seeds: list[RetrievalResult] | None = None,
        adjacency=None,  # TypedAdjacency, optional pre-loaded graph
        tags: list[str] | None = None,
        # Deprecated: fact_type kept for backward compat, ignored internally
        fact_type: str | None = None,
        mode=None,  # RetrievalMode | None — mode-aware pattern selection (MPFP only)
    ) -> tuple[list[RetrievalResult], MPFPTimings | None]:
        """
        Retrieve relevant facts via graph traversal.

        Args:
            pool: Database connection pool
            query_embedding_str: Query embedding as string (for finding entry points)
            bank_id: Memory bank identifier
            budget: Maximum number of nodes to explore/return
            query_text: Original query text (optional, for some strategies)
            semantic_seeds: Pre-computed semantic entry points (from semantic retrieval)
            temporal_seeds: Pre-computed temporal entry points (from temporal retrieval)
            adjacency: Pre-loaded typed adjacency graph (optional, for MPFP)
            tags: Optional tag filter — only return Engrams whose tags contain all given values.
            fact_type: Deprecated — converted to tags internally for backward compat.
            mode: Optional RetrievalMode — enables mode-aware pattern selection in MPFP.
                  Ignored by BFS retriever.

        Returns:
            Tuple of (List of RetrievalResult with activation scores, optional timing info)
        """
        pass


class BFSGraphRetriever(GraphRetriever):
    """
    Graph retrieval using BFS-style spreading activation.

    Starting from semantic entry points, spreads activation through
    the memory graph (entity, temporal, causal links) using breadth-first
    traversal with decaying activation.

    This is the original Hindsight graph retrieval algorithm.
    """

    def __init__(
        self,
        entry_point_limit: int = 5,
        entry_point_threshold: float = 0.5,
        activation_decay: float = 0.8,
        min_activation: float = 0.1,
        batch_size: int = 20,
    ):
        """
        Initialize BFS graph retriever.

        Args:
            entry_point_limit: Maximum number of entry points to start from
            entry_point_threshold: Minimum semantic similarity for entry points
            activation_decay: Decay factor per hop (activation *= decay)
            min_activation: Minimum activation to continue spreading
            batch_size: Number of nodes to process per batch (for neighbor fetching)
        """
        self.entry_point_limit = entry_point_limit
        self.entry_point_threshold = entry_point_threshold
        self.activation_decay = activation_decay
        self.min_activation = min_activation
        self.batch_size = batch_size

    @property
    def name(self) -> str:
        return "bfs"

    async def retrieve(
        self,
        pool,
        query_embedding_str: str,
        bank_id: str,
        budget: int,
        query_text: str | None = None,
        semantic_seeds: list[RetrievalResult] | None = None,
        temporal_seeds: list[RetrievalResult] | None = None,
        adjacency=None,  # Not used by BFS
        tags: list[str] | None = None,
        fact_type: str | None = None,  # Deprecated, ignored
        mode=None,  # RetrievalMode | None — accepted for interface compat, not used by BFS
    ) -> tuple[list[RetrievalResult], MPFPTimings | None]:
        """
        Retrieve facts using BFS spreading activation.

        Algorithm:
        1. Find entry points (top semantic matches above threshold)
        2. BFS traversal: visit neighbors, propagate decaying activation
        3. Boost causal links (causes, enables, prevents)
        4. Return visited nodes up to budget

        Note: BFS finds its own entry points via embedding search.
        The semantic_seeds, temporal_seeds, and adjacency parameters are accepted
        for interface compatibility but not used.
        """
        async with acquire_with_retry(pool) as conn:
            results = await self._retrieve_with_conn(conn, query_embedding_str, bank_id, budget, tags=tags)
            return results, None

    async def _retrieve_with_conn(
        self,
        conn,
        query_embedding_str: str,
        bank_id: str,
        budget: int,
        tags: list[str] | None = None,
    ) -> list[RetrievalResult]:
        """Internal implementation with connection."""

        # Step 1: Find entry points (optional tag filter via engram_dictionary JOIN)
        ep_params = [query_embedding_str, bank_id, self.entry_point_threshold, self.entry_point_limit]
        ep_join = ""
        ep_tag_filter = ""
        if tags:
            ep_params.insert(2, tags)
            ep_join = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
            ep_tag_filter = "AND ed.tags @> $3::jsonb"
            # renumber: $3=tags, $4=threshold, $5=limit
            ep_threshold_idx, ep_limit_idx = 4, 5
        else:
            ep_threshold_idx, ep_limit_idx = 3, 4

        entry_points = await conn.fetch(
            f"""
            SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
                   mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id,
                   1 - (mu.embedding <=> $1::vector) AS similarity
            FROM {fq_table("memory_units")} mu
            {ep_join}
            WHERE mu.bank_id = $2
              AND mu.embedding IS NOT NULL
              {ep_tag_filter}
              AND (1 - (mu.embedding <=> $1::vector)) >= ${ep_threshold_idx}
            ORDER BY mu.embedding <=> $1::vector
            LIMIT ${ep_limit_idx}
            """,
            *ep_params,
        )

        if not entry_points:
            return []

        # Step 2: BFS spreading activation
        visited = set()
        results = []
        queue = [(RetrievalResult.from_db_row(dict(r)), r["similarity"]) for r in entry_points]
        budget_remaining = budget

        while queue and budget_remaining > 0:
            # Collect a batch of nodes to process
            batch_nodes = []
            batch_activations = {}

            while queue and len(batch_nodes) < self.batch_size and budget_remaining > 0:
                current, activation = queue.pop(0)
                unit_id = current.id

                if unit_id not in visited:
                    visited.add(unit_id)
                    budget_remaining -= 1
                    current.activation = activation
                    results.append(current)
                    batch_nodes.append(current.id)
                    batch_activations[unit_id] = activation

            # Batch fetch neighbors (optional tag filter via engram_dictionary JOIN)
            if batch_nodes and budget_remaining > 0:
                max_neighbors = len(batch_nodes) * 20
                n_params = [batch_nodes, self.min_activation]
                n_ed_join = ""
                n_tag_filter = ""
                if tags:
                    n_params.append(tags)
                    n_ed_join = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
                    n_tag_filter = "AND ed.tags @> $3::jsonb"
                n_params.append(max_neighbors)
                n_limit_idx = len(n_params)
                neighbors = await conn.fetch(
                    f"""
                    SELECT mu.id, mu.text, mu.context, mu.occurred_start, mu.occurred_end,
                           mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type,
                           mu.document_id, mu.chunk_id,
                           ml.weight, ml.link_type, ml.from_unit_id
                    FROM {fq_table("memory_links")} ml
                    JOIN {fq_table("memory_units")} mu ON ml.to_unit_id = mu.id
                    {n_ed_join}
                    WHERE ml.from_unit_id = ANY($1::uuid[])
                      AND ml.weight >= $2
                      {n_tag_filter}
                    ORDER BY ml.weight DESC
                    LIMIT ${n_limit_idx}
                    """,
                    *n_params,
                )

                for n in neighbors:
                    neighbor_id = str(n["id"])
                    if neighbor_id not in visited:
                        parent_id = str(n["from_unit_id"])
                        parent_activation = batch_activations.get(parent_id, 0.5)

                        # Boost causal links
                        link_type = n["link_type"]
                        base_weight = n["weight"]

                        if link_type in ("causes", "caused_by"):
                            causal_boost = 2.0
                        elif link_type in ("enables", "prevents"):
                            causal_boost = 1.5
                        else:
                            causal_boost = 1.0

                        effective_weight = base_weight * causal_boost
                        new_activation = parent_activation * effective_weight * self.activation_decay

                        if new_activation > self.min_activation:
                            neighbor_result = RetrievalResult.from_db_row(dict(n))
                            queue.append((neighbor_result, new_activation))

        return results
