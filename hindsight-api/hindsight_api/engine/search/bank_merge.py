"""
Bank-weight merging for dual-bank parallel queries (S5).

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
