"""
ReflectOrchestrator — Deep analysis, reconsolidation, and opinion management.

Extracted from MemoryEngine (God Object decomposition).
All methods delegate shared infrastructure to EngineContext.
recall_async is injected to avoid circular imports.

Covers:
  - reflect_async() — multi-step LLM reasoning over retrieved memories
  - _extract_and_store_opinions_async() — background opinion extraction
  - Opinion evaluation and reinforcement
  - Engram reconsolidation (RF1+RF3+RF4 pipeline)
  - Background task handlers for opinions and reconsolidation

Bio mapping:
- reflect_async → PFC semantic integration over hippocampal recall
- Reconsolidation → post-retrieval lability window: LTP/LTD of reconsolidated synapses
- Opinion reinforcement → hebbian strengthening of belief nodes
- PE-flagged prioritisation → dopaminergic error signal gating plasticity
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..metrics import get_metrics_collector
from .response_models import OpinionEvaluation, ReflectResult
from .retain import bank_utils
from .search import think_utils
from .utils import (
    _DISPOSITION_DESCRIPTIONS,
    Budget,
    JsonSchemaWrapper,
    _ReconsolidationEval,
    acquire_with_retry,
    fq_table,
)

if TYPE_CHECKING:
    from hindsight_api.models import RequestContext

    from .engine_context import EngineContext
    from .recall_orchestrator import RecallOrchestrator
    from .response_models import Session
    from .retain_orchestrator import RetainOrchestrator

logger = logging.getLogger(__name__)


class ReflectOrchestrator:
    def __init__(self, ctx: "EngineContext", recall: "RecallOrchestrator", retain: "RetainOrchestrator") -> None:
        self._ctx = ctx
        self._recall = recall
        self._retain = retain

    async def _evaluate_opinion_update_async(
        self,
        opinion_text: str,
        opinion_confidence: float,
        new_event_text: str,
        entity_name: str,
    ) -> dict[str, Any] | None:
        """
        Evaluate if an opinion should be updated based on a new event.

        Args:
            opinion_text: Current opinion text (includes reasons)
            opinion_confidence: Current confidence score (0.0-1.0)
            new_event_text: Text of the new event
            entity_name: Name of the entity this opinion is about

        Returns:
            Dict with 'action' ('keep'|'update'), 'new_confidence', 'new_text' (if action=='update')
            or None if no changes needed
        """

        evaluation_prompt = f"""You are evaluating whether an existing opinion should be updated based on new information.

ENTITY: {entity_name}

EXISTING OPINION:
{opinion_text}
Current confidence: {opinion_confidence:.2f}

NEW EVENT:
{new_event_text}

Evaluate whether this new event:
1. REINFORCES the opinion (increase confidence, keep text)
2. WEAKENS the opinion (decrease confidence, keep text)
3. CHANGES the opinion (update both text and confidence, noting "Previously I thought X, but now Y...")
4. IRRELEVANT (keep everything as is)

