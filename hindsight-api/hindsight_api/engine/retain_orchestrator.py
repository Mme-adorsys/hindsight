"""
RetainOrchestrator — Fact ingestion pipeline.

Extracted from MemoryEngine (God Object decomposition).
All methods delegate shared infrastructure to EngineContext.

Covers:
  - retain() sync wrapper
  - retain_async() single-item convenience wrapper
  - retain_batch_async() main batched ingestion path (Thalamus gate, chunking, orchestration)
  - _retain_batch_async_internal() backpressure-protected sub-batch worker
  - _find_duplicate_facts_batch() semantic dedup
  - _handle_access_count_update() / _handle_batch_retain() background task handlers

Bio mapping:
- Thalamus gate → relevance filtering before hippocampal encoding
- Duplicate detection → pattern separation (dentate gyrus)
- Fact extraction + embedding → LTP early phase (fragile pre-Engram)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np

from .response_models import TokenUsage
from .retain.deduplication import DuplicateResult
from .retain.types import RetainContentDict
from .utils import acquire_with_retry, fq_table

if TYPE_CHECKING:
    from hindsight_api.models import RequestContext

    from .engine_context import EngineContext
    from .response_models import Session

logger = logging.getLogger(__name__)


class RetainOrchestrator:
    """Ingestion pipeline methods for the memory engine."""

    # Maximum character count per sub-batch (~150k tokens at ~4 chars/token)
    CHARS_PER_BATCH = 600_000

    def __init__(self, ctx: "EngineContext") -> None:
        self._ctx = ctx
        # Set externally by lifespan after pool + Qdrant clients are ready (Epic 04)
        self.engram_storage = None
        # Lazy-init once engram_storage is available
        self._thalamus = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retain(
        self,
        bank_id: str,
        content: str,
        context: str = "",
        event_date: datetime | None = None,
        request_context: "RequestContext | None" = None,
    ) -> list[str]:
        """Synchronous wrapper around retain_async()."""
        from hindsight_api.models import RequestContext as RC

        ctx = request_context if request_context is not None else RC()
        return asyncio.run(self.retain_async(bank_id, content, context, event_date, request_context=ctx))

    async def retain_async(
        self,
        bank_id: str,
        content: str,
        context: str = "",
        event_date: datetime | None = None,
        document_id: str | None = None,
        fact_type_override: str | None = None,
        confidence_score: float | None = None,
        *,
        request_context: "RequestContext",
    ) -> list[str]:
        """Single-item retain (delegates to retain_batch_async)."""
        content_dict: RetainContentDict = {"content": content, "context": context}  # type: ignore[typeddict-item]
        if event_date:
            content_dict["event_date"] = event_date
        if document_id:
            content_dict["document_id"] = document_id
        result = await self.retain_batch_async(
            bank_id=bank_id,
            contents=[content_dict],
            request_context=request_context,
            fact_type_override=fact_type_override,
            confidence_score=confidence_score,
        )
        return result[0] if result else []

    async def retain_batch_async(
        self,
        bank_id: str,
        contents: list[RetainContentDict],
        *,
        session: "Session | None" = None,
        request_context: "RequestContext",
        document_id: str | None = None,
        fact_type_override: str | None = None,
        confidence_score: float | None = None,
        return_usage: bool = False,
        budget=None,
    ):
        """
        Store multiple content items as memory units in ONE batch operation.

        - Extracts facts from all contents in parallel
        - Generates ALL embeddings in ONE batch
        - Does ALL database operations in ONE transaction
        - Automatically chunks large batches to prevent timeouts
        """
        start_time = time.time()

        if not contents:
            if return_usage:
                return [], TokenUsage()
            return []

        await self._ctx.authenticate_tenant(request_context)

        contents_copy = [dict(c) for c in contents]
        if self._ctx.operation_validator:
            from hindsight_api.extensions import RetainContext

            ctx = RetainContext(
                bank_id=bank_id,
                contents=contents_copy,
                request_context=request_context,
                document_id=document_id,
                fact_type_override=fact_type_override,
                confidence_score=confidence_score,
            )
            await self._ctx.validate_operation(self._ctx.operation_validator.validate_retain(ctx))

        if document_id:
            for item in contents:
                if "document_id" not in item:
                    item["document_id"] = document_id

        # --- Thalamus Filter Gate ---
        original_count = len(contents)
        passed_indices: list[int] | None = None

        if self.engram_storage is not None:
            if self._thalamus is None:
                from .thalamus import ThalamusFilter

                self._thalamus = ThalamusFilter(
                    qdrant=self.engram_storage._qdrant,
                    embeddings=self._ctx.embeddings,
                )

            from .response_models import Session as _Session

            effective_session = session or _Session.default()
            mode_config = self._ctx.resolve_session_config(effective_session)
            logger.debug(
                "Retain mode_config: mode=%s strength_pre_filter=%.2f reconsolidation=%s bank=%s",
                effective_session.mode.value,
                mode_config.strength_pre_filter,
                mode_config.reconsolidation_level,
                bank_id,
            )
            from .thalamus import ThalamusFilter as _TF

            threshold = _TF.threshold_for_mode(effective_session.mode)
            passed_contents: list[RetainContentDict] = []
            passed_indices = []
            dropped = 0
            total_score = 0.0

            for i, item in enumerate(contents):
                scores = await self._thalamus.score(
                    content=item.get("content", ""),
                    session=effective_session,
                    bank_id=bank_id,
                    context=item.get("context"),
                    expectation=item.get("expectation"),
                    outcome=item.get("outcome"),
                )
                total_score += scores.overall
                if scores.overall < threshold:
                    dropped += 1
                    logger.info(
                        "Thalamus: dropped content (score=%.3f, threshold=%.3f, bank=%s)",
                        scores.overall,
                        threshold,
                        bank_id,
                    )
                else:
                    enriched = dict(item)
                    enriched["thalamus_scores"] = scores
                    passed_contents.append(enriched)
                    passed_indices.append(i)

            logger.info(
                "Thalamus: passed=%d dropped=%d avg_score=%.3f bank=%s mode=%s",
                len(passed_contents),
                dropped,
                total_score / original_count,
                bank_id,
                effective_session.mode,
            )

            if not passed_contents:
                if return_usage:
                    return [[] for _ in range(original_count)], TokenUsage()
                return [[] for _ in range(original_count)]

            contents = passed_contents

        # Auto-chunk large batches
        total_chars = sum(len(item.get("content", "")) for item in contents)
        total_usage = TokenUsage()
        CHARS_PER_BATCH = self.CHARS_PER_BATCH

        if total_chars > CHARS_PER_BATCH:
            logger.info(
                f"Large batch detected ({total_chars:,} chars from {len(contents)} items). "
                f"Splitting into sub-batches of ~{CHARS_PER_BATCH:,} chars each..."
            )
            sub_batches: list[list[RetainContentDict]] = []
            current_batch: list[RetainContentDict] = []
            current_batch_chars = 0

            for item in contents:
                item_chars = len(item.get("content", ""))
                if current_batch and current_batch_chars + item_chars > CHARS_PER_BATCH:
                    sub_batches.append(current_batch)
                    current_batch = [item]
                    current_batch_chars = item_chars
                else:
                    current_batch.append(item)
                    current_batch_chars += item_chars

            if current_batch:
                sub_batches.append(current_batch)

            logger.info(f"Split into {len(sub_batches)} sub-batches: {[len(b) for b in sub_batches]} items each")

            all_results: list[list[str]] = []
            for i, sub_batch in enumerate(sub_batches, 1):
                sub_batch_chars = sum(len(item.get("content", "")) for item in sub_batch)
                logger.info(
                    f"Processing sub-batch {i}/{len(sub_batches)}: {len(sub_batch)} items, {sub_batch_chars:,} chars"
                )
                sub_results, sub_usage = await self._retain_batch_async_internal(
                    bank_id=bank_id,
                    contents=sub_batch,
                    document_id=document_id,
                    is_first_batch=i == 1,
                    fact_type_override=fact_type_override,
                    confidence_score=confidence_score,
                    session=session,
                    budget=budget,
                )
                all_results.extend(sub_results)
                total_usage = total_usage + sub_usage

            total_time = time.time() - start_time
            logger.info(
                f"RETAIN_BATCH_ASYNC (chunked) COMPLETE: {len(all_results)} results from {len(contents)} contents in {total_time:.3f}s"
            )
            result = all_results
        else:
            result, total_usage = await self._retain_batch_async_internal(
                bank_id=bank_id,
                contents=contents,
                document_id=document_id,
                is_first_batch=True,
                fact_type_override=fact_type_override,
                confidence_score=confidence_score,
                session=session,
                budget=budget,
            )

        # Restore full result shape after Thalamus filtering
        if passed_indices is not None and len(passed_indices) < original_count:
            full_result: list[list[str]] = [[] for _ in range(original_count)]
            for filtered_idx, orig_idx in enumerate(passed_indices):
                if filtered_idx < len(result):
                    full_result[orig_idx] = result[filtered_idx]
            result = full_result

        if self._ctx.operation_validator:
            from hindsight_api.extensions import RetainResult

            result_ctx = RetainResult(
                bank_id=bank_id,
                contents=contents_copy,
                request_context=request_context,
                document_id=document_id,
                fact_type_override=fact_type_override,
                confidence_score=confidence_score,
                unit_ids=result,
                success=True,
                error=None,
            )
            try:
                await self._ctx.operation_validator.on_retain_complete(result_ctx)
            except Exception as e:
                logger.warning(f"Post-retain hook error (non-fatal): {e}")

        if return_usage:
            return result, total_usage
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _retain_batch_async_internal(
        self,
        bank_id: str,
        contents: list[RetainContentDict],
        document_id: str | None = None,
        is_first_batch: bool = True,
        fact_type_override: str | None = None,
        confidence_score: float | None = None,
        session: "Session | None" = None,
        budget=None,
    ) -> tuple[list[list[str]], "TokenUsage"]:
        """Sub-batch worker: backpressure-protected, no chunking logic."""
        async with self._ctx.put_semaphore:
            from .retain import orchestrator

            pool = await self._ctx.get_pool()
            return await orchestrator.retain_batch(
                pool=pool,
                embeddings_model=self._ctx.embeddings,
                llm_registry=self._ctx.llm_registry,
                entity_resolver=self._ctx.entity_resolver,
                task_backend=self._ctx.task_backend,
                format_date_fn=self._format_readable_date,
                duplicate_checker_fn=self._find_duplicate_facts_batch,
                bank_id=bank_id,
                contents_dicts=contents,
                document_id=document_id,
                is_first_batch=is_first_batch,
                fact_type_override=fact_type_override,
                confidence_score=confidence_score,
                session=session,
                budget=budget,
            )

    async def _find_duplicate_facts_batch(
        self,
        conn,
        bank_id: str,
        texts: list[str],
        embeddings: list[list[float]],
        event_date: datetime,
        time_window_hours: int = 24,
        similarity_threshold: float = 0.95,
    ) -> list[DuplicateResult]:
        """Check which facts are duplicates using semantic similarity + temporal window."""
        if not texts:
            return []

        try:
            time_lower = event_date - timedelta(hours=time_window_hours)
        except OverflowError:
            time_lower = datetime.min
        try:
            time_upper = event_date + timedelta(hours=time_window_hours)
        except OverflowError:
            time_upper = datetime.max

        existing_facts = await conn.fetch(
            f"""
            SELECT mu.id, mu.text, mu.embedding,
                   ed.thalamus_overall, ed.strength
            FROM {fq_table("memory_units")} mu
            LEFT JOIN {fq_table("engram_dictionary")} ed ON ed.engram_id = mu.id
            WHERE mu.bank_id = $1
              AND mu.event_date BETWEEN $2 AND $3
            """,
            bank_id,
            time_lower,
            time_upper,
        )

        if not existing_facts:
            return [DuplicateResult(is_duplicate=False)] * len(texts)

        import json

        embedding_arrays = []
        for row in existing_facts:
            raw_emb = row["embedding"]
            if isinstance(raw_emb, str):
                emb = np.array(json.loads(raw_emb), dtype=np.float32)
            elif isinstance(raw_emb, (list, tuple)):
                emb = np.array(raw_emb, dtype=np.float32)
            else:
                emb = np.array(raw_emb, dtype=np.float32)
            embedding_arrays.append(emb)

        if not embedding_arrays:
            existing_embeddings = np.array([])
        elif len(embedding_arrays) == 1:
            existing_embeddings = embedding_arrays[0].reshape(1, -1)
        else:
            existing_embeddings = np.vstack(embedding_arrays)

        results: list[DuplicateResult] = []
        for embedding in embeddings:
            emb_array = np.array(embedding)
            similarities = np.dot(existing_embeddings, emb_array)
            max_sim = float(np.max(similarities)) if len(similarities) > 0 else 0.0
            if max_sim > similarity_threshold:
                best_idx = int(np.argmax(similarities))
                row = existing_facts[best_idx]
                results.append(
                    DuplicateResult(
                        is_duplicate=True,
                        existing_unit_id=str(row["id"]),
                        existing_score=row["thalamus_overall"],
                        existing_strength=row["strength"],
                        similarity=max_sim,
                    )
                )
            else:
                results.append(DuplicateResult(is_duplicate=False, similarity=max_sim))

        return results

    @staticmethod
    def _format_readable_date(dt: datetime) -> str:
        """Format a datetime into a readable string for temporal matching."""
        return f"{dt.strftime('%B')} {dt.strftime('%Y')}"

    # ------------------------------------------------------------------
    # Background task handlers
    # ------------------------------------------------------------------

    async def _handle_access_count_update(self, task_dict: dict[str, Any]) -> None:
        """Increment access_count for a list of unit IDs."""
        import uuid

        node_ids = task_dict.get("node_ids", [])
        if not node_ids:
            return
        pool = await self._ctx.get_pool()
        uuid_list = [uuid.UUID(nid) for nid in node_ids]
        async with acquire_with_retry(pool) as conn:
            await conn.execute(
                f"UPDATE {fq_table('memory_units')} SET access_count = access_count + 1 WHERE id = ANY($1::uuid[])",
                uuid_list,
            )

    async def _handle_batch_retain(self, task_dict: dict[str, Any]) -> None:
        """Execute a background batch retain task."""
        bank_id = task_dict.get("bank_id")
        if not bank_id:
            raise ValueError("bank_id is required for batch retain task")
        contents = task_dict.get("contents", [])
        logger.info(
            f"[BATCH_RETAIN_TASK] Starting background batch retain for bank_id={bank_id}, {len(contents)} items"
        )
        from hindsight_api.models import RequestContext

        internal_context = RequestContext()
        await self.retain_batch_async(bank_id=bank_id, contents=contents, request_context=internal_context)
        logger.info(f"[BATCH_RETAIN_TASK] Completed background batch retain for bank_id={bank_id}")
