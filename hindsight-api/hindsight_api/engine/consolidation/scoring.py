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
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engram_types import ThalamusScores

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

# ---------------------------------------------------------------------------
# Epic 24 Story 02 — Equilibrium Rate r (individual per-Engram)
# ---------------------------------------------------------------------------
#
# r = r_base(mode) × demand / protection × bank_factor
#
#   demand      = 1 + DEMAND_ALPHA × task_relevance           (strictness)
#   protection  = 1 + PROTECTION_BETA × mean(novelty,
#                                              surprise,
#                                              emotional_valence)  (leniency)
#   bank_factor = log(1 + REFERENCE_BANK_SIZE) / log(1 + bank_size)
#
# r is the per-Engram expectation "how many recalls per session until I am
# stable?". Task-relevant Engrams demand higher activation, novel / surprising
# / emotional Engrams get more protection, and tiny banks are compensated for
# their inflated per-Engram recall probability. Values stay > 0 by construction
# — the explicit max(0.001, r) guard protects against pathological inputs.
#
# Concept reference: concept.md ch. 5.3, engram-lifecycle-scoring.md ch. 3.

ENV_R_BASE_PRECISION = "HINDSIGHT_API_R_BASE_PRECISION"
ENV_R_BASE_VALIDATION = "HINDSIGHT_API_R_BASE_VALIDATION"
ENV_R_BASE_ANALOGY = "HINDSIGHT_API_R_BASE_ANALOGY"
ENV_R_BASE_EXPLORATION = "HINDSIGHT_API_R_BASE_EXPLORATION"

DEFAULT_R_BASE_PRECISION: float = 0.8
DEFAULT_R_BASE_VALIDATION: float = 0.6
DEFAULT_R_BASE_ANALOGY: float = 0.4
DEFAULT_R_BASE_EXPLORATION: float = 0.3
DEFAULT_R_BASE: float = 0.5  # fallback for unknown modes

MODE_R_BASE: dict[str, float] = {
    "precision": float(os.getenv(ENV_R_BASE_PRECISION, str(DEFAULT_R_BASE_PRECISION))),
    "validation": float(os.getenv(ENV_R_BASE_VALIDATION, str(DEFAULT_R_BASE_VALIDATION))),
    "analogy": float(os.getenv(ENV_R_BASE_ANALOGY, str(DEFAULT_R_BASE_ANALOGY))),
    "exploration": float(os.getenv(ENV_R_BASE_EXPLORATION, str(DEFAULT_R_BASE_EXPLORATION))),
}

DEMAND_ALPHA: float = 0.5
PROTECTION_BETA: float = 0.5
REFERENCE_BANK_SIZE: int = 1000
MIN_EQUILIBRIUM_RATE: float = 0.001  # lower guard against pathological input

# ---------------------------------------------------------------------------
# Epic 24 Story 04 — Tag-based promote thresholds + normalized hard gates
# ---------------------------------------------------------------------------
#
# Promote thresholds become tag-aware rather than mode-aware: facts are
# cheap routine material and need a strict bar, experiences and opinions are
# already-processed knowledge and get promoted earlier. The hard access gate
# is normalized by bank size because access_count inflates in small banks
# (every Engram is recalled more often on average) and deflates in large
# ones (competition).
#
# The old MODE_PROMOTE_THRESHOLDS / MIN_ACCESS_FOR_PROMOTE / get_promote_threshold
# stay in place until Story 06 migrates the callers and deletes them.
#
# Concept reference: concept.md §5.3.

TAG_PROMOTE_THRESHOLDS: dict[str, float] = {
    "fact": 0.7,
    "experience": 0.4,
    "opinion": 0.4,
}
DEFAULT_TAG_PROMOTE_THRESHOLD: float = 0.7  # conservative fallback (like fact)
BASE_MIN_ACCESS_FOR_PROMOTE: int = 5  # bank-size 1000 anchor


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