Guidelines:
- Only suggest 'update' action if the new event genuinely contradicts or significantly modifies the opinion
- If updating the text, acknowledge the previous opinion and explain the change
- Confidence should reflect accumulated evidence (0.0 = no confidence, 1.0 = very confident)
- Small changes in confidence are normal; large jumps should be rare"""

        try:
            result = await self._ctx.llm_registry.get_llm("reflect", "opinion_extraction").call(
                messages=[
                    {"role": "system", "content": "You evaluate and update opinions based on new information."},
                    {"role": "user", "content": evaluation_prompt},
                ],
                response_format=OpinionEvaluation,
                scope="memory_evaluate_opinion",
                temperature=0.3,  # Lower temperature for more consistent evaluation
            )

            # Only return updates if something actually changed
            if result.action == "keep" and abs(result.new_confidence - opinion_confidence) < 0.01:
                return None

            return {
                "action": result.action,
                "reasoning": result.reasoning,
                "new_confidence": result.new_confidence,
                "new_text": result.new_opinion_text if result.action == "update" else None,
            }

        except Exception as e:
            logger.warning(f"Failed to evaluate opinion update: {str(e)}")
            return None

    async def _handle_form_opinion(self, task_dict: dict[str, Any]):
        """
        Handler for form opinion tasks.

        Args:
            task_dict: Dict with keys: 'bank_id', 'answer_text', 'query', 'tenant_id'
        """
        bank_id = task_dict["bank_id"]
        answer_text = task_dict["answer_text"]
        query = task_dict["query"]
        tenant_id = task_dict.get("tenant_id")

        await self._extract_and_store_opinions_async(
            bank_id=bank_id, answer_text=answer_text, query=query, tenant_id=tenant_id
        )

    async def _handle_reconsolidate_engrams(self, task_dict: dict[str, Any]) -> None:
        """
        Handler for reconsolidate_engrams background tasks (RF1+RF3 — Epic 10).

        Args:
            task_dict: Dict with keys:
                'bank_id'               — bank to reconsolidate
                'reconsolidation_level' — 'minimal'|'moderate'|'schema_update'|'aggressive'
                'prediction_error_ids'  — list[str] of Engram IDs flagged by PE (Epic 11)
                'query'                 — original reflect query (RF3: Qdrant semantic trigger)
                'tenant_id'             — optional tenant for auth
        """
        bank_id = task_dict["bank_id"]
        reconsolidation_level = task_dict.get("reconsolidation_level", "moderate")
        prediction_error_ids: list[str] = task_dict.get("prediction_error_ids", [])
        query: str = task_dict.get("query", "")

        await self._reconsolidate_engrams_async(
            bank_id=bank_id,
            reconsolidation_level=reconsolidation_level,
            prediction_error_ids=prediction_error_ids,
            query=query,
        )

    async def _reconsolidate_engrams_async(
        self,
        bank_id: str,
        reconsolidation_level: str,
        prediction_error_ids: list[str],
        query: str = "",
    ) -> None:
        """
        RF1+RF3: Priority-based reconsolidation triggered by semantic similarity (Qdrant).

        Steps:
        1. Generate query embedding → Qdrant similarity search (RF3: semantic trigger)
        2. Entity-match fallback (RF2): union of Qdrant + entity-match candidates
        3. Fetch FullEngram objects for candidates only (not all active Engrams)
        4. Build priority queue: PE-flagged → weak (strength < 0.3) → rest by last_accessed
        5. For each Engram in top-N: evaluate via LLM → CONFIRMED/MODIFIED/CONTRADICTION
        6. Apply strength delta and persist to engram_dictionary

        Bio mapping:
        - Qdrant trigger (≥0.6 cosine) → hippocampal pattern completion: similar activation
                                          reopens the lability window for reconsolidation
        - Entity fallback              → direct associative recall (always available)
        - Weak Engrams first           → BCM rule: plasticity highest at low-weight synapses
        - PE Engrams prioritised       → Dopaminergic error signal gates LTP/LTD
        - Budget cap                   → Energy cost of offline reconsolidation is bounded

        Concept reference: docs/engram/concept.md — Chapter 10 (RF1, RF2, RF3)
        """
        from .engram_dictionary import filter_entries, update_strength
        from .reflect.prediction_error_registry import PredictionErrorRegistry
        from .reflect.reconsolidation_queue import (
            ReconsolidationOutcome,
            apply_strength_delta,
            build_reconsolidation_queue,
        )
        from .reflect.semantic_trigger import (
            find_entity_match_candidates,
            find_reconsolidation_candidates,
            merge_candidates,
        )
        from .response_models import EngramMetadata, FullEngram
        from .session.session_manager import RetrievalMode

        try:
            pool = await self._ctx.get_pool()

            # Step 1: Semantic trigger — Qdrant similarity search (RF3)
            qdrant_candidates = []
            if query:
                try:
                    from .retain import embedding_utils
                    from .search.engram_retrieval import EngramRetriever
                    from .search.retrieval import get_default_graph_retriever

                    query_embedding = embedding_utils.generate_embedding(self._ctx.embeddings, query)
                    _retriever = get_default_graph_retriever()
                    _qdrant = _retriever.qdrant_client if isinstance(_retriever, EngramRetriever) else None
                    if _qdrant is not None:
                        qdrant_candidates = await find_reconsolidation_candidates(
                            qdrant_client=_qdrant,
                            query_embedding=query_embedding,
                            bank_id=bank_id,
                        )
                except Exception as e:
                    logger.warning("[RECON] Semantic trigger failed, continuing: %s", e)

            # Step 2: Entity-match fallback (RF2) — extract potential entity names from query.
            # Heuristic: capitalized tokens after the first word are likely proper nouns / entity names.
            # Bio mapping: associative recall via named-entity binding (always available, no embedding needed).
            _entity_names: list[str] = []
            if query:
                _tokens = query.split()
                _entity_names = [t.rstrip("?.,;!") for t in _tokens[1:] if t and t[0].isupper()]
            entity_candidates = await find_entity_match_candidates(
                pool=pool,
                bank_id=bank_id,
                entity_names=_entity_names,
                fq_table_fn=fq_table,
            )
            merged = merge_candidates(qdrant_candidates, entity_candidates)

            # Step 3: Fetch FullEngram objects — candidates if Qdrant found matches, else all active
            if merged:
                candidate_ids = {c.engram_id for c in merged}
                rows = await filter_entries(pool, bank_id=bank_id, status="active", limit=500)
                rows = [r for r in rows if str(r.get("engram_id", "")) in candidate_ids]
            else:
                # No semantic candidates → skip reconsolidation.
                # Without a semantic trigger there is no lability window to open — processing
                # arbitrary Engrams would be speculative and wasteful.
                # Bio mapping: no partial-cue activation → no reconsolidation window.
                logger.debug("[RECON] No semantic candidates for bank %s — skipping reconsolidation.", bank_id)
                return

            if not rows:
                return

            # Build FullEngram objects from dict rows
            engrams: list[FullEngram] = []
            for row in rows:
                meta = EngramMetadata(
                    engram_id=row["engram_id"],
                    bank_id=bank_id,
                    strength=float(row.get("strength", 0.0)),
                    last_accessed=row.get("last_accessed"),
                )
                engrams.append(FullEngram(engram_id=row["engram_id"], metadata=meta))

            # Step 2: Build priority queue with PE registry
            pe_registry = PredictionErrorRegistry()
            for eid in prediction_error_ids:
                pe_registry.flag_prediction_error(eid, "prediction_error_from_task")

            # Build ModeConfig with the given reconsolidation_level (base from PRECISION profile)
            from .session.mode_config import MODE_PROFILES

            base_config = MODE_PROFILES[RetrievalMode.PRECISION].with_overrides(
                reconsolidation_level=reconsolidation_level
            )
            queue = build_reconsolidation_queue(engrams, pe_registry, base_config)

            if not queue:
                return

            logger.debug(
                "[RECON] bank=%s level=%s queue_size=%d",
                bank_id,
                reconsolidation_level,
                len(queue),
            )

            # RF4 (T4): Load bank disposition → DispositionProfile for modulated strength updates
            from .reflect.disposition_profile import NEUTRAL as _NEUTRAL_DISPOSITION
            from .reflect.disposition_profile import get_disposition_profile
            from .retain.bank_utils import get_bank_profile

            try:
                bank_profile = await get_bank_profile(pool, bank_id)
                disposition = get_disposition_profile(bank_profile["disposition"])
            except Exception as e:
                logger.warning("[RECON] Could not load disposition for bank %s, using neutral: %s", bank_id, e)
                disposition = _NEUTRAL_DISPOSITION

            # Build similarity score lookup for prompt context (RF3+RF4)
            similarity_lookup: dict[str, float] = {c.engram_id: c.similarity_score for c in merged}

            # Step 3 + 4: LLM evaluation → modulated strength update (RF4)
            from .reflect.disposition_profile import apply_modulated_strength_delta
            from .reflect.reconsolidation_queue import STRENGTH_DELTA, ReconsolidationOutcome

            for engram in queue:
                eid = str(engram.engram_id)
                current_strength = engram.metadata.strength if engram.metadata else 0.0
                sim_score = similarity_lookup.get(eid, 0.0)

                try:
                    outcome = await self._evaluate_engram_reconsolidation_async(
                        eid,
                        bank_id,
                        new_context=query,
                        similarity_score=sim_score,
                        disposition_name=disposition.name,
                    )
                    if outcome is None:
                        continue

                    # RF4: modulate delta by disposition (evidence_weight, update_bias, contradiction_tolerance)

                    raw_delta = STRENGTH_DELTA[outcome]
                    new_strength = apply_modulated_strength_delta(
                        current_strength=current_strength,
                        raw_delta=raw_delta,
                        outcome_is_confirmed=(outcome == ReconsolidationOutcome.CONFIRMED),
                        similarity_score=sim_score,
                        disposition=disposition,
                    )
                    if abs(new_strength - current_strength) > 0.001:
                        await update_strength(pool, eid, new_strength)
                        logger.debug(
                            "[RECON] engram=%s outcome=%s disposition=%s strength %.3f → %.3f",
                            eid,
                            outcome.value,
                            disposition.name,
                            current_strength,
                            new_strength,
                        )
                except Exception as e:
                    logger.warning("[RECON] Failed to reconsolidate engram %s: %s", eid, e)

        except Exception as e:
            logger.warning("[RECON] Reconsolidation failed for bank %s: %s", bank_id, e)

    async def _evaluate_engram_reconsolidation_async(
        self,
        engram_id: str,
        bank_id: str,
        new_context: str = "",
        similarity_score: float = 0.0,
        disposition_name: str = "neutral",
    ) -> "ReconsolidationOutcome | None":
        """
        Evaluate whether an Engram should be confirmed, modified, or marked as contradiction.

        Fetches the Engram text from memory_units and asks the LLM to assess its validity
        given the new context that triggered reconsolidation (RF4 — T4).

        Args:
            engram_id:        Engram to evaluate.
            bank_id:          Bank scope (used for DB fetch).
            new_context:      The query/content that triggered reconsolidation via semantic
                              similarity — gives the LLM the "new information" lens.
            similarity_score: Cosine similarity that triggered this reconsolidation (0.0 if
                              entity-match fallback). Included in the prompt so the LLM
                              can calibrate how closely related the new context is.
            disposition_name: Agent personality for prompt framing — one of
                              'analytical', 'optimistic', 'conservative', 'neutral'.

        Returns None if evaluation is skipped (LLM unavailable or Engram has no text).

        Bio mapping: post-retrieval lability window — new_context acts as the "new information"
        that can trigger LTP (CONFIRMED) or LTD (CONTRADICTION) at the reconsolidated synapse.
        """
        from uuid import UUID

        from .reflect.reconsolidation_queue import ReconsolidationOutcome

        if self._ctx.reflect_llm_config is None:
            return None

        try:
            pool = await self._ctx.get_pool()
            async with acquire_with_retry(pool) as conn:
                row = await conn.fetchrow(
                    f"SELECT text, confidence_score FROM {fq_table('memory_units')} WHERE id = $1",
                    UUID(engram_id),
                )
            if row is None or not row["text"]:
                return None

            engram_text = row["text"]
            confidence = float(row["confidence_score"] or 0.5)

            context_section = ""
            if new_context:
                context_section = f"\nNEW CONTEXT (similarity={similarity_score:.2f}): {new_context}\n"

            disposition_desc = _DISPOSITION_DESCRIPTIONS.get(disposition_name, _DISPOSITION_DESCRIPTIONS["neutral"])
            disposition_section = f"\nAGENT PERSPECTIVE: This agent tends to {disposition_desc}. Apply this perspective when evaluating conflicting evidence.\n"

            prompt = f"""Evaluate and update this stored memory given the new context provided.

