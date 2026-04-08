"""Hindsight MCP Server implementation using FastMCP."""

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime

from fastmcp import FastMCP

from hindsight_api import MemoryEngine
from hindsight_api.api.utils import parse_json_param, session_from_mode
from hindsight_api.engine.response_models import VALID_RECALL_FACT_TYPES
from hindsight_api.models import RequestContext

# Configure logging from HINDSIGHT_API_LOG_LEVEL environment variable
_log_level_str = os.environ.get("HINDSIGHT_API_LOG_LEVEL", "info").lower()
_log_level_map = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "trace": logging.DEBUG,
}
logging.basicConfig(
    level=_log_level_map.get(_log_level_str, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Default bank_id from environment variable
DEFAULT_BANK_ID = os.environ.get("HINDSIGHT_MCP_BANK_ID", "default")

# Context variable to hold the current bank_id
_current_bank_id: ContextVar[str | None] = ContextVar("current_bank_id", default=None)


def get_current_bank_id() -> str | None:
    """Get the current bank_id from context."""
    return _current_bank_id.get()


def create_mcp_server(memory: MemoryEngine) -> FastMCP:
    """
    Create and configure the Hindsight MCP server.

    Args:
        memory: MemoryEngine instance (required)

    Returns:
        Configured FastMCP server instance with stateless_http enabled
    """
    # Use stateless_http=True for Claude Code compatibility
    mcp = FastMCP("hindsight-mcp-server", stateless_http=True)

    @mcp.tool()
    async def retain(
        content: str,
        context: str = "general",
        timestamp: str | None = None,
        document_id: str | None = None,
        entities: str | None = None,
        metadata: str | None = None,
        mode: str | None = None,
        async_processing: bool = True,
        bank_id: str | None = None,
    ) -> dict:
        """
        Store important information to long-term memory.

        Use this tool PROACTIVELY whenever the user shares:
        - Personal facts, preferences, or interests
        - Important events or milestones
        - User history, experiences, or background
        - Decisions, opinions, or stated preferences
        - Goals, plans, or future intentions
        - Relationships or people mentioned
        - Work context, projects, or responsibilities

        Args:
            content: The fact/memory to store (be specific and include relevant details)
            context: Category for the memory (e.g., 'preferences', 'work', 'hobbies', 'family'). Default: 'general'
            timestamp: ISO datetime when the event occurred (e.g., '2024-01-15T10:30:00Z'). Helps with temporal ordering.
            document_id: Group related memories under one ID. Re-retaining with the same document_id replaces old memories (upsert).
            entities: JSON array of entity hints. Format: '[{"text": "Alice", "type": "PERSON"}]'. Types: PERSON, ORG, CONCEPT, LOCATION.
            metadata: JSON object with key-value pairs. Format: '{"source": "slack", "channel": "#general"}'.
            mode: Session mode affecting Thalamus filter scoring. Values: precision (default), exploration, analogy, validation.
                  Note: async_processing=True does not apply the mode parameter. Use async_processing=False if mode is important.
            async_processing: If True, queue for background processing and return immediately. If False, wait for completion. Default: True
            bank_id: Optional bank to store in (defaults to session bank). Use for cross-bank operations.
        """
        try:
            target_bank = bank_id or get_current_bank_id()
            if target_bank is None:
                return {"status": "error", "message": "No bank_id configured"}

            content_dict: dict = {"content": content, "context": context}
            if timestamp:
                content_dict["event_date"] = datetime.fromisoformat(timestamp)
            if document_id:
                content_dict["document_id"] = document_id
            if entities:
                try:
                    content_dict["entities"] = parse_json_param(entities, "entities")
                except ValueError as e:
                    logger.warning(f"Ignoring entities: {e}")
            if metadata:
                try:
                    content_dict["metadata"] = parse_json_param(metadata, "metadata")
                except ValueError as e:
                    logger.warning(f"Ignoring metadata: {e}")

            try:
                session = session_from_mode(mode)
            except ValueError as e:
                return {"status": "error", "message": str(e)}

            if async_processing:
                result = await memory.submit_async_retain(
                    bank_id=target_bank,
                    contents=[content_dict],
                    request_context=RequestContext(),
                )
                return {
                    "status": "accepted",
                    "operation_id": result.get("operation_id", "N/A"),
                    "message": "Memory queued for background processing",
                }
            else:
                await memory.retain_batch_async(
                    bank_id=target_bank,
                    contents=[content_dict],
                    session=session,
                    request_context=RequestContext(),
                )
                return {"status": "success", "bank_id": target_bank, "message": "Memory stored successfully"}
        except Exception as e:
            logger.error(f"Error storing memory: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    async def recall(
        query: str,
        max_tokens: int = 4096,
        budget: str = "mid",
        types: str | None = None,
        mode: str | None = None,
        trace: bool = False,
        query_timestamp: str | None = None,
        include_entities: bool = True,
        include_chunks: bool = False,
        tags: str | None = None,
        bank_id: str | None = None,
        shared_bank_id: str | None = None,
    ) -> dict:
        """
        Search memories to provide personalized, context-aware responses.

        Use this tool PROACTIVELY to:
        - Check user's preferences before making suggestions
        - Recall user's history to provide continuity
        - Remember user's goals and context
        - Personalize responses based on past interactions

        Args:
            query: Natural language search query (e.g., "user's food preferences", "what projects is user working on")
            max_tokens: Maximum tokens in the response (default: 4096)
            budget: Search depth. 'low' = fast/fewer results, 'mid' = balanced (default), 'high' = deep search with spreading activation.
            types: Comma-separated fact types to search. Values: world, experience, opinion. Default: all types.
            mode: Session mode affecting result ranking. Values: precision (default), exploration, analogy, validation.
            trace: Set to true to get debug information about the search process (scoring, timing, retrieval steps).
            query_timestamp: ISO datetime for temporal context. Retrieval ranks memories closer to this timestamp higher.
            include_entities: Include entity observations with results (default: true).
            include_chunks: Include raw text chunks that memories were extracted from (default: false).
            tags: Comma-separated tags to filter by.
            bank_id: Optional bank to search in (defaults to session bank). Use for cross-bank operations.
            shared_bank_id: Optional shared/global bank to query in parallel (dual-bank recall, B6).
        """
        try:
            target_bank = bank_id or get_current_bank_id()
            if target_bank is None:
                return {"error": "No bank_id configured", "results": []}

            from hindsight_api.engine.memory_engine import Budget

            budget_map = {"low": Budget.LOW, "mid": Budget.MID, "high": Budget.HIGH}
            budget_enum = budget_map.get(budget.lower(), Budget.MID)

            fact_types = [t.strip() for t in types.split(",") if t.strip()] if types else list(VALID_RECALL_FACT_TYPES)
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            question_date = datetime.fromisoformat(query_timestamp) if query_timestamp else None

            try:
                session = session_from_mode(mode)
            except ValueError as e:
                return {"error": str(e), "results": []}

            recall_result = await memory.recall_async(
                bank_id=target_bank,
                query=query,
                fact_type=fact_types,
                budget=budget_enum,
                max_tokens=max_tokens,
                enable_trace=trace,
                question_date=question_date,
                include_entities=include_entities,
                include_chunks=include_chunks,
                tags=tag_list,
                session=session,
                request_context=RequestContext(),
                shared_bank_id=shared_bank_id,
            )

            return recall_result.model_dump()
        except Exception as e:
            logger.error(f"Error searching: {e}", exc_info=True)
            return {"error": str(e), "results": []}

    @mcp.tool()
    async def reflect(
        query: str,
        context: str | None = None,
        budget: str = "low",
        max_tokens: int = 4096,
        mode: str | None = None,
        response_schema: str | None = None,
        include_facts: bool = False,
        bank_id: str | None = None,
    ) -> dict:
        """
        Generate thoughtful analysis by synthesizing stored memories with the bank's personality.

        WHEN TO USE THIS TOOL:
        Use reflect when you need reasoned analysis, not just fact retrieval. This tool
        thinks through the question using everything the bank knows and its personality traits.

        EXAMPLES OF GOOD QUERIES:
        - "What patterns have emerged in how I approach debugging?"
        - "Based on my past decisions, what architectural style do I prefer?"
        - "What might be the best approach for this problem given what you know about me?"
        - "How should I prioritize these tasks based on my goals?"

        HOW IT DIFFERS FROM RECALL:
        - recall: Returns raw facts matching your search (fast lookup)
        - reflect: Reasons across memories to form a synthesized answer (deeper analysis)

        Use recall for "what did I say about X?" and reflect for "what should I do about X?"

        Args:
            query: The question or topic to reflect on
            context: Optional context about why this reflection is needed
            budget: Search depth. 'low' = quick opinion (default), 'mid' = moderate analysis, 'high' = deep synthesis.
            max_tokens: Maximum tokens in the response (default: 4096)
            mode: Session mode. 'analogy' finds unexpected cross-domain connections. 'exploration' broadens associations.
            response_schema: JSON Schema string for structured output. The response will include a 'structured_output' field.
            include_facts: Include the facts the answer is based on (default: false). Useful for transparency and verification.
            bank_id: Optional bank to reflect in (defaults to session bank). Use for cross-bank operations.
        """
        try:
            target_bank = bank_id or get_current_bank_id()
            if target_bank is None:
                return {"error": "No bank_id configured", "text": ""}

            from hindsight_api.engine.memory_engine import Budget

            budget_map = {"low": Budget.LOW, "mid": Budget.MID, "high": Budget.HIGH}
            budget_enum = budget_map.get(budget.lower(), Budget.LOW)

            try:
                session = session_from_mode(mode)
            except ValueError as e:
                return {"error": str(e), "text": ""}

            parsed_schema: dict | None = None
            if response_schema:
                try:
                    parsed_schema = parse_json_param(response_schema, "response_schema")
                except ValueError as e:
                    return {"error": str(e), "text": ""}

            reflect_result = await memory.reflect_async(
                bank_id=target_bank,
                query=query,
                budget=budget_enum,
                context=context,
                max_tokens=max_tokens,
                response_schema=parsed_schema,
                session=session,
                request_context=RequestContext(),
            )

            result_dict = reflect_result.model_dump()
            if not include_facts:
                result_dict.pop("based_on", None)
            return result_dict
        except Exception as e:
            logger.error(f"Error reflecting: {e}", exc_info=True)
            return {"error": str(e), "text": ""}

    @mcp.tool()
    async def list_banks() -> dict:
        """
        List all available memory banks.

        Use this tool to discover what memory banks exist in the system.
        Each bank is an isolated memory store (like a separate "brain").

        Returns:
            JSON list of banks with their IDs, names, dispositions, and backgrounds.
        """
        try:
            banks = await memory.list_banks(request_context=RequestContext())
            return {"banks": banks}
        except Exception as e:
            logger.error(f"Error listing banks: {e}", exc_info=True)
            return {"error": str(e), "banks": []}

    @mcp.tool()
    async def create_bank(bank_id: str, name: str | None = None, background: str | None = None) -> dict:
        """
        Create a new memory bank or get an existing one.

        Memory banks are isolated stores - each one is like a separate "brain" for a user/agent.
        Banks are auto-created with default settings if they don't exist.

        Args:
            bank_id: Unique identifier for the bank (e.g., 'user-123', 'agent-alpha')
            name: Optional human-friendly name for the bank
            background: Optional background context about the bank's owner/purpose
        """
        try:
            # get_bank_profile auto-creates bank if it doesn't exist
            profile = await memory.get_bank_profile(bank_id, request_context=RequestContext())

            # Update name/background if provided
            if name is not None or background is not None:
                await memory.update_bank(
                    bank_id,
                    name=name,
                    background=background,
                    request_context=RequestContext(),
                )
                # Fetch updated profile
                profile = await memory.get_bank_profile(bank_id, request_context=RequestContext())

            # Serialize disposition if it's a Pydantic model
            if "disposition" in profile and hasattr(profile["disposition"], "model_dump"):
                profile["disposition"] = profile["disposition"].model_dump()
            return profile
        except Exception as e:
            logger.error(f"Error creating bank: {e}", exc_info=True)
            return {"error": str(e)}

    return mcp


class MCPMiddleware:
    """ASGI middleware that extracts bank_id from header or path and sets context.

    Bank ID can be provided via:
    1. X-Bank-Id header (recommended for Claude Code)
    2. URL path: /mcp/{bank_id}/
    3. Environment variable HINDSIGHT_MCP_BANK_ID (fallback default)

    For Claude Code, configure with:
        claude mcp add --transport http hindsight http://localhost:8888/mcp \\
            --header "X-Bank-Id: my-bank"
    """

    def __init__(self, app, memory: MemoryEngine):
        self.app = app
        self.memory = memory
        self.mcp_server = create_mcp_server(memory)
        self.mcp_app = self.mcp_server.http_app(path="/")
        # Expose the lifespan for the parent app to chain
        self.lifespan = self.mcp_app.lifespan_handler if hasattr(self.mcp_app, "lifespan_handler") else None

    def _get_header(self, scope: dict, name: str) -> str | None:
        """Extract a header value from ASGI scope."""
        name_lower = name.lower().encode()
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == name_lower:
                return header_value.decode()
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Strip any mount prefix (e.g., /mcp) that FastAPI might not have stripped
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :] or "/"

        # Also handle case where mount path wasn't stripped (e.g., /mcp/...)
        if path.startswith("/mcp/"):
            path = path[4:]  # Remove /mcp prefix
        elif path == "/mcp":
            path = "/"

        # Try to get bank_id from header first (for Claude Code compatibility)
        bank_id = self._get_header(scope, "X-Bank-Id")

        # MCP endpoint paths that should not be treated as bank_ids
        MCP_ENDPOINTS = {"sse", "messages"}

        # If no header, try to extract from path: /{bank_id}/...
        new_path = path
        if not bank_id and path.startswith("/") and len(path) > 1:
            parts = path[1:].split("/", 1)
            # Don't treat MCP endpoints as bank_ids
            if parts[0] and parts[0] not in MCP_ENDPOINTS:
                # First segment looks like a bank_id
                bank_id = parts[0]
                new_path = "/" + parts[1] if len(parts) > 1 else "/"

        # Fall back to default bank_id
        if not bank_id:
            bank_id = DEFAULT_BANK_ID
            logger.debug(f"Using default bank_id: {bank_id}")

        # Set bank_id context
        token = _current_bank_id.set(bank_id)
        try:
            new_scope = scope.copy()
            new_scope["path"] = new_path
            # Clear root_path since we're passing directly to the app
            new_scope["root_path"] = ""

            # Wrap send to rewrite the SSE endpoint URL to include bank_id if using path-based routing
            async def send_wrapper(message):
                if message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body and b"/messages" in body:
                        # Rewrite /messages to /{bank_id}/messages in SSE endpoint event
                        body = body.replace(b"data: /messages", f"data: /{bank_id}/messages".encode())
                        message = {**message, "body": body}
                await send(message)

            await self.mcp_app(new_scope, receive, send_wrapper)
        finally:
            _current_bank_id.reset(token)

    async def _send_error(self, send, status: int, message: str):
        """Send an error response."""
        body = json.dumps({"error": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )


def create_mcp_app(memory: MemoryEngine):
    """
    Create an ASGI app that handles MCP requests.

    Bank ID can be provided via:
    1. X-Bank-Id header: claude mcp add --transport http hindsight http://localhost:8888/mcp --header "X-Bank-Id: my-bank"
    2. URL path: /mcp/{bank_id}/
    3. Environment variable HINDSIGHT_MCP_BANK_ID (fallback, default: "default")

    Args:
        memory: MemoryEngine instance

    Returns:
        ASGI application
    """
    return MCPMiddleware(None, memory)
