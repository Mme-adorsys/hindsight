"""
EngineContext — Shared Infrastructure for MemoryEngine Orchestrators.

Holds all shared state and infrastructure that orchestrators need:
  - Connection pool (asyncpg)
  - Embeddings and cross-encoder reranker
  - LLM registry (subtask-aware model routing)
  - Query analyzer
  - Task backend
  - Session manager (lazy-init, thread-safe)
  - Extension hooks (operation validator, tenant extension)
  - Semaphores for backpressure
  - pg0 lifecycle (embedded PostgreSQL)

Usage by orchestrators:
    class RetainOrchestrator:
        def __init__(self, ctx: EngineContext) -> None:
            self._ctx = ctx

        async def retain_async(self, ...):
            pool = await self._ctx.get_pool()
            llm = self._ctx.llm_registry.get_llm("retain", "fact_extraction")
            ...

The MemoryEngine facade creates one EngineContext in __init__ and passes it to
all orchestrators. The facade delegates initialize() / close() to this class.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import asyncpg

from .cross_encoder import CrossEncoderModel
from .embeddings import Embeddings, create_embeddings_from_env
from .llm_wrapper import LLMConfig
from .query_analyzer import QueryAnalyzer
from .search.reranking import CrossEncoderReranker
from .task_backend import AsyncIOQueueBackend, NoopTaskBackend, TaskBackend
from .utils import _current_schema, acquire_with_retry, fq_table

if TYPE_CHECKING:
    from hindsight_api.extensions import OperationValidatorExtension, TenantExtension
    from hindsight_api.models import RequestContext

    from .llm_routing import LLMRegistry
    from .response_models import Session
    from .session.mode_config import ModeConfig
    from .session.session_manager import SessionManager

logger = logging.getLogger(__name__)


def _build_llm_base_url(provider: str, explicit_base_url: str | None) -> str:
    """
    Resolve the base URL for an LLM provider.

    Returns the explicit value if provided, otherwise falls back to
    well-known provider defaults (Groq, Ollama). All other providers
    return an empty string (OpenAI-compatible APIs use no base URL).
    """
    if explicit_base_url is not None:
        return explicit_base_url
    if provider.lower() == "groq":
        return "https://api.groq.com/openai/v1"
    if provider.lower() == "ollama":
        return "http://localhost:11434/v1"
    return ""


class EngineContext:
    """
    Shared infrastructure container for MemoryEngine orchestrators.

    One instance is created in MemoryEngine.__init__ and passed to all
    orchestrators. Lifecycle (initialize / close) is driven by MemoryEngine.
    """

    def __init__(
        self,
        db_url: str | None = None,
        memory_llm_provider: str | None = None,
        memory_llm_api_key: str | None = None,
        memory_llm_model: str | None = None,
        memory_llm_base_url: str | None = None,
        retain_llm_provider: str | None = None,
        retain_llm_api_key: str | None = None,
        retain_llm_model: str | None = None,
        retain_llm_base_url: str | None = None,
        reflect_llm_provider: str | None = None,
        reflect_llm_api_key: str | None = None,
        reflect_llm_model: str | None = None,
        reflect_llm_base_url: str | None = None,
        embeddings: Embeddings | None = None,
        cross_encoder: CrossEncoderModel | None = None,
        query_analyzer: QueryAnalyzer | None = None,
        pool_min_size: int | None = None,
        pool_max_size: int | None = None,
        db_command_timeout: int | None = None,
        db_acquire_timeout: int | None = None,
        task_backend: TaskBackend | None = None,
        task_batch_size: int | None = None,
        task_batch_interval: float | None = None,
        run_migrations: bool = True,
        operation_validator: "OperationValidatorExtension | None" = None,
        tenant_extension: "TenantExtension | None" = None,
        skip_llm_verification: bool | None = None,
        lazy_reranker: bool | None = None,
        session_manager: "SessionManager | None" = None,
    ) -> None:
        from ..config import get_config
        from ..pg0 import parse_pg0_url

        config = get_config()

        # --- Optimization flags ---
        self._skip_llm_verification = (
            skip_llm_verification if skip_llm_verification is not None else config.skip_llm_verification
        )
        self._lazy_reranker = lazy_reranker if lazy_reranker is not None else config.lazy_reranker

        # --- Apply config defaults ---
        db_url = db_url or config.database_url
        memory_llm_provider = memory_llm_provider or config.llm_provider
        memory_llm_api_key = memory_llm_api_key or config.llm_api_key
        if not memory_llm_api_key and memory_llm_provider not in ("ollama", "mock"):
            raise ValueError("LLM API key is required. Set HINDSIGHT_API_LLM_API_KEY environment variable.")
        memory_llm_model = memory_llm_model or config.llm_model
        memory_llm_base_url = _build_llm_base_url(
            memory_llm_provider, memory_llm_base_url or config.get_llm_base_url() or None
        )

        # --- pg0 (embedded PostgreSQL) ---
        self._pg0 = None
        self._use_pg0, self._pg0_instance_name, self._pg0_port = parse_pg0_url(db_url)
        self.db_url: str | None = None if self._use_pg0 else db_url

        # --- Pool config ---
        self._pool: asyncpg.Pool | None = None
        self._initialized = False
        self._pool_min_size = pool_min_size if pool_min_size is not None else config.db_pool_min_size
        self._pool_max_size = pool_max_size if pool_max_size is not None else config.db_pool_max_size
        self._db_command_timeout = db_command_timeout if db_command_timeout is not None else config.db_command_timeout
        self._db_acquire_timeout = db_acquire_timeout if db_acquire_timeout is not None else config.db_acquire_timeout
        self._run_migrations = run_migrations

        # --- Embeddings ---
        self.embeddings: Embeddings = embeddings if embeddings is not None else create_embeddings_from_env()

        # --- Query analyzer ---
        if query_analyzer is not None:
            self.query_analyzer: QueryAnalyzer = query_analyzer
        else:
            from .query_analyzer import DateparserQueryAnalyzer

            self.query_analyzer = DateparserQueryAnalyzer()

        # --- Global LLM config ---
        self._llm_config = LLMConfig(
            provider=memory_llm_provider,
            api_key=memory_llm_api_key,
            base_url=memory_llm_base_url,
            model=memory_llm_model,
        )

        # --- Per-operation LLM configs ---
        retain_provider = retain_llm_provider or config.retain_llm_provider or memory_llm_provider
        retain_api_key = retain_llm_api_key or config.retain_llm_api_key or memory_llm_api_key
        retain_model = retain_llm_model or config.retain_llm_model or memory_llm_model
        self._retain_llm_config = LLMConfig(
            provider=retain_provider,
            api_key=retain_api_key,
            base_url=_build_llm_base_url(retain_provider, retain_llm_base_url or config.retain_llm_base_url or None),
            model=retain_model,
        )

        reflect_provider = reflect_llm_provider or config.reflect_llm_provider or memory_llm_provider
        reflect_api_key = reflect_llm_api_key or config.reflect_llm_api_key or memory_llm_api_key
        reflect_model = reflect_llm_model or config.reflect_llm_model or memory_llm_model
        self._reflect_llm_config = LLMConfig(
            provider=reflect_provider,
            api_key=reflect_api_key,
            base_url=_build_llm_base_url(reflect_provider, reflect_llm_base_url or config.reflect_llm_base_url or None),
            model=reflect_model,
        )

        # --- LLM registry (subtask-aware routing) ---
        from .llm_routing import LLMRegistry

        self.llm_registry: LLMRegistry = LLMRegistry(
            global_config=self._llm_config,
            operation_configs={"retain": self._retain_llm_config, "reflect": self._reflect_llm_config},
        )

        # --- Cross-encoder reranker ---
        self._cross_encoder_reranker = CrossEncoderReranker(cross_encoder=cross_encoder)

        # --- Task backend ---
        if task_backend:
            self._task_backend: TaskBackend = task_backend
        elif config.task_backend == "noop":
            self._task_backend = NoopTaskBackend()
        else:
            _batch_size = task_batch_size if task_batch_size is not None else config.task_backend_memory_batch_size
            _batch_interval = (
                task_batch_interval if task_batch_interval is not None else config.task_backend_memory_batch_interval
            )
            self._task_backend = AsyncIOQueueBackend(batch_size=_batch_size, batch_interval=_batch_interval)

        # --- Backpressure semaphores ---
        self._search_semaphore = asyncio.Semaphore(10)
        self._put_semaphore = asyncio.Semaphore(5)

        # --- Extensions ---
        self._operation_validator: "OperationValidatorExtension | None" = operation_validator
        self._tenant_extension: "TenantExtension | None" = tenant_extension

        # --- Session manager (lazy-init, thread-safe) ---
        self._session_manager: "SessionManager | None" = session_manager
        self._session_manager_lock = threading.Lock()

        # --- Entity resolver (set during initialize()) ---
        self.entity_resolver = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, executor: Callable[[dict[str, Any]], Coroutine] | None = None) -> None:
        """
        Initialize the connection pool, models, and task backend.

        Runs model loading and pg0 startup in parallel for faster overall init.

        Args:
            executor: Coroutine callable passed to the task backend as its
                      execution target. Typically MemoryEngine.execute_task.
        """
        if self._initialized:
            return

        loop = asyncio.get_running_loop()

        async def start_pg0() -> None:
            if self._use_pg0:
                from ..pg0 import EmbeddedPostgres

                kwargs: dict[str, Any] = {"name": self._pg0_instance_name}
                if self._pg0_port is not None:
                    kwargs["port"] = self._pg0_port
                pg0 = EmbeddedPostgres(**kwargs)  # type: ignore[invalid-argument-type]
                was_already_running = await pg0.is_running()
                self.db_url = await pg0.ensure_running()
                if not was_already_running:
                    self._pg0 = pg0

        async def init_embeddings() -> None:
            if self.embeddings.provider_name == "local":
                await loop.run_in_executor(None, lambda: asyncio.run(self.embeddings.initialize()))
            else:
                await self.embeddings.initialize()

        async def init_cross_encoder() -> None:
            cross_encoder = self._cross_encoder_reranker.cross_encoder
            if cross_encoder.provider_name == "local":
                await loop.run_in_executor(None, lambda: asyncio.run(cross_encoder.initialize()))
            else:
                await cross_encoder.initialize()
            self._cross_encoder_reranker._initialized = True

        async def init_query_analyzer() -> None:
            await loop.run_in_executor(None, self.query_analyzer.load)

        async def verify_llm() -> None:
            if not self._skip_llm_verification:
                await self._llm_config.verify_connection()
                retain_is_different = (
                    self._retain_llm_config.provider != self._llm_config.provider
                    or self._retain_llm_config.model != self._llm_config.model
                )
                if retain_is_different:
                    await self._retain_llm_config.verify_connection()
                reflect_is_different = (
                    self._reflect_llm_config.provider != self._llm_config.provider
                    or self._reflect_llm_config.model != self._llm_config.model
                ) and (
                    self._reflect_llm_config.provider != self._retain_llm_config.provider
                    or self._reflect_llm_config.model != self._retain_llm_config.model
                )
                if reflect_is_different:
                    await self._reflect_llm_config.verify_connection()

        init_tasks: list[Any] = [start_pg0(), init_embeddings(), init_query_analyzer()]
        if not self._lazy_reranker:
            init_tasks.append(init_cross_encoder())
        if not self._skip_llm_verification:
            init_tasks.append(verify_llm())

        await asyncio.gather(*init_tasks)

        # Migrations
        if self._run_migrations:
            from ..migrations import ensure_embedding_dimension, run_migrations

            if not self.db_url:
                raise ValueError("Database URL is required for migrations")
            logger.info("Running database migrations...")
            run_migrations(self.db_url)
            ensure_embedding_dimension(self.db_url, self.embeddings.dimension)

        logger.info(f"Connecting to PostgreSQL at {self.db_url}")
        self._pool = await asyncpg.create_pool(
            self.db_url,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
            command_timeout=self._db_command_timeout,
            statement_cache_size=0,
            timeout=self._db_acquire_timeout,
        )

        from .entity_resolver import EntityResolver

        self.entity_resolver = EntityResolver(self._pool)

        if executor is not None:
            self._task_backend.set_executor(executor)
        await self._task_backend.initialize()

        self._initialized = True
        logger.info("EngineContext initialized (pool and task backend started)")

    async def close(self) -> None:
        """Close the connection pool and shut down the task backend."""
        logger.info("EngineContext.close() started")
        await self._task_backend.shutdown()
        if self._pool is not None:
            self._pool.terminate()
            self._pool = None
        self._initialized = False
        if self._pg0 is not None:
            logger.info("Stopping pg0...")
            await self._pg0.stop()
            self._pg0 = None
            logger.info("pg0 stopped")

    # ------------------------------------------------------------------
    # Public accessors for orchestrator use
    # ------------------------------------------------------------------

    @property
    def operation_validator(self) -> "OperationValidatorExtension | None":
        """Operation validator extension (may be None if not configured)."""
        return self._operation_validator

    @operation_validator.setter
    def operation_validator(self, value: "OperationValidatorExtension | None") -> None:
        self._operation_validator = value

    @property
    def tenant_extension(self) -> "TenantExtension | None":
        """Tenant extension (may be None if not configured)."""
        return self._tenant_extension

    @property
    def task_backend(self) -> "TaskBackend":
        """Task backend for submitting background work."""
        return self._task_backend

    @property
    def search_semaphore(self) -> asyncio.Semaphore:
        """Semaphore for limiting concurrent search operations."""
        return self._search_semaphore

    @property
    def put_semaphore(self) -> asyncio.Semaphore:
        """Semaphore for limiting concurrent retain operations."""
        return self._put_semaphore

    @property
    def reflect_llm_config(self) -> "LLMConfig | None":
        """LLM config for reflect operations."""
        return self._reflect_llm_config

    @property
    def cross_encoder_reranker(self) -> "CrossEncoderReranker":
        """Cross-encoder reranker instance."""
        return self._cross_encoder_reranker

    # ------------------------------------------------------------------
    # Pool access
    # ------------------------------------------------------------------

    async def get_pool(self) -> asyncpg.Pool:
        """Return the connection pool, initializing lazily if needed."""
        if not self._initialized:
            await self.initialize()
        return self._pool  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Session manager
    # ------------------------------------------------------------------

    def get_session_manager(self) -> "SessionManager":
        """Return the SessionManager, creating a default instance on first use (thread-safe)."""
        if self._session_manager is None:
            with self._session_manager_lock:
                if self._session_manager is None:
                    from .session.session_manager import SessionManager

                    self._session_manager = SessionManager()
        return self._session_manager

    def resolve_session_config(self, session: "Session | None") -> "ModeConfig":
        """
        Resolve ModeConfig for the given session.

        Returns the Precision profile when session is None.
        """
        from .response_models import RetrievalMode
        from .session.mode_config import get_mode_config

        mode = session.mode if session is not None else RetrievalMode.PRECISION
        return get_mode_config(mode)

    # ------------------------------------------------------------------
    # Tenant authentication
    # ------------------------------------------------------------------

    async def authenticate_tenant(self, request_context: "RequestContext | None") -> str:
        """
        Authenticate tenant and set schema in the async context variable.

        Returns:
            Schema name that was set.

        Raises:
            AuthenticationError: If authentication fails or request_context is missing.
        """
        if self._tenant_extension is None:
            _current_schema.set("public")
            return "public"

        from hindsight_api.extensions import AuthenticationError

        if request_context is None:
            raise AuthenticationError("RequestContext is required when tenant extension is configured")

        tenant_context = await self._tenant_extension.authenticate(request_context)
        _current_schema.set(tenant_context.schema_name)
        return tenant_context.schema_name

    # ------------------------------------------------------------------
    # Operation validation
    # ------------------------------------------------------------------

    async def validate_operation(self, validation_coro: Any) -> None:
        """
        Run validation if an operation validator is configured.

        Raises:
            OperationValidationError: If validation fails.
        """
        if self._operation_validator is None:
            return

        from hindsight_api.extensions import OperationValidationError

        result = await validation_coro
        if not result.allowed:
            raise OperationValidationError(result.reason or "Operation not allowed", result.status_code)

    # ------------------------------------------------------------------
    # Async-operation record helpers
    # ------------------------------------------------------------------

    async def delete_operation_record(self, operation_id: str) -> None:
        """Delete an async_operations record after successful task completion."""
        try:
            pool = await self.get_pool()
            async with acquire_with_retry(pool) as conn:
                await conn.execute(
                    f"DELETE FROM {fq_table('async_operations')} WHERE operation_id = $1",
                    uuid.UUID(operation_id),
                )
        except Exception as e:
            logger.error(f"Failed to delete async operation record {operation_id}: {e}")

    async def mark_operation_failed(self, operation_id: str, error_message: str, error_traceback: str) -> None:
        """Mark an async_operations record as failed with the error details."""
        try:
            pool = await self.get_pool()
            full_error = f"{error_message}\n\nTraceback:\n{error_traceback}"
            truncated_error = full_error[:5000] if len(full_error) > 5000 else full_error
            async with acquire_with_retry(pool) as conn:
                await conn.execute(
                    f"""
                    UPDATE {fq_table("async_operations")}
                    SET status = 'failed', error_message = $2
                    WHERE operation_id = $1
                    """,
                    uuid.UUID(operation_id),
                    truncated_error,
                )
            logger.info(f"Marked async operation as failed: {operation_id}")
        except Exception as e:
            logger.error(f"Failed to mark operation as failed {operation_id}: {e}")
