"""
EngramRetriever — Neo4j + Qdrant graph retrieval strategy (S6).

Implements the GraphRetriever interface using the hybrid Engram storage:
- Seeds:     Qdrant vector similarity (CA3 pattern completion)
- Traversal: Neo4j graph BFS (CA1 spreading activation)
- Enrichment: PostgreSQL memory_units + engram_dictionary (text + metadata)

Bio-mapping: The full CA3→CA1 recall loop — content-addressed retrieval
followed by associative spreading, then text enrichment from the Engram
Dictionary. Coexists with MPFP/BFS — Session Layer routes by bank type.

Concept reference: docs/engram/concept.md § 8 Search & Retrieval (S6)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from ..db_utils import acquire_with_retry
from ..memory_engine import fq_table
from ..neo4j_client import RELATIONSHIP_TYPES, Neo4jEngineClient
from ..qdrant_client import QdrantEngineClient
from .graph_retrieval import GraphRetriever
from .types import MPFPTimings, RetrievalResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mode-dependent seed limits
# Precision: focused (5), Exploration: broad (20), Analogy/Validation: medium (10)
# ---------------------------------------------------------------------------
_SEED_LIMITS: dict[str, int] = {
    "precision": 5,
    "exploration": 20,
    "analogy": 10,
    "validation": 10,
}
_DEFAULT_SEED_LIMIT = 10

# Traversal depth per ModeConfig.traversal_depth string
_DEPTH_MAP: dict[str, int] = {
    "shallow": 1,
    "medium": 2,
    "deep": 3,
}
_DEFAULT_DEPTH = 2

# Weak-link relationship types (co-activation and temporal proximity)
# Bio mapping: STC (Synaptic Tagging & Capture) — fragile associations that
# require repeated co-activation to consolidate into strong links.
WEAK_LINK_TYPES: frozenset[str] = frozenset({"CO_ACTIVATED", "TEMPORAL_PROXIMITY"})

# Minimum weight for weak-link traversal (T3)
# Below this threshold, a weak link is too fragile to be meaningful.
WEAK_LINK_MIN_WEIGHT: float = 0.1

# Score multiplier for prefer-mode (Analogy): weak-link hits receive a boost
# so they rank above equivalent strong-link hits, surfacing cross-domain patterns.
WEAK_LINK_PREFER_BOOST: float = 1.5


def _resolve_mode_params(mode) -> tuple[int, list[str], int, str]:
    """
    Resolve seed limit, Neo4j relationship types, traversal depth, and weak_link_policy from mode.

    Returns:
        (seed_limit, rel_types, max_depth, weak_link_policy)

    rel_types reflects the weak_link_policy:
      - ignore: CO_ACTIVATED + TEMPORAL_PROXIMITY excluded
      - follow: all rel types included (default)
      - prefer: all rel types included (weak-link boost handled separately in retrieve())
    """
    if mode is None:
        return _DEFAULT_SEED_LIMIT, list(RELATIONSHIP_TYPES), _DEFAULT_DEPTH, "follow"

    mode_key = mode.value  # e.g. "precision", "exploration"
    seed_limit = _SEED_LIMITS.get(mode_key, _DEFAULT_SEED_LIMIT)

    # Derive rel_types from MODE_PATTERNS (same edge types, uppercased for Neo4j)
    try:
        from .mpfp_retrieval import MODE_PATTERNS

        ps = MODE_PATTERNS.get(mode_key)
        if ps:
            flat = {edge.upper() for pattern in ps.semantic_patterns for edge in pattern}
            rel_types = [rt for rt in RELATIONSHIP_TYPES if rt in flat] or list(RELATIONSHIP_TYPES)
        else:
            rel_types = list(RELATIONSHIP_TYPES)
    except ImportError:
        rel_types = list(RELATIONSHIP_TYPES)

    # Derive max_depth and weak_link_policy from ModeConfig
    weak_link_policy = "follow"
    try:
        from ..session.mode_config import MODE_PROFILES

        profile = MODE_PROFILES.get(mode)
        if profile:
            max_depth = _DEPTH_MAP.get(profile.traversal_depth, _DEFAULT_DEPTH)
            weak_link_policy = profile.weak_link_policy
        else:
            max_depth = _DEFAULT_DEPTH
    except ImportError:
        max_depth = _DEFAULT_DEPTH

    # T1: Apply weak_link_policy to rel_types filter
    # ignore → strip CO_ACTIVATED + TEMPORAL_PROXIMITY from traversal
    # follow/prefer → keep all (prefer does a separate boost pass in retrieve())
    if weak_link_policy == "ignore":
        rel_types = [rt for rt in rel_types if rt not in WEAK_LINK_TYPES]

    return seed_limit, rel_types, max_depth, weak_link_policy


class EngramRetriever(GraphRetriever):
    """
    Graph retrieval via Neo4j + Qdrant — the hybrid Engram recall path.

    Three phases:
    1. Seed:     Qdrant vector search → top-k engram_ids (or reuse semantic_seeds)
    2. Traverse: Neo4j BFS from seeds → activation map {engram_id → score}
    3. Enrich:   Batch PostgreSQL query for text + metadata → RetrievalResult list

    This retriever implements the same GraphRetriever ABC as BFS and MPFP,
    so it drops into the existing retrieve_parallel pipeline unchanged.
    """

    @property
    def name(self) -> str:
        return "engram"

    def __init__(
        self,
        qdrant: QdrantEngineClient,
        neo4j: Neo4jEngineClient,
    ) -> None:
        self._qdrant = qdrant
        self._neo4j = neo4j

    def get_qdrant_client(self) -> QdrantEngineClient:
        """Return the Qdrant client (public accessor for reconsolidation and scoring pipelines)."""
        return self._qdrant

    # ------------------------------------------------------------------
    # T2 — Qdrant Seed Phase
    # ------------------------------------------------------------------

    async def _get_seeds(
        self,
        query_embedding: list[float],
        bank_id: str,
        limit: int,
        tags: list[str] | None,
    ) -> list[str]:
        """
        Query Qdrant for vector-similar Engrams and return their IDs.

        Filters by bank_id; optionally by tags (must match all tags).
        Returns up to `limit` engram_id strings.
        """
        must_filters = [{"key": "bank_id", "match": {"value": bank_id}}]
        if tags:
            for tag in tags:
                must_filters.append({"key": "tags", "match": {"value": tag}})

        hits = await self._qdrant.search_similar(
            embedding=query_embedding,
            limit=limit,
            filters={"must": must_filters} if must_filters else None,
        )
        return [h["engram_id"] for h in hits]

    # ------------------------------------------------------------------
    # T3 — Neo4j Traversal Phase
    # ------------------------------------------------------------------

    async def _traverse(
        self,
        seed_ids: list[str],
        rel_types: list[str],
        max_depth: int,
        min_weight: float = 0.01,
    ) -> dict[str, float]:
        """
        BFS from each seed via Neo4j; return engram_id → activation score.

        Activation score: `1.0 / (1 + depth)` — depth-decaying.
        Seeds at depth=0 inherit 1.0 (set by caller).
        Parallel traversals for each seed via asyncio.gather.
        """
        if not seed_ids:
            return {}

        async def _traverse_one(seed_id: str) -> list[dict]:
            try:
                return await self._neo4j.traverse(
                    start_id=seed_id,
                    rel_types=rel_types,
                    max_depth=max_depth,
                    min_weight=min_weight,
                )
            except Exception as exc:
                logger.warning(f"[EngramRetriever] Neo4j traverse failed for seed {seed_id}: {exc}")
                return []

        all_results = await asyncio.gather(*[_traverse_one(sid) for sid in seed_ids])

        activation_map: dict[str, float] = {}
        for results in all_results:
            for node in results:
                eid = node["engram_id"]
                depth = node.get("depth", max_depth)
                score = 1.0 / (1.0 + depth)
                # Keep the highest activation if reached via multiple paths
                if eid not in activation_map or score > activation_map[eid]:
                    activation_map[eid] = score

        return activation_map

    # ------------------------------------------------------------------
    # T4 — Enrichment Phase
    # ------------------------------------------------------------------

    async def _enrich(
        self,
        pool,
        bank_id: str,
        activation_map: dict[str, float],
        budget: int,
    ) -> list[RetrievalResult]:
        """
        Batch-fetch text + metadata from PostgreSQL for traversed engram_ids.

        Joins memory_units with engram_dictionary to get both content and
        Thalamus/Strength metadata. Returns RetrievalResult list sorted by
        activation (descending), truncated to budget.
        """
        if not activation_map:
            return []

        # Sort by activation, take top budget IDs (avoid fetching everything)
        top_ids = sorted(activation_map, key=activation_map.__getitem__, reverse=True)[:budget]

        async with acquire_with_retry(pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT mu.id::text,
                       mu.text,
                       mu.fact_type,
                       mu.context,
                       mu.occurred_start,
                       mu.occurred_end,
                       mu.mentioned_at,
                       mu.access_count,
                       mu.document_id,
                       mu.chunk_id
                FROM {fq_table("memory_units")} mu
                WHERE mu.id = ANY($1::uuid[])
                  AND mu.bank_id = $2
                """,
                top_ids,
                bank_id,
            )

        results = []
        for row in rows:
            rr = RetrievalResult.from_db_row(dict(row))
            rr.activation = activation_map.get(rr.id, 0.0)
            results.append(rr)

        results.sort(key=lambda r: r.activation or 0.0, reverse=True)
        return results[:budget]

    # ------------------------------------------------------------------
    # T5 — retrieve() Orchestration
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        pool,
        query_embedding_str: str,
        bank_id: str,
        budget: int,
        query_text: str | None = None,
        semantic_seeds: list[RetrievalResult] | None = None,
        temporal_seeds: list[RetrievalResult] | None = None,
        adjacency=None,
        tags: list[str] | None = None,
        fact_type: str | None = None,  # Deprecated, ignored
        mode=None,  # RetrievalMode | None
    ) -> tuple[list[RetrievalResult], MPFPTimings | None]:
        """
        Full EngramRetriever recall: Seeds → Traverse → Enrich.

        If semantic_seeds are already available (from retrieve_parallel upstream),
        they are used as the traversal starting points instead of calling Qdrant
        again (avoids a redundant vector search).

        Args:
            pool: PostgreSQL pool (for enrichment queries)
            query_embedding_str: JSON string of query embedding vector
            bank_id: Memory bank identifier
            budget: Maximum results to return
            semantic_seeds: Pre-computed seeds from upstream retrieval (optional)
            tags: Optional tag filter for Qdrant seed phase
            mode: Optional RetrievalMode for mode-aware seed limits + traversal depth

        Returns:
            (results, timings) — timings reuses MPFPTimings for pipeline compat
        """
        seed_limit, rel_types, max_depth, weak_link_policy = _resolve_mode_params(mode)

        t_start = time.time()

        # Phase 1: Seeds
        if semantic_seeds:
            seed_ids = [s.id for s in semantic_seeds[:seed_limit]]
        else:
            try:
                embedding = json.loads(query_embedding_str)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(f"[EngramRetriever] Failed to parse query_embedding_str: {exc}")
                return [], None
            seed_ids = await self._get_seeds(embedding, bank_id, seed_limit, tags)

        seed_time = time.time() - t_start

        if not seed_ids:
            logger.debug(f"[EngramRetriever] No seeds found for bank {bank_id}")
            return [], None

        # Phase 2: Neo4j traversal
        # T1: rel_types already filtered by weak_link_policy in _resolve_mode_params
        t_traverse = time.time()
        activation_map = await self._traverse(seed_ids, rel_types, max_depth)
        traverse_time = time.time() - t_traverse

        # Inject seeds themselves at activation=1.0 (not traversed from, but matched directly)
        for sid in seed_ids:
            activation_map.setdefault(sid, 1.0)

        # seed_id_set created early — needed by both prefer-mode and traversal_source marking
        seed_id_set = set(seed_ids)

        # T2: Prefer mode — additional weak-link traversal with score boost (Analogy mode)
        # Bio mapping: Analogy thinking preferentially follows weak associative bridges
        # to surface cross-domain patterns not reachable via strong links.
        # Fix 17: only add to weak_link_ids when the boost actually wins over the strong-link score,
        # so nodes that are primarily strong-link results are never mis-marked as weak-link.
        weak_link_ids: set[str] = set()
        if weak_link_policy == "prefer":
            weak_rel_types = list(WEAK_LINK_TYPES)
            weak_activation = await self._traverse(seed_ids, weak_rel_types, max_depth, min_weight=WEAK_LINK_MIN_WEIGHT)
            for eid, score in weak_activation.items():
                boosted = score * WEAK_LINK_PREFER_BOOST
                if eid not in activation_map or boosted > activation_map[eid]:
                    activation_map[eid] = boosted
                    # Only mark as weak-link when the weak path actually dominates
                    # and the node is not a direct seed (seeds are primary matches, not associations)
                    if eid not in seed_id_set:
                        weak_link_ids.add(eid)
            logger.debug(
                "[EngramRetriever] prefer-mode weak-link traversal: %d additional nodes (boost=×%.1f)",
                len(weak_link_ids),
                WEAK_LINK_PREFER_BOOST,
            )

        # Fix 20: log a warning for unknown modes (silent fallback is hard to diagnose)
        if mode is not None and mode.value not in _SEED_LIMITS:
            logger.warning(
                "[EngramRetriever] Unknown mode '%s'; used default seed_limit=%d",
                mode.value,
                _DEFAULT_SEED_LIMIT,
            )

        # Phase 3: Enrichment
        t_enrich = time.time()
        results = await self._enrich(pool, bank_id, activation_map, budget)
        enrich_time = time.time() - t_enrich

        # T4: Mark results reached via weak links (only when weak path dominated, not seeds)
        for rr in results:
            if rr.id in weak_link_ids:
                rr.traversal_source = "weak_link"

        logger.debug(
            "[EngramRetriever] bank=%s seeds=%d traversed=%d enriched=%d "
            "seed_time=%.3fs traverse_time=%.3fs enrich_time=%.3fs",
            bank_id,
            len(seed_ids),
            len(activation_map),
            len(results),
            seed_time,
            traverse_time,
            enrich_time,
        )

        # Reuse MPFPTimings for pipeline compatibility
        # edge_count repurposed as seed_count; db_queries as traversal node count
        timings = MPFPTimings(
            tags=tags or [],
            edge_count=len(seed_ids),
            db_queries=len(activation_map),
            traverse=traverse_time,
            pattern_count=len(rel_types),
            fetch=enrich_time,
            seeds_time=seed_time,
            result_count=len(results),
        )
        return results, timings
