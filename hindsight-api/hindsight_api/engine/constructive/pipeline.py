"""
Construction Pipeline — assembles ConstructedAnswer from retrieval results (Epic 11, Story 02).

The pipeline runs after retrieval scoring and transforms raw ScoredResults into a structured
ConstructedAnswer with facts, inferences, and gaps. The Session Mode controls how creative
and how inferential the construction is.

Pipeline steps:
  1. Fact Extraction  — ScoredResult → ConstructedFact (confidence, source, weak_link penalty)
  2. Inference Phase  — LLM: derives conclusions from facts (mode-specific prompt)
  3. Gap Detection    — LLM: identifies missing information
  4. Confidence Agg.  — weighted mean × coverage factor (penalised by gap relevance)
  5. WC Update        — confirmed inferences pushed to WorkingContext.inference_layer

Bio mapping:
- Fact Extraction  → hippocampal pattern-completion: retrieved fragments reconstructed from trace
- Inference Phase  → PFC semantic integration: existing knowledge + context → new hypothesis
- Gap Detection    → CA1 mismatch signal: prediction vs. reality reveals missing information
- Coverage Factor  → hippocampal encoding efficiency: gaps reduce the reconstruction confidence
- WC Update        → short-term potentiation: confirmed hypotheses strengthen working memory

Concept reference: docs/engram/concept.md — Chapter 11 (Constructive Memory)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .models import ConstructedAnswer, ConstructedFact, Gap, Inference

if TYPE_CHECKING:
    from ..llm_wrapper import LLMConfig
    from ..search.types import ScoredResult
    from ..session.mode_config import ModeConfig
    from ..session.working_context import WorkingContext

logger = logging.getLogger(__name__)

# Penalty multiplier applied to confidence when the Engram was reached via weak link traversal
WEAK_LINK_CONFIDENCE_PENALTY: float = 0.85

# Threshold above which an inference is considered "confirmed" and added to WorkingContext
INFERENCE_CONFIRMATION_THRESHOLD: float = 0.7

# Maximum number of inferences stored in WorkingContext.inference_layer (FIFO eviction)
# Bio mapping: working memory capacity — oldest hypotheses decay when limit is reached
INFERENCE_LAYER_MAX: int = 20

# Minimum fact confidence to classify as 'direct' (vs 'reconstructed')
DIRECT_SOURCE_THRESHOLD: float = 0.5

# Mode-specific instruction appended to the inference prompt
_INFERENCE_STYLE: dict[str, str] = {
    "conservative": "Only state what logically follows from the given facts. Do not speculate.",
    "creative": "Include speculative connections and possible implications beyond the explicit facts.",
    "cross_domain": "Draw parallels to other domains and find cross-domain analogies and patterns.",
    "evidence_based": "For each inference, also note potential counter-evidence or contradictions.",
}

# Inference type hint per mode
_INFERENCE_TYPE_HINT: dict[str, str] = {
    "conservative": "deduction",
    "creative": "extrapolation",
    "cross_domain": "analogy",
    "evidence_based": "deduction",
}


# ---------------------------------------------------------------------------
# LLM response schemas
# ---------------------------------------------------------------------------


class _InferenceItem(BaseModel):
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    inference_type: str = "deduction"
    reasoning: str
    supporting_indices: list[int] = Field(default_factory=list)


class _InferenceList(BaseModel):
    inferences: list[_InferenceItem] = Field(default_factory=list)


class _GapItem(BaseModel):
    description: str
    relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    suggested_query: str | None = None
    related_fact_indices: list[int] = Field(default_factory=list)


class _GapList(BaseModel):
    gaps: list[_GapItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ConstructionPipeline
# ---------------------------------------------------------------------------


class ConstructionPipeline:
    """
    Transforms retrieval results into a ConstructedAnswer.

    Designed to be instantiated once and reused across recall calls within the
    same session. The LLM and ModeConfig control the quality and style of construction.

    Args:
        llm:         LLMConfig used for inference and gap detection calls.
        mode_config: Active session ModeConfig — supplies construction_style.
    """

    def __init__(self, llm: LLMConfig, mode_config: ModeConfig) -> None:
        self._llm = llm
        self._mode_config = mode_config

    async def construct(
        self,
        scored_results: list[ScoredResult],
        query: str,
        working_context: WorkingContext | None = None,
    ) -> ConstructedAnswer:
        """
        Build a ConstructedAnswer from retrieval results.

        Args:
            scored_results:  Post-scoring retrieval results (may be empty).
            query:           Original user query — provides context for LLM steps.
            working_context: Active WorkingContext — provides inference_layer context
                             and receives confirmed inferences (T6).

        Returns:
            ConstructedAnswer with facts, inferences, gaps, and overall_confidence.
        """
        start = time.monotonic()
        style = self._mode_config.construction_style

        # T2 — Fact Extraction
        facts = self._extract_facts(scored_results)

        inferences: list[Inference] = []
        gaps: list[Gap] = []

        if facts and self._llm is not None:
            # T3 — Inference Phase (LLM)
            try:
                inferences = await self._run_inference_phase(facts, query, working_context, style)
            except Exception as e:
                logger.warning("[CONSTRUCTION] Inference phase failed: %s", e)

            # T4 — Gap Detection Phase (LLM)
            try:
                gaps = await self._run_gap_detection(facts, inferences, query)
            except Exception as e:
                logger.warning("[CONSTRUCTION] Gap detection failed: %s", e)

        # T5 — Confidence Aggregation
        overall_confidence = self._aggregate_confidence(facts, gaps)

        # T6 — Working Context Update
        if working_context is not None:
            self._update_working_context(inferences, facts, working_context)

        elapsed = time.monotonic() - start
        logger.debug(
            "[CONSTRUCTION] style=%s facts=%d inferences=%d gaps=%d confidence=%.3f elapsed=%.3fs",
            style,
            len(facts),
            len(inferences),
            len(gaps),
            overall_confidence,
            elapsed,
        )

        return ConstructedAnswer(
            facts=facts,
            inferences=inferences,
            gaps=gaps,
            overall_confidence=overall_confidence,
            mode_influence=style,
            construction_metadata={
                "elapsed_s": round(elapsed, 4),
                "style": style,
                "fact_count": len(facts),
                "inference_count": len(inferences),
                "gap_count": len(gaps),
            },
        )

    # ------------------------------------------------------------------
    # T2 — Fact Extraction
    # ------------------------------------------------------------------

    def _extract_facts(self, scored_results: list[ScoredResult]) -> list[ConstructedFact]:
        """Convert ScoredResults to ConstructedFacts."""
        facts: list[ConstructedFact] = []
        for sr in scored_results:
            confidence = float(sr.combined_score)

            # Weak-link traversal reduces confidence (Engram reached via STC/CO_ACTIVATED link)
            if sr.retrieval.traversal_source == "weak_link":
                confidence *= WEAK_LINK_CONFIDENCE_PENALTY
            confidence = max(0.0, min(1.0, confidence))

            source: str = "direct" if confidence >= DIRECT_SOURCE_THRESHOLD else "reconstructed"

            thalamus: dict[str, float] = {}
            if hasattr(sr, "thalamus_score") and sr.thalamus_score is not None:
                thalamus["thalamus_composite"] = float(sr.thalamus_score)

            facts.append(
                ConstructedFact(
                    engram_id=sr.id,
                    content=sr.retrieval.text or "",
                    confidence=confidence,
                    source=source,
                    thalamus_scores=thalamus,
                )
            )
        return facts

    # ------------------------------------------------------------------
    # T3 — Inference Phase
    # ------------------------------------------------------------------

    async def _run_inference_phase(
        self,
        facts: list[ConstructedFact],
        query: str,
        working_context: WorkingContext | None,
        style: str,
    ) -> list[Inference]:
        """LLM-based inference generation (medium-tier task)."""
        facts_text = "\n".join(f"[{i}] {f.content}" for i, f in enumerate(facts))
        wc_context = ""
        if working_context and working_context.confirmed_inferences:
            existing = "; ".join(inf.content for inf in working_context.confirmed_inferences[:3])
            wc_context = f"\nExisting working-context hypotheses: {existing}"

        style_instruction = _INFERENCE_STYLE.get(style, _INFERENCE_STYLE["conservative"])
        type_hint = _INFERENCE_TYPE_HINT.get(style, "deduction")

        prompt = f"""Given these facts retrieved for the query "{query}":
{facts_text}{wc_context}

