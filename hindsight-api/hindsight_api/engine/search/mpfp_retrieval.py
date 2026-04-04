"""
Meta-Path Forward Push (MPFP) graph retrieval.

A sublinear graph traversal algorithm for memory retrieval over heterogeneous
graphs with multiple edge types (semantic, temporal, causal, entity).

Combines meta-path patterns from HIN literature with Forward Push local
propagation from Approximate PPR.

Key properties:
- Sublinear in graph size (threshold pruning bounds active nodes)
- Lazy edge loading: only loads edges for frontier nodes, not entire graph
- Predefined patterns capture different retrieval intents
- All patterns run in parallel, results fused via RRF
- No LLM in the loop during traversal
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace

from ..db_utils import acquire_with_retry
from ..memory_engine import fq_table
from .graph_retrieval import GraphRetriever
from .types import MPFPTimings, RetrievalResult

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------


@dataclass
class EdgeTarget:
    """A neighbor node with its edge weight."""

    node_id: str
    weight: float


@dataclass
class EdgeCache:
    """
    Cache for lazily-loaded edges.

    Grows per-hop as edges are loaded for frontier nodes.
    Shared across patterns to avoid redundant loads.
    Loads ALL edge types at once to minimize DB queries.
    """

    # edge_type -> from_node_id -> list of EdgeTarget
    graphs: dict[str, dict[str, list[EdgeTarget]]] = field(default_factory=dict)
    # Track which nodes have been fully loaded (all edge types)
    _fully_loaded: set[str] = field(default_factory=set)
    # Timing stats
    db_queries: int = 0
    edge_load_time: float = 0.0

    def get_neighbors(self, edge_type: str, node_id: str) -> list[EdgeTarget]:
        """Get neighbors for a node via a specific edge type."""
        return self.graphs.get(edge_type, {}).get(node_id, [])

    def get_normalized_neighbors(self, edge_type: str, node_id: str, top_k: int) -> list[EdgeTarget]:
        """Get top-k neighbors with weights normalized to sum to 1."""
        neighbors = self.get_neighbors(edge_type, node_id)[:top_k]
        if not neighbors:
            return []

        total = sum(n.weight for n in neighbors)
        if total == 0:
            return []

        return [EdgeTarget(node_id=n.node_id, weight=n.weight / total) for n in neighbors]

    def is_fully_loaded(self, node_id: str) -> bool:
        """Check if all edges for this node have been loaded."""
        return node_id in self._fully_loaded

    def get_uncached(self, node_ids: list[str]) -> list[str]:
        """Get node IDs that haven't been fully loaded yet."""
        return [n for n in node_ids if not self.is_fully_loaded(n)]

    def add_all_edges(self, edges_by_type: dict[str, dict[str, list[EdgeTarget]]], all_queried: list[str]):
        """
        Add loaded edges to the cache (all edge types at once).

        Args:
            edges_by_type: Dict mapping edge_type -> from_node_id -> list of EdgeTarget
            all_queried: All node IDs that were queried (marks them as fully loaded)
        """
        for edge_type, edges in edges_by_type.items():
            if edge_type not in self.graphs:
                self.graphs[edge_type] = {}
            for node_id, neighbors in edges.items():
                self.graphs[edge_type][node_id] = neighbors

        # Mark all queried nodes as fully loaded (even if they have no edges)
        self._fully_loaded.update(all_queried)


@dataclass
class PatternResult:
    """Result from a single pattern traversal."""

    pattern: list[str]
    scores: dict[str, float]  # node_id -> accumulated mass


@dataclass(frozen=True)
class MPFPPatternSet:
    """
    Mode-specific MPFP pattern configuration. Immutable.

    Bio-mapping: PFC top-down attention modulating hippocampal strategy selection —
    different cognitive demands (Precision/Exploration/Analogy/Validation) require
    different memory access patterns and activation thresholds.
    """

    semantic_patterns: tuple[tuple[str, ...], ...]  # patterns seeded from semantic entry points
    temporal_patterns: tuple[tuple[str, ...], ...]  # patterns seeded from temporal entry points
    threshold: float  # mass pruning threshold (lower = explore further)
    top_k: int  # max results returned from rrf_fusion