def compute_bank_factor(bank_size: int, reference_size: int = REFERENCE_BANK_SIZE) -> float:
    """Normalize per-Engram recall probability across banks of different sizes.

    Small banks (bank_size << reference_size) have inflated access_counts
    because every Engram is recalled more often on average. Large banks have
    deflated access_counts due to competition. This factor shifts the
    equilibrium rate to keep the lifecycle fair regardless of bank size.

    Formula: ``log(1 + reference_size) / log(1 + bank_size)``.

    Args:
        bank_size: Current Engram count for the bank.
        reference_size: Neutral anchor where bank_factor == 1.0.
            Defaults to ``REFERENCE_BANK_SIZE``.

    Returns:
        A strictly positive multiplier. An empty / size=0 bank yields 2.0
        (maximum compensation) rather than a division by zero.
    """
    if bank_size < 1:
        return 2.0
    return math.log(1 + reference_size) / math.log(1 + bank_size)


def compute_equilibrium_rate(
    thalamus_scores: ThalamusScores,
    mode: str | None,
    bank_size: int,
) -> float:
    """Per-Engram equilibrium rate driving the decay formula (Epic 24 Story 02).

    The rate is "how many recalls per session do we expect for this Engram to
    stay stable?". Task-relevant Engrams demand more (stricter), novel /
    surprising / emotionally charged Engrams get more protection (more lenient),
    and bank size is normalized to avoid rewarding tiny banks unfairly.

    Formula:
        ``r_base × demand / protection × bank_factor``

    with:
        ``demand     = 1 + DEMAND_ALPHA × task_relevance``
        ``protection = 1 + PROTECTION_BETA × mean(novelty, surprise, valence)``
        ``bank_factor = compute_bank_factor(bank_size)``

    Args:
        thalamus_scores: Birth-value scores recorded by the Thalamus Filter.
            Only the four dimension fields are read.
        mode: Session mode the Engram was created in. ``None`` or an unknown
            value falls back to ``DEFAULT_R_BASE``.
        bank_size: Current Engram count in the bank (for normalization).

    Returns:
        A strictly positive rate, clamped to ``MIN_EQUILIBRIUM_RATE`` (0.001)
        so downstream decay formulas never divide by zero.
    """
    r_base = MODE_R_BASE.get(mode, DEFAULT_R_BASE) if mode is not None else DEFAULT_R_BASE
    demand = 1.0 + DEMAND_ALPHA * thalamus_scores.task_relevance
    avg_protective = (thalamus_scores.novelty + thalamus_scores.surprise + thalamus_scores.emotional_valence) / 3.0
    protection = 1.0 + PROTECTION_BETA * avg_protective
    bank_factor = compute_bank_factor(bank_size)
    r = r_base * demand / protection * bank_factor
    return max(MIN_EQUILIBRIUM_RATE, r)


# ---------------------------------------------------------------------------
# Epic 24 Story 03 — Natural Decay & Composite Score
# ---------------------------------------------------------------------------
#
# The new composite is a product rather than a sum:
#
#     decay     = log(1 + access_count) / log(1 + sessions_alive × r)
#     composite = thalamus_overall × decay
#
# Key property: decay can be > 1.0. When an Engram is recalled more often than
# its equilibrium rate predicts, the composite grows above the thalamus-derived
# birth value — amplification, not just decay. This structurally fixes the
# Epic 12 frequency_bonus issue where the bonus only overcame the 0.9 decay
# once access_count > 12.
#
# The decay itself is NOT clamped — the > 1.0 regime is semantically meaningful.
# Only the final composite gets an [0.0, 10.0] overflow guard matching the old
# compute_composite_strength() contract.
#
# Concept reference: concept.md §5.3, engram-lifecycle-scoring.md ch. 3.

COMPOSITE_MAX: float = 10.0  # composite overflow guard (matches old contract)


def compute_decay(access_count: int, sessions_alive: int, r: float) -> float:
    """Natural decay factor for an Engram (Epic 24 Story 03).

    Measures actual recall frequency against the expected frequency
    ``sessions_alive × r``. Values below 1.0 mean the Engram is being recalled
    less than expected (true decay); values above 1.0 mean it is being recalled
    more than expected (amplification).

    Args:
        access_count: Total recall hits accumulated for this Engram.
        sessions_alive: Number of completed sessions since creation (see
            ``sessions_alive()`` helper).
        r: Per-Engram equilibrium rate from ``compute_equilibrium_rate()``.

    Returns:
        The raw decay factor. Edge cases:

        - ``sessions_alive <= 0`` → 1.0 (fresh Engram, no decay yet — avoids
          ``log(1) = 0`` in the denominator).
        - ``r <= 0`` → 1.0 (defensive guard; ``compute_equilibrium_rate`` already
          clamps to ``MIN_EQUILIBRIUM_RATE``).
        - ``access_count <= 0`` → 0.0 (never accessed → full decay).

        Never clamped above — ``decay > 1.0`` is a valid amplification signal.
    """
    if sessions_alive <= 0:
        return 1.0
    if r <= 0:
        return 1.0
    if access_count <= 0:
        return 0.0
    expected = sessions_alive * r
    return math.log(1 + access_count) / math.log(1 + expected)