What can be inferred? {style_instruction}

For each inference, specify:
- content: the inferred statement
- confidence: 0.0-1.0
- inference_type: one of deduction/analogy/interpolation/extrapolation (prefer '{type_hint}')
- reasoning: brief explanation
- supporting_indices: which fact indices [0..{len(facts) - 1}] support this inference"""

        result: _InferenceList = await self._llm.call(
            messages=[
                {
                    "role": "system",
                    "content": "You are a memory inference engine. Extract logical conclusions from retrieved facts.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format=_InferenceList,
            scope="memory_construction_inference",
            temperature=0.3,
        )

        inferences: list[Inference] = []
        for item in result.inferences:
            # Map supporting_indices → engram_ids
            supporting_ids = [facts[i].engram_id for i in item.supporting_indices if 0 <= i < len(facts)]
            # Validate inference_type
            valid_types = {"deduction", "analogy", "interpolation", "extrapolation"}
            inf_type = item.inference_type if item.inference_type in valid_types else type_hint

            inferences.append(
                Inference(
                    content=item.content,
                    confidence=max(0.0, min(1.0, item.confidence)),
                    supporting_engram_ids=supporting_ids,
                    inference_type=inf_type,
                    reasoning=item.reasoning,
                )
            )
        return inferences

    # ------------------------------------------------------------------
    # T4 — Gap Detection Phase
    # ------------------------------------------------------------------

    async def _run_gap_detection(
        self,
        facts: list[ConstructedFact],
        inferences: list[Inference],
        query: str,
    ) -> list[Gap]:
        """LLM-based knowledge gap identification (small-tier task)."""
        facts_summary = "; ".join(f.content[:80] for f in facts[:5])
        inferences_summary = "; ".join(i.content[:60] for i in inferences[:3])

        prompt = f"""Query: "{query}"
