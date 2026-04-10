"""
Composite Strength Scoring for Selective Consolidation (Epic 24).

The composite strength score combines the initial Thalamus gate saliency
with a logarithmic access-frequency metric to determine whether an
Engram in Working Memory should be promoted to the Buffer.

Formula:
    recount_score = log(1 + access_count) / log(2 + cycles_alive)
    total_score   = w_thalamus * thalamus_overall + w_recount * recount_score

Bio mapping:
- Thalamus component = synaptic tagging (initial plasticity marker)
- Recount component  = rehearsal-dependent capture (repeated reactivation)
- Logarithmic decay  = natural forgetting curve in hippocampus
- Asymmetric recovery = a reactivation of an old memory boosts the
  numerator (log scale) proportionally more than one additional cycle
  erodes the denominator

Ref: concept.md ch. 12 (Consolidation Pipeline), STC model.
"""

from __future__ import annotations

import math

# Default weights (user-confirmed 70/30 split).
W_THALAMUS: float = 0.7
W_RECOUNT: float = 0.3

# Thresholds
PROMOTE_THRESHOLD: float = 0.4
ARCHIVE_THRESHOLD_WM: float = 0.08
ARCHIVE_THRESHOLD_BUFFER: float = 0.05


def compute_recount_score(access_count: int, cycles_alive: int) -> float:
    """Logarithmic access-frequency metric.

    Returns a value in [0, ~1.0]:
    - 0.0 when access_count == 0 (never accessed)
    - approaches 1.0 when accesses >> cycles
    - decays logarithmically as cycles_alive grows without new accesses

    The log(2 + cycles) denominator ensures:
    - At cycles_alive=0: log(2) ≈ 0.693 → recount stays bounded
    - Growth is sub-linear → natural decay even with constant access_count

    The log(1 + access) numerator ensures:
    - At access_count=0: 0.0 (clean zero)
    - Each additional access has diminishing returns
    - But on OLD memories (high denominator), each access has
      proportionally larger impact → asymmetric recovery
    """
    if access_count <= 0:
        return 0.0
    return math.log(1 + access_count) / math.log(2 + cycles_alive)


def compute_composite_strength(
    thalamus_overall: float | None,
    access_count: int,
    cycles_alive: int,
    *,
    w_thalamus: float = W_THALAMUS,
    w_recount: float = W_RECOUNT,
) -> float:
    """Compute the composite strength score for an Engram.

    Args:
        thalamus_overall: Initial Thalamus gate score [0, 1]. None → 0.0.
        access_count: Number of recall hits for this Engram.
        cycles_alive: bank.op_count - engram.created_at_op (session-relative age).
        w_thalamus: Weight for the Thalamus component (default 0.7).
        w_recount: Weight for the recount component (default 0.3).

    Returns:
        Composite strength in [0, ~1.0].
    """
    thalamus = thalamus_overall if thalamus_overall is not None else 0.0
    recount = compute_recount_score(access_count, cycles_alive)
    return w_thalamus * thalamus + w_recount * recount
