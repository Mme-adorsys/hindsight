"""
NCR Phase 3 — EngramSchemaProcessor.

Concrete implementation of SchemaProcessor that orchestrates all 5 Game-of-Life
Schema Emergence rules (R1–R5) in order:

    R1 → Clustering/Birth      (schema_clustering.run_clustering)
    R2+R3 → Maturation+Abstraction (schema_maturation.run_maturation)
    R5 → Competition/Death     (schema_competition.run_competition)

R4 (Reinforcement) runs at Retain time — not called here.

Each step is wrapped in try/except: a failure in R1 does not prevent R2+R3 or
R5 from running.  All errors are logged at ERROR level.

Biological mapping:
  REM Sleep — whole-graph pattern detection, schema birth/death, compression.
  Simple local rules produce emergent schema structure (Conway's Game of Life).
  Ref: concept.md ch. 13 — Schema Emergence, 5 Game-of-Life Rules

Epic 13, Story 04.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import asyncpg

from hindsight_api.engine.consolidation.schema_clustering import run_clustering
from hindsight_api.engine.consolidation.schema_competition import run_competition
from hindsight_api.engine.consolidation.schema_maturation import run_maturation
from hindsight_api.engine.consolidation.schema_processor import SchemaProcessor, SchemaResult

if TYPE_CHECKING:
    from hindsight_api.engine.neo4j_client import Neo4jEngineClient
    from hindsight_api.engine.qdrant_client import QdrantEngineClient
    from hindsight_api.engine.response_models import FullEngram

logger = logging.getLogger(__name__)


class EngramSchemaProcessor(SchemaProcessor):
    """
    Real SchemaProcessor — orchestrates R1 → R2+R3 → R5 each NCR cycle.

    Args:
        neo4j_client: Connected Neo4j client.
        qdrant_client: Connected Qdrant client.
        pool:          asyncpg connection pool (for Dictionary updates).
        llm:           LLM client with `.call()` method (used by R2+R3 for abstraction).
                       If None, maturation uses fallback content (no LLM call).
        embed_fn:      Async function ``(text: str) -> list[float]`` for embedding
                       new Schema content (used by R2+R3).
    """

    def __init__(
        self,
        neo4j_client: "Neo4jEngineClient",
        qdrant_client: "QdrantEngineClient",
        pool: asyncpg.Pool,
        llm: object | None,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> None:
        self._neo4j = neo4j_client
        self._qdrant = qdrant_client
        self._pool = pool
        self._llm = llm
        self._embed_fn = embed_fn

    async def process(self, bank_id: str, engrams: list["FullEngram"]) -> SchemaResult:
        """
        Run R1 → R2+R3 → R5 for *bank_id*.

        Args:
            bank_id: The memory bank to process.
            engrams: Neocortex Engrams (passed by NCR Orchestrator — not used
                     directly here; sub-modules query Neo4j for what they need).

        Returns:
            SchemaResult with metrics from all three phases.
        """
        result = SchemaResult()

        # ── R1: Clustering / Birth ────────────────────────────────────────────
        try:
            cluster_result = await run_clustering(bank_id, self._neo4j)
            result.clusters_found = cluster_result.candidates_created
            logger.info(
                "[Schema/R1] bank=%s clusters_found=%d",
                bank_id,
                result.clusters_found,
            )
        except Exception as exc:
            logger.error("[Schema/R1] Clustering failed for bank %s: %s", bank_id, exc)

        # ── R2+R3: Maturation + Abstraction ──────────────────────────────────
        try:
            mat_result = await run_maturation(
                bank_id,
                self._neo4j,
                self._qdrant,
                self._pool,
                self._llm,
                self._embed_fn,
            )
            result.schemas_matured = mat_result.candidates_incremented
            result.schemas_created = mat_result.schemas_created
            result.created = mat_result.schemas_created  # backward-compat alias
            result.details = mat_result.details  # type: ignore[assignment]
            logger.info(
                "[Schema/R2+R3] bank=%s schemas_created=%d matured=%d",
                bank_id,
                result.schemas_created,
                result.schemas_matured,
            )
        except Exception as exc:
            logger.error("[Schema/R2+R3] Maturation failed for bank %s: %s", bank_id, exc)

        # ── R5: Competition / Death ───────────────────────────────────────────
        try:
            comp_result = await run_competition(bank_id, self._neo4j, self._qdrant, self._pool)
            result.schemas_deleted = comp_result.schemas_deleted
            result.deleted = comp_result.schemas_deleted  # backward-compat alias
            logger.info(
                "[Schema/R5] bank=%s schemas_deleted=%d active=%d avg_strength=%.3f",
                bank_id,
                result.schemas_deleted,
                comp_result.active_schemas,
                comp_result.avg_strength,
            )
        except Exception as exc:
            logger.error("[Schema/R5] Competition failed for bank %s: %s", bank_id, exc)

        return result
