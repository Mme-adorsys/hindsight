"""
Prediction Error Detection — Epic 11, Story 03.

Compares a ConstructedAnswer against the session's current_expectation using an LLM
(small-tier). When a significant deviation is found:
  1. The severity determines whether a mode shift is triggered (≥ 0.3 → Validation).
  2. Conflicting fact Engrams are flagged in the PredictionErrorRegistry for priority
     reconsolidation (Epic 10 RF1).
  3. A summary of the error is written into ConstructedAnswer.construction_metadata.

Bio mapping:
- Prediction Error     → Dopaminergic mismatch signal: actual ≠ predicted
- severity             → Error magnitude; drives plasticity update strength
- Mode shift           → Noradrenergic arousal: surprise → heightened scrutiny (Validation)
- Engram flagging      → Synaptic tagging: mis-predicting synapses primed for LTP/LTD
- No expectation       → No prediction → no PE signal (baseline state)

Concept reference: docs/engram/concept.md — Chapter 11 (Prediction Error), Chapter 10 (RF1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..llm_wrapper import LLMConfig
    from .models import ConstructedAnswer

logger = logging.getLogger(__name__)

# Severity thresholds for PE classification
SEVERITY_MINOR_MAX: float = 0.3  # 0.0–0.29 → Minor (no mode shift)
SEVERITY_MODERATE_MAX: float = 0.7  # 0.3–0.69 → Moderate (mode shift)
# 0.7–1.0  → Major   (mode shift + high priority)


# ---------------------------------------------------------------------------
# T2 — PredictionError dataclass
# ---------------------------------------------------------------------------


@dataclass
class PredictionError:
    """
    Represents a detected deviation between ConstructedAnswer and current_expectation.

    Attributes:
        severity:             0.0–1.0 — how large the mismatch is.
                              0.0–0.3 → Minor   (informational, no mode shift)
                              0.3–0.7 → Moderate (triggers Validation mode shift)
                              0.7–1.0 → Major   (triggers shift + high reconsolidation priority)
        description:          Human-readable explanation of the deviation.
        conflicting_fact_ids: Engram IDs whose content conflicts with the expectation.
        expected_summary:     Brief summary of what was expected.
        actual_summary:       Brief summary of what was found.
    """

    severity: float
    description: str
    conflicting_fact_ids: list[str] = field(default_factory=list)
    expected_summary: str = ""
    actual_summary: str = ""

    @property
    def level(self) -> str:
        """Classify severity as 'minor', 'moderate', or 'major'."""
        if self.severity < SEVERITY_MINOR_MAX:
            return "minor"
        if self.severity < SEVERITY_MODERATE_MAX:
            return "moderate"
        return "major"

    @property
    def triggers_mode_shift(self) -> bool:
        """True when severity is significant enough to shift Session Mode."""
        return self.severity >= SEVERITY_MINOR_MAX


# ---------------------------------------------------------------------------
# LLM response schema
# ---------------------------------------------------------------------------


class _PEResponse(BaseModel):
    has_error: bool = False
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    conflicting_fact_indices: list[int] = Field(default_factory=list)
    expected_summary: str = ""
    actual_summary: str = ""


# ---------------------------------------------------------------------------
# T1 — PredictionErrorDetector
# ---------------------------------------------------------------------------


class PredictionErrorDetector:
    """
    Detects prediction errors between a ConstructedAnswer and a session expectation.

    Uses an LLM (small-tier) to semantically compare the answer with the expectation,
    which catches paraphrases and logical contradictions that string matching would miss.

    Args:
        llm: LLMConfig used for the detection call.
    """

    def __init__(self, llm: LLMConfig) -> None:
        self._llm = llm

    async def detect(
        self,
        answer: ConstructedAnswer,
        expectation: str,
    ) -> PredictionError | None:
        """
        Compare the ConstructedAnswer against the expectation.

        Returns a PredictionError when a significant deviation is found,
        or None when the answer is consistent with the expectation.

        Args:
            answer:      The ConstructedAnswer from the Construction Pipeline.
            expectation: The session's current_expectation string.
        """
        if not expectation or not answer.facts:
            return None

        facts_text = "\n".join(f"[{i}] {f.content[:120]}" for i, f in enumerate(answer.facts[:8]))

        prompt = f"""Compare this retrieved answer with the stated expectation.

Expectation: "{expectation}"

Retrieved facts:
{facts_text}

Determine:
1. Is there a significant deviation between the expectation and the retrieved facts?
2. How severe is the mismatch? (0.0 = perfect match, 1.0 = fundamental contradiction)
3. Which fact indices conflict with the expectation?
4. Summarise what was expected vs. what was found (1 sentence each).

Only flag an error if the deviation is meaningful (severity > 0.1). Minor wording
differences or additional details that don't contradict the expectation are NOT errors."""

        result: _PEResponse = await self._llm.call(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a prediction error detector. Identify whether retrieved "
                        "memory facts contradict or significantly deviate from an expectation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=_PEResponse,
            scope="prediction_error_detection",
            temperature=0.1,
        )

        if not result.has_error or result.severity <= 0.1:
            return None

        # Map fact indices → engram IDs
        conflicting_ids = [
            answer.facts[i].engram_id for i in result.conflicting_fact_indices if 0 <= i < len(answer.facts)
        ]

        return PredictionError(
            severity=max(0.0, min(1.0, result.severity)),
            description=result.description,
            conflicting_fact_ids=conflicting_ids,
            expected_summary=result.expected_summary,
            actual_summary=result.actual_summary,
        )


# ---------------------------------------------------------------------------
# T3+T4 — Feedback dispatcher
# ---------------------------------------------------------------------------


async def apply_prediction_error_feedback(
    error: PredictionError,
    session_id: Any,
    session_manager: Any,
    prediction_error_registry: Any,
) -> None:
    """
    Apply prediction error feedback to the session and reconsolidation pipeline.

    T3 — Mode shift: severity ≥ 0.3 → ModeSignal.PREDICTION_ERROR → Validation mode.
    T4 — Engram flagging: conflicting_fact_ids → prediction_error_registry.flag_prediction_error()

    Args:
        error:                      The detected PredictionError.
        session_id:                 UUID of the active session.
        session_manager:            SessionManager instance (for process_signal).
        prediction_error_registry:  PredictionErrorRegistry instance (for flag_prediction_error).
    """
    from ..session.session_manager import ModeSignal

    # T3 — Mode shift
    if error.triggers_mode_shift:
        shifted = await session_manager.process_signal(session_id, ModeSignal.PREDICTION_ERROR)
        logger.debug(
            "[PE] Mode shift triggered (severity=%.2f level=%s shifted=%s)",
            error.severity,
            error.level,
            shifted,
        )

    # T4 — Reconsolidation flagging
    context = (
        f"Prediction error (severity={error.severity:.2f}): {error.description[:200]}"
        f" | expected: {error.expected_summary[:100]}"
        f" | actual: {error.actual_summary[:100]}"
    )
    for engram_id in error.conflicting_fact_ids:
        prediction_error_registry.flag_prediction_error(engram_id, context)

    logger.debug(
        "[PE] Flagged %d engrams in PE registry",
        len(error.conflicting_fact_ids),
    )
