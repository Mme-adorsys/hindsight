"""
Memory Engine for Memory Banks.

This implements a sophisticated memory architecture that combines:
1. Temporal links: Memories connected by time proximity
2. Semantic links: Memories connected by meaning/similarity
3. Entity links: Memories connected by shared entities (PERSON, ORG, etc.)
4. Spreading activation: Search through the graph with activation decay
5. Dynamic weighting: Recency and frequency-based importance

This file is now a Facade. All logic lives in the orchestrators:
  - engine_context.py      — EngineContext (shared infrastructure)
  - retain_orchestrator.py — RetainOrchestrator
  - recall_orchestrator.py — RecallOrchestrator
  - reflect_orchestrator.py — ReflectOrchestrator
  - admin_operations.py    — AdminOperations
  - entity_operations.py   — EntityOperations
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from ..config import get_config
from ..metrics import get_metrics_collector
from .cross_encoder import CrossEncoderModel
from .embeddings import Embeddings, create_embeddings_from_env
from .interface import MemoryEngineInterface

# Engine utilities — re-exported here so existing code keeps working.
from .utils import (  # noqa: F401 (re-exports)
    _DISPOSITION_DESCRIPTIONS,
    Budget,
    UnqualifiedTableError,
    _current_schema,
    _get_tiktoken_encoding,
    _ReconsolidationEval,
    acquire_with_retry,
    fq_table,
    get_current_schema,
    utcnow,
    validate_sql_schema,
)

if TYPE_CHECKING:
    from hindsight_api.engine.response_models import Session
    from hindsight_api.engine.session.session_manager import SessionManager
    from hindsight_api.extensions import OperationValidatorExtension, TenantExtension
    from hindsight_api.models import RequestContext

from enum import Enum  # noqa: E402

from ..pg0 import EmbeddedPostgres, parse_pg0_url  # noqa: F401
from .llm_wrapper import LLMConfig
from .query_analyzer import QueryAnalyzer
from .response_models import (  # noqa: F401
    VALID_RECALL_FACT_TYPES,
    EntityObservation,
    EntityState,
    MemoryFact,
    ReflectResult,
    TokenUsage,
)
from .response_models import RecallResult as RecallResultModel
from .retain import bank_utils, embedding_utils  # noqa: F401
from .retain.deduplication import DuplicateResult  # noqa: F401
from .retain.types import RetainContentDict
from .search import observation_utils, think_utils  # noqa: F401
from .search.reranking import CrossEncoderReranker  # noqa: F401
from .task_backend import AsyncIOQueueBackend, NoopTaskBackend, TaskBackend

logger = logging.getLogger(__name__)

_get_tiktoken_encoding()  # eager warmup


class MemoryEngine(MemoryEngineInterface):
    """
    Advanced memory system using temporal and semantic linking with PostgreSQL.

    This class is a facade. All logic lives in the orchestrators imported from
    engine_context.py, retain_orchestrator.py, recall_orchestrator.py,
    reflect_orchestrator.py, admin_operations.py, and entity_operations.py.
    """

    # Maximum retry attempts for background task execution before marking failed
    MAX_TASK_RETRIES = 3

    def __init__(
        self,
        db_url=None,
        memory_llm_provider=None,
        memory_llm_api_key=None,
        memory_llm_model=None,
        memory_llm_base_url=None,
        retain_llm_provider=None,
        retain_llm_api_key=None,
        retain_llm_model=None,
        retain_llm_base_url=None,
        reflect_llm_provider=None,
        reflect_llm_api_key=None,
        reflect_llm_model=None,
        reflect_llm_base_url=None,
        embeddings=None,
        cross_encoder=None,
        query_analyzer=None,
        pool_min_size=None,
        pool_max_size=None,
        db_command_timeout=None,
        db_acquire_timeout=None,
        task_backend=None,
        task_batch_size=None,
        task_batch_interval=None,
        run_migrations=True,
        operation_validator=None,
        tenant_extension=None,
        skip_llm_verification=None,
        lazy_reranker=None,
        session_manager=None,
    ):
        from .admin_operations import AdminOperations
        from .engine_context import EngineContext
        from .entity_operations import EntityOperations
        from .recall_orchestrator import RecallOrchestrator
        from .reflect_orchestrator import ReflectOrchestrator
        from .retain_orchestrator import RetainOrchestrator

        self._ctx = EngineContext(
            db_url=db_url,
            memory_llm_provider=memory_llm_provider,
            memory_llm_api_key=memory_llm_api_key,
            memory_llm_model=memory_llm_model,
            memory_llm_base_url=memory_llm_base_url,
            retain_llm_provider=retain_llm_provider,
            retain_llm_api_key=retain_llm_api_key,
            retain_llm_model=retain_llm_model,
            retain_llm_base_url=retain_llm_base_url,
            reflect_llm_provider=reflect_llm_provider,
            reflect_llm_api_key=reflect_llm_api_key,
            reflect_llm_model=reflect_llm_model,
            reflect_llm_base_url=reflect_llm_base_url,
            embeddings=embeddings,
            cross_encoder=cross_encoder,
            query_analyzer=query_analyzer,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
            db_command_timeout=db_command_timeout,
            db_acquire_timeout=db_acquire_timeout,
            task_backend=task_backend,
            task_batch_size=task_batch_size,
            task_batch_interval=task_batch_interval,
            run_migrations=run_migrations,
            operation_validator=operation_validator,
            tenant_extension=tenant_extension,
            skip_llm_verification=skip_llm_verification,
            lazy_reranker=lazy_reranker,
            session_manager=session_manager,
        )

        self._retain = RetainOrchestrator(self._ctx)
        self._recall = RecallOrchestrator(self._ctx)
        self._reflect = ReflectOrchestrator(self._ctx, recall=self._recall, retain=self._retain)
        self._admin = AdminOperations(self._ctx)
        self._entity = EntityOperations(self._ctx)

        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self):
        await self._ctx.initialize(executor=self.execute_task)
        self._initialized = True

    async def close(self):
        await self._ctx.close()
        self._initialized = False

    async def health_check(self) -> dict:
        if not self._initialized:
            return {"status": "unhealthy", "reason": "not_initialized"}
        try:
            pool = await self._ctx.get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                if result == 1:
                    return {"status": "healthy", "database": "connected"}
                return {"status": "unhealthy", "database": "unexpected response"}
        except Exception as e:
            return {"status": "unhealthy", "database": "error", "error": str(e)}

    async def wait_for_background_tasks(self):
        if hasattr(self._ctx._task_backend, "wait_for_pending_tasks"):
            await self._ctx._task_backend.wait_for_pending_tasks()

    # ------------------------------------------------------------------
    # Backward-compat property proxies (accessed by lifespan / external code)
    # ------------------------------------------------------------------

    @property
    def embeddings(self):
        return self._ctx.embeddings

    @embeddings.setter
    def embeddings(self, value):
        self._ctx.embeddings = value

    @property
    def entity_resolver(self):
        return self._ctx.entity_resolver

    @entity_resolver.setter
    def entity_resolver(self, value):
        self._ctx.entity_resolver = value

    @property
    def engram_storage(self):
        return self._retain.engram_storage

    @engram_storage.setter
    def engram_storage(self, value):
        self._retain.engram_storage = value

    @property
    def _thalamus(self):
        return self._retain._thalamus

    @_thalamus.setter
    def _thalamus(self, value):
        self._retain._thalamus = value

    @property
    def db_url(self):
        return self._ctx.db_url

    @db_url.setter
    def db_url(self, value):
        self._ctx.db_url = value

    @property
    def _pg0(self):
        return self._ctx._pg0

    @_pg0.setter
    def _pg0(self, value):
        self._ctx._pg0 = value

    @property
    def _llm_registry(self):
        return self._ctx.llm_registry

    @property
    def _llm_config(self):
        return self._ctx._llm_config

    @property
    def _retain_llm_config(self):
        return self._ctx._retain_llm_config

    @property
    def _reflect_llm_config(self):
        return self._ctx._reflect_llm_config

    @property
    def query_analyzer(self):
        return self._ctx.query_analyzer

    @property
    def _task_backend(self):
        return self._ctx._task_backend

    @property
    def _operation_validator(self):
        return self._ctx._operation_validator

    @_operation_validator.setter
    def _operation_validator(self, value):
        self._ctx._operation_validator = value

    @property
    def _tenant_extension(self):
        return self._ctx._tenant_extension

    @property
    def _cross_encoder_reranker(self):
        return self._ctx._cross_encoder_reranker

    @property
    def _pool(self):
        return self._ctx._pool

    # ------------------------------------------------------------------
    # execute_task — dispatch stays in facade
    # ------------------------------------------------------------------

    async def execute_task(self, task_dict: dict[str, Any]):
        task_type = task_dict.get("type")
        operation_id = task_dict.get("operation_id")
        retry_count = task_dict.get("retry_count", 0)
        max_retries = self.MAX_TASK_RETRIES

        if operation_id:
            try:
                pool = await self._ctx.get_pool()
                async with acquire_with_retry(pool) as conn:
                    result = await conn.fetchrow(
                        f"SELECT operation_id FROM {fq_table('async_operations')} WHERE operation_id = $1",
                        uuid.UUID(operation_id),
                    )
                    if not result:
                        logger.info(f"Skipping cancelled operation: {operation_id}")
                        return
            except Exception as e:
                logger.error(f"Failed to check operation status {operation_id}: {e}")

        try:
            if task_type == "access_count_update":
                await self._retain._handle_access_count_update(task_dict)
            elif task_type == "reinforce_opinion":
                await self._reflect._handle_reinforce_opinion(task_dict)
            elif task_type == "form_opinion":
                await self._reflect._handle_form_opinion(task_dict)
            elif task_type == "reconsolidate_engrams":
                await self._reflect._handle_reconsolidate_engrams(task_dict)
            elif task_type == "batch_retain":
                await self._retain._handle_batch_retain(task_dict)
            elif task_type == "regenerate_observations":
                await self._entity._handle_regenerate_observations(task_dict)
            else:
                logger.error(f"Unknown task type: {task_type}")
                if operation_id:
                    await self._ctx.delete_operation_record(operation_id)
                return

            if operation_id:
                await self._ctx.delete_operation_record(operation_id)

        except Exception as e:
            logger.error(
                f"Task execution failed (attempt {retry_count + 1}/{max_retries + 1}): {task_type}, error: {e}"
            )
            import traceback

            error_traceback = traceback.format_exc()
            traceback.print_exc()

            if retry_count < max_retries:
                task_dict["retry_count"] = retry_count + 1
                logger.info(f"Rescheduling task {task_type} (retry {retry_count + 1}/{max_retries})")
                await self._ctx._task_backend.submit_task(task_dict)
            else:
                logger.error(f"Max retries exceeded for task {task_type}, marking as failed")
                if operation_id:
                    await self._ctx.mark_operation_failed(operation_id, str(e), error_traceback)

    # ------------------------------------------------------------------
    # Private helpers (backward compat — used by tests/lifespan via self)
    # ------------------------------------------------------------------

    async def _authenticate_tenant(self, request_context):
        return await self._ctx.authenticate_tenant(request_context)

    async def _validate_operation(self, validation_coro):
        return await self._ctx.validate_operation(validation_coro)

    def _get_session_manager(self):
        return self._ctx.get_session_manager()

    def _resolve_session_config(self, session):
        return self._ctx.resolve_session_config(session)

    async def _get_pool(self):
        return await self._ctx.get_pool()

    async def _delete_operation_record(self, operation_id):
        return await self._ctx.delete_operation_record(operation_id)

    async def _mark_operation_failed(self, operation_id, error_message, error_traceback):
        return await self._ctx.mark_operation_failed(operation_id, error_message, error_traceback)

    async def _find_duplicate_facts_batch(
        self, conn, bank_id, texts, embeddings, event_date, time_window_hours=24, similarity_threshold=0.95
    ):
        return await self._retain._find_duplicate_facts_batch(
            conn, bank_id, texts, embeddings, event_date, time_window_hours, similarity_threshold
        )

    def _format_readable_date(self, dt):
        return self._retain._format_readable_date(dt)

    # ------------------------------------------------------------------
    # Retain
    # ------------------------------------------------------------------

    def retain(self, bank_id, content, context="", event_date=None, request_context=None):
        return self._retain.retain(bank_id, content, context, event_date, request_context)

    async def retain_async(
        self,
        bank_id,
        content,
        context="",
        event_date=None,
        document_id=None,
        fact_type_override=None,
        confidence_score=None,
        *,
        request_context,
    ):
        return await self._retain.retain_async(
            bank_id,
            content,
            context,
            event_date,
            document_id,
            fact_type_override,
            confidence_score,
            request_context=request_context,
        )

    async def retain_batch_async(
        self,
        bank_id,
        contents,
        *,
        session=None,
        request_context,
        document_id=None,
        fact_type_override=None,
        confidence_score=None,
        return_usage=False,
        budget=None,
    ):
        return await self._retain.retain_batch_async(
            bank_id,
            contents,
            session=session,
            request_context=request_context,
            document_id=document_id,
            fact_type_override=fact_type_override,
            confidence_score=confidence_score,
            return_usage=return_usage,
            budget=budget,
        )

    async def _retain_batch_async_internal(
        self,
        bank_id,
        contents,
        document_id=None,
        is_first_batch=True,
        fact_type_override=None,
        confidence_score=None,
        session=None,
    ):
        return await self._retain._retain_batch_async_internal(
            bank_id, contents, document_id, is_first_batch, fact_type_override, confidence_score, session
        )

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    def recall(self, bank_id, query, fact_type, budget=Budget.MID, max_tokens=4096, enable_trace=False):
        return self._recall.recall(bank_id, query, fact_type, budget, max_tokens, enable_trace)

    async def recall_async(
        self,
        bank_id,
        query,
        *,
        budget=None,
        max_tokens=4096,
        enable_trace=False,
        fact_type=None,
        question_date=None,
        include_entities=False,
        max_entity_tokens=500,
        include_chunks=False,
        max_chunk_tokens=8192,
        session=None,
        request_context,
        tags=None,
        shared_bank_id=None,
        expectation=None,
    ):
        return await self._recall.recall_async(
            bank_id,
            query,
            budget=budget,
            max_tokens=max_tokens,
            enable_trace=enable_trace,
            fact_type=fact_type,
            question_date=question_date,
            include_entities=include_entities,
            max_entity_tokens=max_entity_tokens,
            include_chunks=include_chunks,
            max_chunk_tokens=max_chunk_tokens,
            session=session,
            request_context=request_context,
            tags=tags,
            shared_bank_id=shared_bank_id,
            expectation=expectation,
        )

    def _filter_by_token_budget(self, results, max_tokens):
        return self._recall._filter_by_token_budget(results, max_tokens)

    async def _search_with_retries(
        self,
        bank_id,
        query,
        fact_type,
        thinking_budget,
        max_tokens,
        enable_trace,
        question_date=None,
        include_entities=False,
        max_entity_tokens=500,
        include_chunks=False,
        max_chunk_tokens=8192,
        request_context=None,
        tags=None,
        mode=None,
        shared_bank_id=None,
    ):
        return await self._recall._search_with_retries(
            bank_id,
            query,
            fact_type,
            thinking_budget,
            max_tokens,
            enable_trace,
            question_date,
            include_entities,
            max_entity_tokens,
            include_chunks,
            max_chunk_tokens,
            request_context,
            tags=tags,
            mode=mode,
            shared_bank_id=shared_bank_id,
        )

    # ------------------------------------------------------------------
    # Reflect
    # ------------------------------------------------------------------

    async def reflect_async(
        self,
        bank_id,
        query,
        *,
        budget=None,
        context=None,
        max_tokens=4096,
        response_schema=None,
        session=None,
        request_context,
    ):
        return await self._reflect.reflect_async(
            bank_id,
            query,
            budget=budget,
            context=context,
            max_tokens=max_tokens,
            response_schema=response_schema,
            session=session,
            request_context=request_context,
        )

    async def _evaluate_opinion_update_async(self, opinion_text, opinion_confidence, new_event_text, entity_name):
        return await self._reflect._evaluate_opinion_update_async(
            opinion_text, opinion_confidence, new_event_text, entity_name
        )

    async def _handle_form_opinion(self, task_dict):
        return await self._reflect._handle_form_opinion(task_dict)

    async def _handle_reconsolidate_engrams(self, task_dict):
        return await self._reflect._handle_reconsolidate_engrams(task_dict)

    async def _reconsolidate_engrams_async(self, bank_id, reconsolidation_level, prediction_error_ids, query=""):
        return await self._reflect._reconsolidate_engrams_async(
            bank_id, reconsolidation_level, prediction_error_ids, query
        )

    async def _evaluate_engram_reconsolidation_async(
        self, engram_id, bank_id, new_context="", similarity_score=0.0, disposition_name="neutral"
    ):
        return await self._reflect._evaluate_engram_reconsolidation_async(
            engram_id, bank_id, new_context, similarity_score, disposition_name
        )

    async def _handle_reinforce_opinion(self, task_dict):
        return await self._reflect._handle_reinforce_opinion(task_dict)

    async def _reinforce_opinions_async(self, bank_id, created_unit_ids, unit_texts, unit_entities):
        return await self._reflect._reinforce_opinions_async(bank_id, created_unit_ids, unit_texts, unit_entities)

    async def _extract_and_store_opinions_async(self, bank_id, answer_text, query, tenant_id=None):
        return await self._reflect._extract_and_store_opinions_async(bank_id, answer_text, query, tenant_id)

    # ------------------------------------------------------------------
    # Admin operations
    # ------------------------------------------------------------------

    async def get_document(self, document_id, bank_id, *, request_context):
        return await self._admin.get_document(document_id, bank_id, request_context=request_context)

    async def delete_document(self, document_id, bank_id, *, request_context):
        return await self._admin.delete_document(document_id, bank_id, request_context=request_context)

    async def delete_memory_unit(self, unit_id, *, request_context):
        return await self._admin.delete_memory_unit(unit_id, request_context=request_context)

    async def delete_bank(self, bank_id, fact_type=None, *, request_context):
        return await self._admin.delete_bank(bank_id, fact_type, request_context=request_context)

    async def get_graph_data(self, bank_id=None, fact_type=None, *, limit=1000, request_context):
        return await self._admin.get_graph_data(bank_id, fact_type, limit=limit, request_context=request_context)

    async def list_memory_units(
        self, bank_id, *, fact_type=None, search_query=None, limit=100, offset=0, request_context
    ):
        return await self._admin.list_memory_units(
            bank_id,
            fact_type=fact_type,
            search_query=search_query,
            limit=limit,
            offset=offset,
            request_context=request_context,
        )

    async def list_documents(self, bank_id, *, search_query=None, limit=100, offset=0, request_context):
        return await self._admin.list_documents(
            bank_id,
            search_query=search_query,
            limit=limit,
            offset=offset,
            request_context=request_context,
        )

    async def get_chunk(self, chunk_id, *, request_context):
        return await self._admin.get_chunk(chunk_id, request_context=request_context)

    async def get_bank_profile(self, bank_id, *, request_context):
        return await self._admin.get_bank_profile(bank_id, request_context=request_context)

    async def update_bank_disposition(self, bank_id, disposition, *, request_context):
        return await self._admin.update_bank_disposition(bank_id, disposition, request_context=request_context)

    async def merge_bank_background(self, bank_id, new_info, *, update_disposition=True, request_context):
        return await self._admin.merge_bank_background(
            bank_id, new_info, update_disposition=update_disposition, request_context=request_context
        )

    async def list_banks(self, *, request_context):
        return await self._admin.list_banks(request_context=request_context)

    async def update_bank(self, bank_id, *, name=None, background=None, request_context):
        return await self._admin.update_bank(bank_id, name=name, background=background, request_context=request_context)

    async def get_bank_stats(self, bank_id, *, request_context):
        return await self._admin.get_bank_stats(bank_id, request_context=request_context)

    async def list_operations(self, bank_id, *, request_context):
        return await self._admin.list_operations(bank_id, request_context=request_context)

    async def cancel_operation(self, bank_id, operation_id, *, request_context):
        return await self._admin.cancel_operation(bank_id, operation_id, request_context=request_context)

    async def submit_async_retain(self, bank_id, contents, *, request_context):
        return await self._admin.submit_async_retain(bank_id, contents, request_context=request_context)

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    async def get_entity_observations(self, bank_id, entity_id, *, limit=10, request_context):
        return await self._entity.get_entity_observations(
            bank_id, entity_id, limit=limit, request_context=request_context
        )

    async def get_entity_observations_batch(self, bank_id, entity_ids, *, limit_per_entity=5, request_context):
        return await self._entity.get_entity_observations_batch(
            bank_id, entity_ids, limit_per_entity=limit_per_entity, request_context=request_context
        )

    async def list_entities(self, bank_id, *, limit=100, request_context):
        return await self._entity.list_entities(bank_id, limit=limit, request_context=request_context)

    async def get_entity_state(self, bank_id, entity_id, entity_name, *, limit=10, request_context):
        return await self._entity.get_entity_state(
            bank_id, entity_id, entity_name, limit=limit, request_context=request_context
        )

    async def get_entity(self, bank_id, entity_id, *, request_context):
        return await self._entity.get_entity(bank_id, entity_id, request_context=request_context)

    async def regenerate_entity_observations(
        self, bank_id, entity_id, entity_name, *, version=None, conn=None, request_context
    ):
        return await self._entity.regenerate_entity_observations(
            bank_id,
            entity_id,
            entity_name,
            version=version,
            conn=conn,
            request_context=request_context,
        )

    async def _regenerate_observations_sync(self, bank_id, entity_ids, min_facts=None, conn=None, request_context=None):
        return await self._entity._regenerate_observations_sync(bank_id, entity_ids, min_facts, conn, request_context)

    async def _handle_regenerate_observations(self, task_dict):
        return await self._entity._handle_regenerate_observations(task_dict)

    # ------------------------------------------------------------------
    # Access count update and batch retain (backward compat)
    # ------------------------------------------------------------------

    async def _handle_access_count_update(self, task_dict):
        return await self._retain._handle_access_count_update(task_dict)

    async def _handle_batch_retain(self, task_dict):
        return await self._retain._handle_batch_retain(task_dict)
