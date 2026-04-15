"""
Composite Strength Scoring for Selective Consolidation (Epic 24, revised).

The composite strength score combines a recall-frequency metric with a
saliency boost from emotional valence and surprise to determine whether
a Working Memory Engram should be promoted to the Buffer.

Formula:
    saliency      = max(emotional_valence, surprise)
    recall_score  = log(1 + access_count) / log(2 + cycles_alive)
    composite     = recall_score + SALIENCY_WEIGHT * saliency

Two hard gates must be passed before the score is evaluated:
    1. access_count >= MIN_ACCESS_FOR_PROMOTE  (rehearsal required)
    2. novelty     >= MIN_NOVELTY_FOR_PROMOTE   (only new info consolidates)

The promote threshold is mode-dependent (MODE_PROMOTE_THRESHOLDS),
keyed on the session_mode at Engram creation time.

Bio mapping:
- Recall score    = rehearsal-dependent capture (repeated reactivation)
- Saliency boost  = amygdala modulation (emotional) + noradrenaline (surprise)
- Novelty gate    = CA1 mismatch detection — known info is not re-consolidated
- Logarithmic decay = natural forgetting curve in hippocampus
- Min-access gate = STC requirement: tagging alone is insufficient,
  capture (rehearsal) must also occur

Ref: concept.md ch. 12 (Consolidation Pipeline), STC model.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------

MIN_ACCESS_FOR_PROMOTE: int = 5  # STC: rehearsal required for consolidation
MIN_NOVELTY_FOR_PROMOTE: float = 0.2  # CA1: only novel info consolidates

# ---------------------------------------------------------------------------
# Score parameters
# ---------------------------------------------------------------------------

SALIENCY_WEIGHT: float = 0.3  # max bonus = 0.3 (at saliency=1.0)

# ---------------------------------------------------------------------------
# Mode-dependent promote thresholds
# ---------------------------------------------------------------------------

MODE_PROMOTE_THRESHOLDS: dict[str, float] = {
    "precision": 0.8,
    "validation": 0.7,
    "analogy": 0.6,
    "exploration": 0.5,
}
DEFAULT_PROMOTE_THRESHOLD: float = 0.7  # fallback when mode is unknown

# ---------------------------------------------------------------------------
# Archive threshold (unchanged from Epic 24)
# ---------------------------------------------------------------------------

ARCHIVE_THRESHOLD_WM: float = 0.08
ARCHIVE_THRESHOLD_BUFFER: float = 0.05


def sessions_alive(bank_session_count: int, engram_created_at_session: int) -> int:
    """Return the number of sessions a given Engram has been alive for.

    Epic 24 Story 01 — new aging metric that replaces cycles_alive. A session is
    the natural unit of work; an Engram proves its value by being recalled across
    multiple sessions, not by the raw retain/recall op count.

    Args:
        bank_session_count: Current ``banks.session_count`` value.
        engram_created_at_session: Snapshot of ``session_count`` at Engram creation.

    Returns:
        ``max(0, bank_session_count - engram_created_at_session)``. The guard
        protects against race conditions where a fresh Engram's snapshot could
        briefly appear higher than the bank counter.
    """
    return max(0, bank_session_count - engram_created_at_session)


def compute_recount_score(access_count: int, cycles_alive: int) -> float:
    """Logarithmic access-frequency metric.

    Returns a value in [0, ~1.0+]:
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
    emotional_valence: float | None,
    surprise: float | None,
    access_count: int,
    cycles_alive: int,
    *,
    saliency_weight: float = SALIENCY_WEIGHT,
) -> float:
    """Compute the composite strength score for an Engram.

    The score is primarily driven by recall frequency (access_count),
    with a saliency boost from emotional_valence and surprise.

    Args:
        emotional_valence: Amygdala modulation score [0, 1]. None → 0.0.
        surprise: Noradrenaline surprise score [0, 1]. None → 0.0.
        access_count: Number of recall hits for this Engram.
        cycles_alive: bank.op_count - engram.created_at_op (session-relative age).
        saliency_weight: Weight for the saliency boost (default 0.3).

    Returns:
        Composite strength score clamped to [0.0, 10.0].
    """
    ev = emotional_valence if emotional_valence is not None else 0.0
    sur = surprise if surprise is not None else 0.0
    saliency = max(ev, sur)
    recall = compute_recount_score(access_count, cycles_alive)
    raw = recall + saliency_weight * saliency
    return min(10.0, raw)  # clamp to prevent inf/overflow in edge cases


def get_promote_threshold(mode: str | None) -> float:
    """Return the promote threshold for a given session mode.

    Args:
        mode: Session mode at Engram creation time. None → DEFAULT.

    Returns:
        Threshold value the composite score must reach for promotion.
    """
    if mode is None:
        return DEFAULT_PROMOTE_THRESHOLD
    return MODE_PROMOTE_THRESHOLDS.get(mode, DEFAULT_PROMOTE_THRESHOLD)
