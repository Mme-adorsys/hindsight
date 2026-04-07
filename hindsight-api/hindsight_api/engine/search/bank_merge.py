"""
Bank-weight merging for dual-bank parallel queries (S5 + Epic 14 S4).

When recall_async queries both an Agent Session Bank and a Shared Bank in parallel,
results must be merged with mode-dependent weighting before the existing RRF pipeline.

Bio-mapping:
  Agent Bank = episodic/hippocampal memory — personal, session-specific, recent
  Shared Bank = semantic/neocortical memory — consolidated, cross-agent, long-term

Mode determines the weighting ratio:
  Precision:   agent 0.7 / shared 0.3  — trust established personal knowledge
  Exploration: agent 0.3 / shared 0.7  — cast wide net across shared knowledge
  Analogy:     agent 0.3 / shared 0.7  — cross-domain requires shared context
  Validation:  agent 0.5 / shared 0.5  — balanced cross-check of both banks

Epic 14 S4 additions:
  Schema-Boost:     Shared results with fact_type='schema' get +0.2 similarity boost.
                    Schema Engrams encode generalised knowledge — they are worth
                    surfacing even when their raw similarity is moderate.
  Freshness Penalty: All shared results are multiplied by 0.95 to give a slight
                    edge to fresher agent-bank results of equal relevance.
  Cross-Bank Dedup: If an Agent-Bank result and a Shared-Bank result share the same
                    engram_id (promoted copy), keep the Agent version (more context).

Concept reference: docs/engram/concept.md § 7 Session Layer, § 15 Multi-Bank Architecture
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retrieval import ParallelRetrievalResult
    from .types import RetrievalResult

# ---------------------------------------------------------------------------
# Mode-dependent bank weights
# ---------------------------------------------------------------------------
BANK_WEIGHTS: dict[str, tuple[float, float]] = {
    "precision": (0.7, 0.3),
    "exploration": (0.3, 0.7),
    "analogy": (0.3, 0.7),
    "validation": (0.5, 0.5),
}
_DEFAULT_BANK_WEIGHTS: tuple[float, float] = (0.7, 0.3)  # Precision behavior as default

# Epic 14 S4 — Shared-Bank post-processing constants
SCHEMA_BOOST: float = 0.2  # T2: extra similarity added for schema-type shared results
SHARED_FRESHNESS_PENALTY: float = 0.95  # T3: score multiplier for all shared results


def get_bank_weights(mode) -> tuple[float, float]:
    """
    Return (agent_weight, shared_weight) for the given RetrievalMode.

    Falls back to precision weights (0.7/0.3) when mode is None.
    """
    if mode is None:
        return _DEFAULT_BANK_WEIGHTS
    return BANK_WEIGHTS.get(mode.value, _DEFAULT_BANK_WEIGHTS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scale_and_mark(results: list[RetrievalResult], weight: float, source: str) -> None:
    """
    Mark source and scale all retrieval scores by weight in-place.

    Scale happens before concatenation so downstream RRF naturally reflects
    bank weighting — no post-hoc injection needed.
    """
    for r in results:
        r.source = source
        if r.similarity is not None:
            r.similarity *= weight
        if r.bm25_score is not None:
            r.bm25_score *= weight
        if r.activation is not None:
            r.activation *= weight
        if r.temporal_score is not None:
            r.temporal_score *= weight


def _apply_schema_boost(results: list[RetrievalResult], boost: float = SCHEMA_BOOST) -> None:
    """
    T2 — Boost the similarity score of schema-type Shared Bank results.

    Schema Engrams (fact_type='schema') encode abstract, generalised knowledge.
    They deserve a small upward nudge so they surface even when raw similarity
    is moderate — analogous to the neocortex giving priority to schema-encoded
    memories during retrieval.
    """
    for r in results:
        if r.fact_type == "schema" and r.similarity is not None:
            r.similarity += boost


def _apply_freshness_penalty(results: list[RetrievalResult], penalty: float = SHARED_FRESHNESS_PENALTY) -> None:
    """
    T3 — Apply a mild freshness penalty to all Shared Bank scores.

    Shared Bank Engrams have undergone consolidation (NCR) and may be older
    than agent-bank memories. A ×0.95 multiplier gives a slight preference
    to fresher agent-bank results when scores are otherwise equal.
    """
    for r in results:
        if r.similarity is not None:
            r.similarity *= penalty
        if r.bm25_score is not None:
            r.bm25_score *= penalty
        if r.activation is not None:
            r.activation *= penalty
        if r.temporal_score is not None:
            r.temporal_score *= penalty


def _deduplicate_shared(
    agent_lists: list[list[RetrievalResult]],
    shared_lists: list[list[RetrievalResult]],
) -> None:
    """
    T4 — Remove Shared Bank results whose ID already appears in Agent Bank results.

    When an Agent-Dictionary Engram is promoted to the Shared Bank (Epic 14 S3),
    both the original and the shared copy may appear in parallel query results.
    The Agent version is more contextually specific — keep it, drop the shared copy.

    Mutates `shared_lists` in-place.
    """
    agent_ids: set[str] = set()
    for lst in agent_lists:
        for r in lst:
            agent_ids.add(r.id)

    for lst in shared_lists:
        remove_indices = [i for i, r in enumerate(lst) if r.id in agent_ids]
        for i in reversed(remove_indices):
            lst.pop(i)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def merge_parallel_results(
    agent_rr: ParallelRetrievalResult,
    shared_rr: ParallelRetrievalResult,
    mode,
) -> ParallelRetrievalResult:
    """
    Merge two ParallelRetrievalResult objects with mode-dependent bank weights.

    Marks all agent results with source='agent', shared results with source='shared'.
    Scales scores by bank weight before concatenation so the existing RRF pipeline
    naturally produces mode-weighted rankings without any further changes.

    Epic 14 S4 enhancements applied to shared results (in order):
      1. Schema boost  (+0.2 similarity for fact_type='schema')
      2. Freshness penalty (×0.95 all scores)
      3. Cross-bank deduplication (drop shared copies already present in agent bank)

    Args:
        agent_rr:  Results from the Agent Session Bank (PostgreSQL/MPFP)
        shared_rr: Results from the Shared Bank (Qdrant+Neo4j/EngramRetriever)
        mode:      RetrievalMode | None — determines (agent_w, shared_w)

    Returns:
        Single merged ParallelRetrievalResult ready for the existing RRF pipeline.
    """
    from .retrieval import ParallelRetrievalResult

    agent_w, shared_w = get_bank_weights(mode)

    _scale_and_mark(agent_rr.semantic, agent_w, "agent")
    _scale_and_mark(agent_rr.bm25, agent_w, "agent")
    _scale_and_mark(agent_rr.graph, agent_w, "agent")
    if agent_rr.temporal:
        _scale_and_mark(agent_rr.temporal, agent_w, "agent")

    _scale_and_mark(shared_rr.semantic, shared_w, "shared")
    _scale_and_mark(shared_rr.bm25, shared_w, "shared")
    _scale_and_mark(shared_rr.graph, shared_w, "shared")
    if shared_rr.temporal:
        _scale_and_mark(shared_rr.temporal, shared_w, "shared")

    # T2 — Schema boost (after weight scaling so boost is mode-independent)
    shared_all = [shared_rr.semantic, shared_rr.bm25, shared_rr.graph] + (
        [shared_rr.temporal] if shared_rr.temporal else []
    )
    for lst in shared_all:
        _apply_schema_boost(lst)

    # T3 — Freshness penalty for shared results
    for lst in shared_all:
        _apply_freshness_penalty(lst)

    # T4 — Deduplicate: drop shared results already covered by agent bank
    agent_all = [agent_rr.semantic, agent_rr.bm25, agent_rr.graph] + ([agent_rr.temporal] if agent_rr.temporal else [])
    _deduplicate_shared(agent_all, shared_all)

    merged_temporal = (agent_rr.temporal or []) + (shared_rr.temporal or [])

    return ParallelRetrievalResult(
        semantic=agent_rr.semantic + shared_rr.semantic,
        bm25=agent_rr.bm25 + shared_rr.bm25,
        graph=agent_rr.graph + shared_rr.graph,
        temporal=merged_temporal if merged_temporal else None,
        timings={
            **agent_rr.timings,
            **{f"shared_{k}": v for k, v in shared_rr.timings.items()},
        },
        temporal_constraint=agent_rr.temporal_constraint or shared_rr.temporal_constraint,
        mpfp_timings=agent_rr.mpfp_timings + shared_rr.mpfp_timings,
    )