# Mode-specific pattern sets (keyed by RetrievalMode.value to avoid circular import).
# Selected at retrieve() time when mode is provided; falls back to MPFPConfig defaults otherwise.
MODE_PATTERNS: dict[str, MPFPPatternSet] = {
    # Precision: short direct paths, high threshold — strongest signal only
    "precision": MPFPPatternSet(
        semantic_patterns=(
            ("semantic",),  # direct semantic neighbors
            ("entity", "semantic"),  # entity context (1 entity hop + 1 semantic)
        ),
        temporal_patterns=(("temporal",),),
        threshold=0.01,
        top_k=10,
    ),
    # Exploration: long multi-hop paths, low threshold — weak links included
    "exploration": MPFPPatternSet(
        semantic_patterns=(
            ("semantic", "semantic"),  # topic expansion
            ("entity", "temporal"),  # entity timeline
            ("co_activated", "semantic"),  # co-activation spreading
            ("temporal_proximity", "entity"),  # temporal proximity chains
        ),
        temporal_patterns=(
            ("temporal", "semantic"),
            ("temporal", "entity"),
        ),
        threshold=0.0001,
        top_k=30,
    ),
    # Analogy: schema link traversal — abstract patterns across domains
    "analogy": MPFPPatternSet(
        semantic_patterns=(
            ("schema", "entity"),  # schema → concrete instances
            ("schema", "semantic"),  # schema → related concepts
            ("semantic", "schema"),  # concept → its schema
        ),
        temporal_patterns=(("temporal", "semantic"),),
        threshold=0.001,
        top_k=20,
    ),
    # Validation: causal + contradiction chains — evidence and counter-evidence
    "validation": MPFPPatternSet(
        semantic_patterns=(
            ("causes", "entity"),  # causal forward chains
            ("caused_by", "semantic"),  # causal backward chains
            ("contradiction", "semantic"),  # contradicting evidence
        ),
        temporal_patterns=(("temporal", "semantic"),),
        threshold=0.001,
        top_k=20,
    ),
}


@dataclass
class MPFPConfig:
    """Configuration for MPFP algorithm."""

    alpha: float = 0.15  # teleport/keep probability
    threshold: float = 1e-6  # mass pruning threshold (lower = explore more)
    top_k_neighbors: int = 20  # fan-out limit per node

    # Patterns from semantic seeds
    patterns_semantic: list[list[str]] = field(
        default_factory=lambda: [
            ["semantic", "semantic"],  # topic expansion
            ["entity", "temporal"],  # entity timeline
            ["semantic", "causes"],  # reasoning chains (forward)
            ["semantic", "caused_by"],  # reasoning chains (backward)
            ["entity", "semantic"],  # entity context
        ]
    )

    # Patterns from temporal seeds
    patterns_temporal: list[list[str]] = field(
        default_factory=lambda: [
            ["temporal", "semantic"],  # what was happening then
            ["temporal", "entity"],  # who was involved then
        ]
    )


@dataclass
class SeedNode:
    """An entry point node with its initial score."""

    node_id: str
    score: float  # initial mass (e.g., similarity score)


# -----------------------------------------------------------------------------
# Lazy Edge Loading
# -----------------------------------------------------------------------------