Known facts: {facts_summary}
Derived inferences: {inferences_summary}

What important information is MISSING to fully answer the query?
For each gap specify:
- description: what is missing
- relevance: 0.0-1.0 (how important is this for answering the query)
- suggested_query: a follow-up search query to fill this gap (or null)
- related_fact_indices: which known facts [0..{len(facts) - 1}] are adjacent to this gap"""

        result: _GapList = await self._llm.call(
            messages=[
                {"role": "system", "content": "You identify knowledge gaps in retrieved memory results."},
                {"role": "user", "content": prompt},
            ],
            response_format=_GapList,
            scope="memory_construction_gaps",
            temperature=0.2,
        )

        gaps: list[Gap] = []
        for item in result.gaps:
            related_ids = [facts[i].engram_id for i in item.related_fact_indices if 0 <= i < len(facts)]
            gaps.append(
                Gap(
                    description=item.description,
                    relevance=max(0.0, min(1.0, item.relevance)),
                    related_engram_ids=related_ids,
                    suggested_query=item.suggested_query,
                )
            )
        return gaps

    # ------------------------------------------------------------------
    # T5 — Confidence Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_confidence(facts: list[ConstructedFact], gaps: list[Gap]) -> float:
        """
        overall_confidence = weighted_mean(fact_confidences) * coverage_factor

        coverage_factor penalises significant gaps: each gap with relevance ≥ 0.3
        reduces coverage by gap.relevance * 0.15 (capped at 50% total reduction).
        """
        if not facts:
            return 0.0

        weighted_mean = sum(f.confidence for f in facts) / len(facts)

        # Coverage factor: 1.0 = no gaps, decreases with significant gaps
        gap_penalty = sum(g.relevance * 0.15 for g in gaps if g.relevance >= 0.3)
        coverage_factor = max(0.5, 1.0 - gap_penalty)

        return max(0.0, min(1.0, weighted_mean * coverage_factor))

    # ------------------------------------------------------------------
    # T6 — Working Context Update
    # ------------------------------------------------------------------

    def _update_working_context(
        self,
        inferences: list[Inference],
        facts: list[ConstructedFact],
        working_context: WorkingContext,
    ) -> None:
        """
        Push confirmed inferences (confidence ≥ threshold) to WorkingContext.confirmed_inferences.

        WorkingContext.Inference is a dataclass distinct from constructive.models.Inference.
        We bridge the two types here. High-confidence inferences are confirmed immediately
        and stored in the persistent confirmed_inferences list (not the transient SessionCache).
        """
        from datetime import datetime, timezone
        from uuid import uuid4

        from ..session.working_context import Inference as WCInference

        for inf in inferences:
            if inf.confidence >= INFERENCE_CONFIRMATION_THRESHOLD:
                # Enforce FIFO cap: evict oldest inference when layer is full.
                # Bio mapping: working memory capacity limit — oldest hypotheses decay.
                if len(working_context.confirmed_inferences) >= INFERENCE_LAYER_MAX:
                    working_context.confirmed_inferences.pop(0)
                wc_inf = WCInference(
                    id=str(uuid4()),
                    content=inf.content,
                    confidence=inf.confidence,
                    supporting_engram_ids=inf.supporting_engram_ids,
                    created_at=datetime.now(tz=timezone.utc),
                    status="confirmed",
                )
                working_context.confirmed_inferences.append(wc_inf)

        logger.debug(
            "[CONSTRUCTION] WC update: %d inferences pushed to confirmed_inferences",
            sum(1 for i in inferences if i.confidence >= INFERENCE_CONFIRMATION_THRESHOLD),
        )