STORED MEMORY: {engram_text}
CURRENT CONFIDENCE: {confidence:.2f}{context_section}{disposition_section}
Assess whether this memory is:
1. CONFIRMED — still accurate and relevant given the new context
2. MODIFIED — partially outdated or needs updating in light of the new context
3. CONTRADICTION — conflicts with the new context or is clearly wrong

Respond with one of: confirmed, modified, contradiction"""

            result = await self._ctx.llm_registry.get_llm("reflect", "reconsolidation").call(
                messages=[
                    {
                        "role": "system",
                        "content": "You evaluate stored memory entries for validity given new information.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=_ReconsolidationEval,
                scope="memory_reconsolidate",
                temperature=0.2,
            )

            outcome_map = {
                "confirmed": ReconsolidationOutcome.CONFIRMED,
                "modified": ReconsolidationOutcome.MODIFIED,
                "contradiction": ReconsolidationOutcome.CONTRADICTION,
            }
            return outcome_map.get(result.outcome.lower(), ReconsolidationOutcome.CONFIRMED)

        except Exception as e:
            logger.warning("[RECON] LLM eval failed for engram %s: %s", engram_id, e)
            return None

    async def _handle_reinforce_opinion(self, task_dict: dict[str, Any]):
        """
        Handler for reinforce opinion tasks.

        Args:
            task_dict: Dict with keys: 'bank_id', 'created_unit_ids', 'unit_texts', 'unit_entities'
        """
        bank_id = task_dict["bank_id"]
        created_unit_ids = task_dict["created_unit_ids"]
        unit_texts = task_dict["unit_texts"]
        unit_entities = task_dict["unit_entities"]

        await self._reinforce_opinions_async(
            bank_id=bank_id, created_unit_ids=created_unit_ids, unit_texts=unit_texts, unit_entities=unit_entities
        )

    async def _reinforce_opinions_async(
        self,
        bank_id: str,
        created_unit_ids: list[str],
        unit_texts: list[str],
        unit_entities: list[list[dict[str, str]]],
    ):
        """
        Background task to reinforce opinions based on newly ingested events.

        This runs asynchronously and does not block the put operation.

        Args:
            bank_id: bank ID
            created_unit_ids: List of newly created memory unit IDs
            unit_texts: Texts of the newly created units
            unit_entities: Entities extracted from each unit
        """
        try:
            # Extract all unique entity names from the new units
            entity_names = set()
            for entities_list in unit_entities:
                for entity in entities_list:
                    # Handle both Entity objects and dicts
                    if hasattr(entity, "text"):
                        entity_names.add(entity.text)
                    elif isinstance(entity, dict):
                        entity_names.add(entity["text"])

            if not entity_names:
                return

            pool = await self._ctx.get_pool()
            async with acquire_with_retry(pool) as conn:
                # Find all opinions related to these entities
                opinions = await conn.fetch(
                    f"""
                    SELECT DISTINCT mu.id, mu.text, mu.confidence_score, e.canonical_name
                    FROM {fq_table("memory_units")} mu
                    JOIN {fq_table("unit_entities")} ue ON mu.id = ue.unit_id
                    JOIN {fq_table("entities")} e ON ue.entity_id = e.id
                    WHERE mu.bank_id = $1
                      AND mu.fact_type = 'opinion'
                      AND e.canonical_name = ANY($2::text[])
                    """,
                    bank_id,
                    list(entity_names),
                )

                if not opinions:
                    return

                # Use cached LLM config
                if self._ctx.reflect_llm_config is None:
                    logger.error("[REINFORCE] LLM config not available, skipping opinion reinforcement")
                    return

                # Evaluate each opinion against the new events
                updates_to_apply = []
                for opinion in opinions:
                    opinion_id = str(opinion["id"])
                    opinion_text = opinion["text"]
                    opinion_confidence = opinion["confidence_score"]
                    entity_name = opinion["canonical_name"]

                    # Find all new events mentioning this entity
                    relevant_events = []
                    for unit_text, entities_list in zip(unit_texts, unit_entities):
                        if any(e["text"] == entity_name for e in entities_list):
                            relevant_events.append(unit_text)

                    if not relevant_events:
                        continue

                    # Combine all relevant events
                    combined_events = "\n".join(relevant_events)

                    # Evaluate if opinion should be updated
                    evaluation = await self._evaluate_opinion_update_async(
                        opinion_text, opinion_confidence, combined_events, entity_name
                    )

                    if evaluation:
                        updates_to_apply.append({"opinion_id": opinion_id, "evaluation": evaluation})

                # Apply all updates in a single transaction
                if updates_to_apply:
                    async with conn.transaction():
                        for update in updates_to_apply:
                            opinion_id = update["opinion_id"]
                            evaluation = update["evaluation"]

                            if evaluation["action"] == "update" and evaluation["new_text"]:
                                # Update both text and confidence
                                await conn.execute(
                                    f"""
                                    UPDATE {fq_table("memory_units")}
                                    SET text = $1, confidence_score = $2, updated_at = NOW()
                                    WHERE id = $3
                                    """,
                                    evaluation["new_text"],
                                    evaluation["new_confidence"],
                                    uuid.UUID(opinion_id),
                                )
                            else:
                                # Only update confidence
                                await conn.execute(
                                    f"""
                                    UPDATE {fq_table("memory_units")}
                                    SET confidence_score = $1, updated_at = NOW()
                                    WHERE id = $2
                                    """,
                                    evaluation["new_confidence"],
                                    uuid.UUID(opinion_id),
                                )

                else:
                    pass  # No opinions to update

        except Exception as e:
            logger.error(f"[REINFORCE] Error during opinion reinforcement: {str(e)}")
            import traceback

            traceback.print_exc()

    async def reflect_async(
        self,
        bank_id: str,
        query: str,
        *,
        budget: Budget | None = None,
        context: str | None = None,
        max_tokens: int = 4096,
        response_schema: dict | None = None,
        session: "Session | None" = None,
        request_context: "RequestContext",
    ) -> ReflectResult:
        """
        Reflect and formulate an answer using bank identity, world facts, and opinions.

        This method:
        1. Retrieves experience (conversations and events)
        2. Retrieves world facts (general knowledge)
        3. Retrieves existing opinions (bank's formed perspectives)
        4. Uses LLM to formulate an answer
        5. Extracts and stores any new opinions formed during reflection
        6. Optionally generates structured output based on response_schema
        7. Returns plain text answer and the facts used

        Args:
            bank_id: bank identifier
            query: Question to answer
            budget: Budget level for memory exploration (low=100, mid=300, high=600 units)
            context: Additional context string to include in LLM prompt (not used in recall)
            response_schema: Optional JSON Schema for structured output

        Returns:
            ReflectResult containing:
                - text: Plain text answer (no markdown)
                - based_on: Dict with 'world', 'experience', and 'opinion' fact lists (MemoryFact objects)
                - new_opinions: List of newly formed opinions
                - structured_output: Optional dict if response_schema was provided
        """
        # Use cached LLM config
        if self._ctx.reflect_llm_config is None:
            raise ValueError("Memory LLM API key not set. Set HINDSIGHT_API_LLM_API_KEY environment variable.")

        # Authenticate tenant and set schema in context (for fq_table())
        await self._ctx.authenticate_tenant(request_context)

        # Resolve ModeConfig for session-aware reflect (Epic 06 wire-through; used in Epic 10)
        mode_config = self._ctx.resolve_session_config(session)
        logger.debug(
            "Reflect mode_config: mode=%s reconsolidation=%s construction=%s bank=%s",
            session.mode.value if session else "precision",
            mode_config.reconsolidation_level,
            mode_config.construction_style,
            bank_id,
        )

        # Validate operation if validator is configured
        if self._ctx.operation_validator:
            from hindsight_api.extensions import ReflectContext

            ctx = ReflectContext(
                bank_id=bank_id,
                query=query,
                request_context=request_context,
                budget=budget,
                context=context,
            )
            await self._ctx.validate_operation(self._ctx.operation_validator.validate_reflect(ctx))

        reflect_start = time.time()
        reflect_id = f"{bank_id[:8]}-{int(time.time() * 1000) % 100000}"
        log_buffer = []
        log_buffer.append(f"[REFLECT {reflect_id}] Query: '{query[:50]}...'")

        # Steps 1-3: Run multi-fact-type search (12-way retrieval: 4 methods × 3 fact types)
        recall_start = time.time()
        metrics = get_metrics_collector()
        with metrics.record_operation(
            "recall", bank_id=bank_id, source="reflect", budget=budget.value if budget else None
        ):
            search_result = await self._recall.recall_async(
                bank_id=bank_id,
                query=query,
                budget=budget,
                max_tokens=4096,
                enable_trace=False,
                fact_type=["experience", "world", "opinion"],
                include_entities=True,
                request_context=request_context,
            )
        recall_time = time.time() - recall_start

        all_results = search_result.results

        # Split results by fact type for structured response
        agent_results = [r for r in all_results if r.fact_type == "experience"]
        world_results = [r for r in all_results if r.fact_type == "world"]
        opinion_results = [r for r in all_results if r.fact_type == "opinion"]

        log_buffer.append(
            f"[REFLECT {reflect_id}] Recall: {len(all_results)} facts (experience={len(agent_results)}, world={len(world_results)}, opinion={len(opinion_results)}) in {recall_time:.3f}s"
        )

        # Format facts for LLM
        agent_facts_text = think_utils.format_facts_for_prompt(agent_results)
        world_facts_text = think_utils.format_facts_for_prompt(world_results)
        opinion_facts_text = think_utils.format_facts_for_prompt(opinion_results)

        # Get bank profile (name, disposition + background)
        pool = await self._ctx.get_pool()
        profile_row = await bank_utils.get_bank_profile(pool, bank_id)
        profile = {
            "bank_id": bank_id,
            "name": profile_row["name"],
            "disposition": profile_row["disposition"],
            "background": profile_row["background"],
        }
        name = profile["name"]
        disposition = profile["disposition"]  # Typed as DispositionTraits
        background = profile["background"]

        # Build the prompt
        prompt = think_utils.build_think_prompt(
            agent_facts_text=agent_facts_text,
            world_facts_text=world_facts_text,
            opinion_facts_text=opinion_facts_text,
            query=query,
            name=name,
            disposition=disposition,
            background=background,
            context=context,
        )

        log_buffer.append(f"[REFLECT {reflect_id}] Prompt: {len(prompt)} chars")

        system_message = think_utils.get_system_message(disposition)
        messages = [{"role": "system", "content": system_message}, {"role": "user", "content": prompt}]

        # Prepare response_format if schema provided
        response_format = None
        if response_schema is not None:
            response_format = JsonSchemaWrapper(response_schema)

        llm_start = time.time()
        llm_result, usage = await self._ctx.llm_registry.get_llm("reflect", "think").call(
            messages=messages,
            scope="memory_reflect",
            max_completion_tokens=max_tokens,
            response_format=response_format,
            skip_validation=True if response_format else False,
            # Don't enforce strict_schema - not all providers support it and may retry forever
            # Soft enforcement (schema in prompt + json_object mode) is sufficient
            strict_schema=False,
            return_usage=True,
        )
        llm_time = time.time() - llm_start

        # Handle response based on whether structured output was requested
        if response_schema is not None:
            structured_output = llm_result
            answer_text = ""  # Empty for backward compatibility
            log_buffer.append(f"[REFLECT {reflect_id}] Structured output generated")
        else:
            structured_output = None
            answer_text = llm_result.strip()

        # Submit form_opinion task for background processing
        # Pass tenant_id from request context for internal authentication in background task
        await self._ctx.task_backend.submit_task(
            {
                "type": "form_opinion",
                "bank_id": bank_id,
                "answer_text": answer_text,
                "query": query,
                "tenant_id": getattr(request_context, "tenant_id", None) if request_context else None,
            }
        )

        # RF1+RF3 (Epic 10): Submit reconsolidation task — semantic trigger uses query embedding
        # PE IDs from session's PredictionErrorRegistry (populated by recall_async, Epic 11).
        _pe_ids: list[str] = []
        if session is not None:
            _pe_reg = self._ctx.get_session_manager().get_prediction_error_registry(session.session_id)
            if _pe_reg is not None:
                _pe_ids = list(_pe_reg.get_flagged_ids())
        await self._ctx.task_backend.submit_task(
            {
                "type": "reconsolidate_engrams",
                "bank_id": bank_id,
                "reconsolidation_level": mode_config.reconsolidation_level,
                "prediction_error_ids": _pe_ids,
                "query": query,  # RF3: used to generate embedding for Qdrant semantic trigger
                "tenant_id": getattr(request_context, "tenant_id", None) if request_context else None,
            }
        )

        total_time = time.time() - reflect_start
        log_buffer.append(
            f"[REFLECT {reflect_id}] Complete: {len(answer_text)} chars response, LLM {llm_time:.3f}s, total {total_time:.3f}s"
        )
        logger.info("\n" + "\n".join(log_buffer))

        # Return response with facts split by type
        result = ReflectResult(
            text=answer_text,
            based_on={"world": world_results, "experience": agent_results, "opinion": opinion_results},
            new_opinions=[],  # Opinions are being extracted asynchronously
            structured_output=structured_output,
            usage=usage,
        )

        # Call post-operation hook if validator is configured
        if self._ctx.operation_validator:
            from hindsight_api.extensions.operation_validator import ReflectResultContext

            result_ctx = ReflectResultContext(
                bank_id=bank_id,
                query=query,
                request_context=request_context,
                budget=budget,
                context=context,
                result=result,
                success=True,
                error=None,
            )
            try:
                await self._ctx.operation_validator.on_reflect_complete(result_ctx)
            except Exception as e:
                logger.warning(f"Post-reflect hook error (non-fatal): {e}")

        return result

    async def _extract_and_store_opinions_async(
        self, bank_id: str, answer_text: str, query: str, tenant_id: str | None = None
    ):
        """
        Background task to extract and store opinions from think response.

        This runs asynchronously and does not block the think response.

        Args:
            bank_id: bank IDentifier
            answer_text: The generated answer text
            query: The original query
            tenant_id: Tenant identifier for internal authentication
        """
        try:
            # Extract opinions from the answer
            new_opinions = await think_utils.extract_opinions_from_text(
                self._ctx.llm_registry.get_llm("reflect", "opinion_extraction"), text=answer_text, query=query
            )

            # Store new opinions
            if new_opinions:
                from datetime import datetime

                current_time = datetime.now(UTC)
                # Use internal context with tenant_id for background authentication
                # Extension can check internal=True to bypass normal auth
                from hindsight_api.models import RequestContext

                internal_context = RequestContext(tenant_id=tenant_id, internal=True)
                for opinion in new_opinions:
                    await self._retain.retain_async(
                        bank_id=bank_id,
                        content=opinion.opinion,
                        context=f"formed during thinking about: {query}",
                        event_date=current_time,
                        fact_type_override="opinion",
                        confidence_score=opinion.confidence,
                        request_context=internal_context,
                    )

        except Exception as e:
            logger.warning(f"[REFLECT] Failed to extract/store opinions: {str(e)}")
