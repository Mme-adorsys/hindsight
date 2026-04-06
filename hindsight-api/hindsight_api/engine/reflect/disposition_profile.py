"""
Disposition-aware Reconsolidation — DispositionProfile mapping (RF4).

Disposition modulates HOW reconsolidation updates Engrams, not WHICH ones are selected
(that is the Priority Queue's job). Different agent personalities handle contradictory
evidence differently:

- Analytical:   Weighs evidence more heavily, low contradiction tolerance — updates fast.
- Optimistic:   Positive bias on confirmed memories, higher contradiction tolerance.
- Conservative: Lower evidence weight, high contradiction tolerance — retains existing knowledge.
- Neutral:      No modulation — plain apply_strength_delta (default).

The DispositionProfile is derived from the bank's DispositionTraits (skepticism /
literalism / empathy, all 1-5) via get_disposition_profile().

Bio mapping:
- evidence_weight       → Dopaminergic gain: higher DA → stronger LTP/LTD at reconsolidated synapses
- update_bias           → Noradrenergic arousal: positive arousal → upward plasticity bias
- contradiction_tolerance → PFC error-gating threshold: if error signal < threshold, LTD is blocked
                           (conservative agent's PFC dampens the dopamine error signal)

Concept reference: docs/engram/concept.md — Chapter 10 (RF4 Disposition-aware Reconsolidation)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..response_models import DispositionTraits

# ---------------------------------------------------------------------------
# DispositionProfile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispositionProfile:
    """
    Modulation parameters for reconsolidation strength updates.

    Attributes:
        name:                   Human-readable label for logging/debugging.
        evidence_weight:        Multiplier applied to the raw strength delta (> 1 = stronger
                                updates, < 1 = weaker updates).
        update_bias:            Additive offset applied only on CONFIRMED outcomes — shifts
                                the effective delta upwards for optimistic agents.
        contradiction_tolerance: Similarity score below which CONTRADICTION outcome is
                                suppressed (strength delta → 0). Agents with high tolerance
                                accept contradictory information without reducing strength.
    """

    name: str
    evidence_weight: float
    update_bias: float
    contradiction_tolerance: float


# ---------------------------------------------------------------------------
# Preset profiles
# ---------------------------------------------------------------------------

NEUTRAL = DispositionProfile(
    name="neutral",
    evidence_weight=1.0,
    update_bias=0.0,
    contradiction_tolerance=0.0,
)
"""No modulation — plain apply_strength_delta behaviour."""

ANALYTICAL = DispositionProfile(
    name="analytical",
    evidence_weight=1.2,
    update_bias=0.0,
    contradiction_tolerance=0.3,
)
"""High-skepticism agent: stronger evidence weighting, fast contradiction updates."""

OPTIMISTIC = DispositionProfile(
    name="optimistic",
    evidence_weight=0.8,
    update_bias=0.2,
    contradiction_tolerance=0.5,
)
"""Low-skepticism / high-empathy agent: softer updates, positive CONFIRMED bias,
tolerates moderate contradiction."""

CONSERVATIVE = DispositionProfile(
    name="conservative",
    evidence_weight=0.8,
    update_bias=0.0,
    contradiction_tolerance=0.7,
)
"""High-literalism agent: reduced evidence weight, high contradiction tolerance —
strongly retains existing knowledge under uncertainty."""

_PROFILE_MAP: dict[str, DispositionProfile] = {
    "neutral": NEUTRAL,
    "analytical": ANALYTICAL,
    "optimistic": OPTIMISTIC,
    "conservative": CONSERVATIVE,
}


def get_disposition_profile(traits: DispositionTraits | None) -> DispositionProfile:
    """
    Derive a DispositionProfile from a bank's DispositionTraits.

    Mapping logic (dominant-trait wins, tie-breaks to NEUTRAL):
      - skepticism ≥ 4                     → ANALYTICAL
      - empathy ≥ 4 (and skepticism < 4)   → OPTIMISTIC
      - literalism ≥ 4 (and others < 4)    → CONSERVATIVE
      - otherwise                          → NEUTRAL

    Args:
        traits: DispositionTraits with skepticism / literalism / empathy (1-5).
                Pass None to get NEUTRAL.

    Returns:
        One of ANALYTICAL, OPTIMISTIC, CONSERVATIVE, or NEUTRAL.
    """
    if traits is None:
        return NEUTRAL

    skepticism = traits.skepticism
    literalism = traits.literalism
    empathy = traits.empathy

    if skepticism >= 4:
        return ANALYTICAL
    if empathy >= 4:
        return OPTIMISTIC
    if literalism >= 4:
        return CONSERVATIVE
    return NEUTRAL


def apply_modulated_strength_delta(
    current_strength: float,
    raw_delta: float,
    outcome_is_confirmed: bool,
    similarity_score: float,
    disposition: DispositionProfile,
) -> float:
    """
    Apply a strength delta modulated by the agent's DispositionProfile.

    Steps:
      1. If CONTRADICTION (raw_delta < 0) and similarity_score < contradiction_tolerance
         → suppress update (return current_strength unchanged).
      2. Multiply raw_delta by evidence_weight.
      3. If CONFIRMED (raw_delta > 0 or outcome_is_confirmed) add update_bias.
      4. Clamp result to [0.0, 1.0].

    Args:
        current_strength:    Engram strength before reconsolidation.
        raw_delta:           Unmodulated strength delta from STRENGTH_DELTA map.
        outcome_is_confirmed: True when outcome == CONFIRMED (for bias application).
        similarity_score:    Cosine similarity that triggered reconsolidation.
        disposition:         Active DispositionProfile.

    Returns:
        New strength clamped to [0.0, 1.0].
    """
    # Contradiction suppression: high-tolerance agents block LTD when evidence is weak
    if raw_delta < 0 and similarity_score < disposition.contradiction_tolerance:
        return current_strength

    modulated = raw_delta * disposition.evidence_weight
    if outcome_is_confirmed:
        modulated += disposition.update_bias

    return max(0.0, min(1.0, current_strength + modulated))