async def load_all_edges_for_frontier(
    pool,
    node_ids: list[str],
) -> dict[str, dict[str, list[EdgeTarget]]]:
    """
    Load ALL edge types for frontier nodes in one query.

    Args:
        pool: Database connection pool
        node_ids: Frontier node IDs to load edges for

    Returns:
        Dict mapping edge_type -> from_node_id -> list of EdgeTarget
    """
    if not node_ids:
        return {}

    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            f"""
            SELECT ml.from_unit_id, ml.to_unit_id, ml.link_type, ml.weight
            FROM {fq_table("memory_links")} ml
            WHERE ml.from_unit_id = ANY($1::uuid[])
              AND ml.weight >= 0.1
            ORDER BY ml.from_unit_id, ml.link_type, ml.weight DESC
            """,
            node_ids,
        )

    # Group by edge_type -> from_node -> neighbors
    result: dict[str, dict[str, list[EdgeTarget]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        edge_type = row["link_type"]
        from_id = str(row["from_unit_id"])
        to_id = str(row["to_unit_id"])
        weight = row["weight"]
        result[edge_type][from_id].append(EdgeTarget(node_id=to_id, weight=weight))

    # Convert nested defaultdicts to regular dicts
    return {edge_type: dict(edges) for edge_type, edges in result.items()}


# -----------------------------------------------------------------------------
# Core Algorithm (Async with Lazy Loading)
# -----------------------------------------------------------------------------


async def mpfp_traverse_async(
    pool,
    seeds: list[SeedNode],
    pattern: list[str],
    config: MPFPConfig,
    cache: EdgeCache,
) -> PatternResult:
    """
    Async Forward Push traversal with lazy edge loading.

    Loads ALL edge types per hop to minimize DB queries.

    Args:
        pool: Database connection pool
        seeds: Entry point nodes with initial scores
        pattern: Sequence of edge types to follow
        config: Algorithm parameters
        cache: Shared edge cache (grows as edges are loaded)

    Returns:
        PatternResult with accumulated scores per node
    """
    if not seeds:
        return PatternResult(pattern=pattern, scores={})

    scores: dict[str, float] = {}

    # Initialize frontier with seed masses (normalized)
    total_seed_score = sum(s.score for s in seeds)
    if total_seed_score == 0:
        total_seed_score = len(seeds)  # fallback to uniform

    frontier: dict[str, float] = {s.node_id: s.score / total_seed_score for s in seeds}

    # Follow pattern hop by hop
    for edge_type in pattern:
        # Collect frontier nodes above threshold
        active_nodes = [node_id for node_id, mass in frontier.items() if mass >= config.threshold]

        if not active_nodes:
            break

        # Find nodes that need edge loading (all edge types at once)
        uncached = cache.get_uncached(active_nodes)

        # Batch load ALL edges for uncached nodes (one query for all edge types)
        if uncached:
            import time

            load_start = time.time()
            edges_by_type = await load_all_edges_for_frontier(pool, uncached)
            cache.edge_load_time += time.time() - load_start
            cache.db_queries += 1
            cache.add_all_edges(edges_by_type, uncached)

        # Propagate mass
        next_frontier: dict[str, float] = {}

        for node_id, mass in frontier.items():
            if mass < config.threshold:
                continue

            # Keep α portion for this node
            scores[node_id] = scores.get(node_id, 0) + config.alpha * mass

            # Push (1-α) to neighbors
            push_mass = (1 - config.alpha) * mass
            neighbors = cache.get_normalized_neighbors(edge_type, node_id, config.top_k_neighbors)

            for neighbor in neighbors:
                next_frontier[neighbor.node_id] = next_frontier.get(neighbor.node_id, 0) + push_mass * neighbor.weight

        frontier = next_frontier

    # Final frontier nodes get their remaining mass
    for node_id, mass in frontier.items():
        if mass >= config.threshold:
            scores[node_id] = scores.get(node_id, 0) + mass

    return PatternResult(pattern=pattern, scores=scores)


def rrf_fusion(
    results: list[PatternResult],
    k: int = 60,
    top_k: int = 50,
) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion to combine pattern results.

    Args:
        results: List of pattern results
        k: RRF constant (higher = more uniform weighting)
        top_k: Number of results to return

    Returns:
        List of (node_id, fused_score) tuples, sorted by score descending
    """
    fused: dict[str, float] = {}

    for result in results:
        if not result.scores:
            continue

        # Rank nodes by their score in this pattern
        ranked = sorted(result.scores.keys(), key=lambda n: result.scores[n], reverse=True)

        for rank, node_id in enumerate(ranked):
            fused[node_id] = fused.get(node_id, 0) + 1.0 / (k + rank + 1)

    # Sort by fused score and return top-k
    sorted_results = sorted(fused.items(), key=lambda x: x[1], reverse=True)

    return sorted_results[:top_k]


# -----------------------------------------------------------------------------
# Database Loading
# -----------------------------------------------------------------------------


async def fetch_memory_units_by_ids(
    pool,
    node_ids: list[str],
    tags: list[str] | None = None,
) -> list[RetrievalResult]:
    """Fetch full memory unit details for a list of node IDs.

    Args:
        pool: Database connection pool.
        node_ids: Engram IDs to fetch.
        tags: Optional tag filter — only return Engrams whose tags contain all given values.
    """
    if not node_ids:
        return []

    params = [node_ids]
    join_clause = ""
    tag_filter = ""
    if tags:
        params.append(tags)
        join_clause = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
        tag_filter = "AND ed.tags @> $2::jsonb"

    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(
            f"""
            SELECT mu.id, mu.text, mu.context, mu.event_date, mu.occurred_start, mu.occurred_end,
                   mu.mentioned_at, mu.access_count, mu.embedding, mu.fact_type, mu.document_id, mu.chunk_id
            FROM {fq_table("memory_units")} mu
            {join_clause}
            WHERE mu.id = ANY($1::uuid[])
              {tag_filter}
            """,
            *params,
        )

    return [RetrievalResult.from_db_row(dict(r)) for r in rows]


# -----------------------------------------------------------------------------
# Graph Retriever Implementation
# -----------------------------------------------------------------------------


class MPFPGraphRetriever(GraphRetriever):
    """
    Graph retrieval using Meta-Path Forward Push with lazy edge loading.

    Runs predefined patterns in parallel from semantic and temporal seeds,
    loading edges on-demand per hop instead of loading entire graph upfront.
    """

    def __init__(self, config: MPFPConfig | None = None):
        """
        Initialize MPFP retriever.

        Args:
            config: Algorithm configuration (uses defaults if None)
        """
        self.config = config or MPFPConfig()

    @property
    def name(self) -> str:
        return "mpfp"

    async def retrieve(
        self,
        pool,
        query_embedding_str: str,
        bank_id: str,
        budget: int,
        query_text: str | None = None,
        semantic_seeds: list[RetrievalResult] | None = None,
        temporal_seeds: list[RetrievalResult] | None = None,
        adjacency=None,  # Ignored - kept for interface compatibility
        tags: list[str] | None = None,
        fact_type: str | None = None,  # Deprecated, ignored
        mode=None,  # RetrievalMode | None — selects mode-aware pattern set
    ) -> tuple[list[RetrievalResult], MPFPTimings | None]:
        """
        Retrieve facts using MPFP algorithm with lazy edge loading.

        Args:
            pool: Database connection pool
            query_embedding_str: Query embedding (used for fallback seed finding)
            bank_id: Memory bank ID
            budget: Maximum results to return
            query_text: Original query text (optional)
            semantic_seeds: Pre-computed semantic entry points
            temporal_seeds: Pre-computed temporal entry points
            adjacency: Ignored (kept for interface compatibility)
            tags: Optional tag filter — only return Engrams whose tags contain all given values.
            fact_type: Deprecated, ignored.
            mode: Optional RetrievalMode — selects mode-aware pattern set from MODE_PATTERNS.
                  Falls back to MPFPConfig defaults when None.

        Returns:
            Tuple of (List of RetrievalResult with activation scores, MPFPTimings)
        """
        import time

        timings = MPFPTimings(tags=tags or [])

        # Resolve mode-specific pattern set (bio: PFC top-down attention on hippocampal traversal)
        mode_key = mode.value if mode is not None else None
        if mode_key is not None and mode_key in MODE_PATTERNS:
            ps = MODE_PATTERNS[mode_key]
            semantic_patterns = [list(p) for p in ps.semantic_patterns]
            temporal_patterns = [list(p) for p in ps.temporal_patterns]
            effective_top_k = ps.top_k
            # Override config threshold for this mode while keeping other params
            effective_config = replace(self.config, threshold=ps.threshold)
            logger.debug(
                "MPFP mode=%s patterns=%d+%d threshold=%.4f top_k=%d",
                mode_key,
                len(semantic_patterns),
                len(temporal_patterns),
                ps.threshold,
                effective_top_k,
            )
        else:
            semantic_patterns = self.config.patterns_semantic
            temporal_patterns = self.config.patterns_temporal
            effective_top_k = 50  # rrf_fusion default
            effective_config = self.config

        # Convert seeds to SeedNode format
        semantic_seed_nodes = self._convert_seeds(semantic_seeds, "similarity")
        temporal_seed_nodes = self._convert_seeds(temporal_seeds, "temporal_score")

        # If no semantic seeds provided, fall back to finding our own
        if not semantic_seed_nodes:
            seeds_start = time.time()
            semantic_seed_nodes = await self._find_semantic_seeds(pool, query_embedding_str, bank_id, tags=tags)
            timings.seeds_time = time.time() - seeds_start

        # Collect all pattern jobs
        pattern_jobs = []

        # Patterns from semantic seeds
        for pattern in semantic_patterns:
            if semantic_seed_nodes:
                pattern_jobs.append((semantic_seed_nodes, pattern))

        # Patterns from temporal seeds
        for pattern in temporal_patterns:
            if temporal_seed_nodes:
                pattern_jobs.append((temporal_seed_nodes, pattern))

        if not pattern_jobs:
            return [], timings

        timings.pattern_count = len(pattern_jobs)

        # Shared edge cache across all patterns
        cache = EdgeCache()

        # Run all patterns in parallel (each does lazy edge loading)
        step_start = time.time()
        pattern_tasks = [
            mpfp_traverse_async(pool, seeds, pattern, effective_config, cache) for seeds, pattern in pattern_jobs
        ]
        pattern_results = await asyncio.gather(*pattern_tasks)
        timings.traverse = time.time() - step_start

        # Record edge loading stats from cache
        timings.edge_count = sum(len(neighbors) for g in cache.graphs.values() for neighbors in g.values())
        timings.db_queries = cache.db_queries
        timings.edge_load_time = cache.edge_load_time

        # Fuse results (mode-specific top_k)
        step_start = time.time()
        fused = rrf_fusion(pattern_results, top_k=min(budget, effective_top_k))
        timings.fusion = time.time() - step_start

        if not fused:
            return [], timings

        # Get top result IDs
        result_ids = [node_id for node_id, score in fused][:budget]

        # Fetch full details
        step_start = time.time()
        results = await fetch_memory_units_by_ids(pool, result_ids, tags=tags)
        timings.fetch = time.time() - step_start
        timings.result_count = len(results)

        # Add activation scores from fusion
        score_map = {node_id: score for node_id, score in fused}
        for result in results:
            result.activation = score_map.get(result.id, 0.0)

        # Sort by activation
        results.sort(key=lambda r: r.activation or 0, reverse=True)

        return results, timings

    def _convert_seeds(
        self,
        seeds: list[RetrievalResult] | None,
        score_attr: str,
    ) -> list[SeedNode]:
        """Convert RetrievalResult seeds to SeedNode format."""
        if not seeds:
            return []

        result = []
        for seed in seeds:
            score = getattr(seed, score_attr, None)
            if score is None:
                score = seed.activation or seed.similarity or 1.0
            result.append(SeedNode(node_id=seed.id, score=score))

        return result

    async def _find_semantic_seeds(
        self,
        pool,
        query_embedding_str: str,
        bank_id: str,
        limit: int = 20,
        threshold: float = 0.3,
        tags: list[str] | None = None,
    ) -> list[SeedNode]:
        """Fallback: find semantic seeds via embedding search."""
        params = [query_embedding_str, bank_id, threshold, limit]
        join_clause = ""
        tag_filter = ""
        if tags:
            params.insert(2, tags)
            join_clause = f"JOIN {fq_table('engram_dictionary')} ed ON ed.engram_id = mu.id"
            tag_filter = "AND ed.tags @> $3::jsonb"
            # renumber: $3=tags, $4=threshold, $5=limit
            threshold_idx, limit_idx = 4, 5
        else:
            threshold_idx, limit_idx = 3, 4

        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT mu.id, 1 - (mu.embedding <=> $1::vector) AS similarity
                FROM {fq_table("memory_units")} mu
                {join_clause}
                WHERE mu.bank_id = $2
                  AND mu.embedding IS NOT NULL
                  {tag_filter}
                  AND (1 - (mu.embedding <=> $1::vector)) >= ${threshold_idx}
                ORDER BY mu.embedding <=> $1::vector
                LIMIT ${limit_idx}
                """,
                *params,
            )

        return [SeedNode(node_id=str(r["id"]), score=r["similarity"]) for r in rows]