def compute_composite(
    thalamus_overall: float,
    access_count: int,
    sessions_alive: int,
    r: float,
) -> float:
    """Composite lifecycle score: ``thalamus_overall × decay`` (Epic 24 Story 03).

    Replaces ``compute_composite_strength`` as the single scoring metric for all
    lifecycle decisions (promote, archive, reactivate). At ``sessions_alive=0``
    the decay is exactly 1.0, so a freshly created Engram's composite equals its
    ``thalamus_overall`` birth value.

    Args:
        thalamus_overall: Birth-value score from the Thalamus Filter (0.0–1.0).
        access_count: Total recall hits accumulated for this Engram.
        sessions_alive: Number of completed sessions since creation.
        r: Per-Engram equilibrium rate from ``compute_equilibrium_rate()``.

    Returns:
        Composite score in ``[0.0, COMPOSITE_MAX]``. The upper clamp only
        protects against pathological inputs — realistic values stay in a much
        narrower band around the thalamus score.
    """
    decay = compute_decay(access_count, sessions_alive, r)
    raw = thalamus_overall * decay
    if raw < 0.0:
        return 0.0
    return min(COMPOSITE_MAX, raw)


def get_promote_threshold_for_tags(tags: list[str] | None) -> float:
    """Tag-based promote threshold (Epic 24 Story 04).

    Iterates over an Engram's tags and picks the *lowest* matching threshold
    — multi-tag Engrams get the benefit of the doubt. Tags outside
    ``TAG_PROMOTE_THRESHOLDS`` are ignored; if no tag matches, the default
    (``DEFAULT_TAG_PROMOTE_THRESHOLD``, equal to the strict ``fact`` value)
    applies.

    Args:
        tags: Engram tag list from ``engram_dictionary.tags``. ``None`` and
            empty lists both collapse to the default threshold.

    Returns:
        The promote threshold the composite score must reach.
    """
    if not tags:
        return DEFAULT_TAG_PROMOTE_THRESHOLD
    matches = [TAG_PROMOTE_THRESHOLDS[t] for t in tags if t in TAG_PROMOTE_THRESHOLDS]
    if not matches:
        return DEFAULT_TAG_PROMOTE_THRESHOLD
    return min(matches)


def compute_min_access(bank_size: int, base: int = BASE_MIN_ACCESS_FOR_PROMOTE) -> int:
    """Bank-size normalized access gate (Epic 24 Story 04).

    Small banks inflate ``access_count`` because every Engram has a higher
    per-session recall probability; large banks deflate it due to
    competition. Multiplying ``base`` by the same ``compute_bank_factor``
    used for the equilibrium rate keeps the access gate fair.

    Args:
        bank_size: Current Engram count for the bank.
        base: Anchor value at ``REFERENCE_BANK_SIZE`` (default 5).

    Returns:
        ``ceil(base × bank_factor(bank_size))``, clamped to a minimum of 1
        so the gate can never collapse to "no access needed".
    """
    raw = base * compute_bank_factor(bank_size)
    return max(1, math.ceil(raw))


def passes_hard_gates(access_count: int, novelty: float, bank_size: int) -> bool:
    """Combined access + novelty gate check (Epic 24 Story 04).

    Both gates must pass before a composite score is even evaluated:

    - ``access_count >= compute_min_access(bank_size)`` (normalized STC gate)
    - ``novelty >= MIN_NOVELTY_FOR_PROMOTE`` (CA1 mismatch gate, unchanged)

    Args:
        access_count: Total recall hits accumulated for this Engram.
        novelty: Thalamus novelty score at Engram creation time.
        bank_size: Current Engram count in the bank.

    Returns:
        ``True`` iff both gates pass.
    """
    if access_count < compute_min_access(bank_size):
        return False
    if novelty < MIN_NOVELTY_FOR_PROMOTE:
        return False
    return True
