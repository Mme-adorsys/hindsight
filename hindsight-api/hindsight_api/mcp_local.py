"""
Local MCP server for use with Claude Code (stdio transport).

This runs a fully local Hindsight instance with embedded PostgreSQL (pg0).
No external database or server required.

Run with:
    hindsight-local-mcp

Or with uvx:
    uvx hindsight-api@latest hindsight-local-mcp

Configure in Claude Code's MCP settings:
    {
        "mcpServers": {
            "hindsight": {
                "command": "uvx",
                "args": ["hindsight-api@latest", "hindsight-local-mcp"],
                "env": {
                    "HINDSIGHT_API_LLM_API_KEY": "your-openai-key"
                }
            }
        }
    }

Environment variables:
    HINDSIGHT_API_LLM_API_KEY: Required. API key for LLM provider.
    HINDSIGHT_API_LLM_PROVIDER: Optional. LLM provider (default: "openai").
    HINDSIGHT_API_LLM_MODEL: Optional. LLM model (default: "gpt-4o-mini").
    HINDSIGHT_API_MCP_LOCAL_BANK_ID: Optional. Memory bank ID (default: "mcp").
    HINDSIGHT_API_LOG_LEVEL: Optional. Log level (default: "warning").
    HINDSIGHT_API_MCP_INSTRUCTIONS: Optional. Additional instructions appended to retain, recall, and reflect tools.

Available tools: retain, recall, reflect, list_banks, create_bank

Example custom instructions (these are ADDED to the default behavior):
    To also store assistant actions:
        HINDSIGHT_API_MCP_INSTRUCTIONS="Also store every action you take, including tool calls, code written, and decisions made."

    To also store conversation summaries:
        HINDSIGHT_API_MCP_INSTRUCTIONS="Also store summaries of important conversations and their outcomes."
"""

import json
import logging
import os
import sys
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from hindsight_api.config import (
    DEFAULT_MCP_LOCAL_BANK_ID,
    DEFAULT_MCP_RECALL_DESCRIPTION,
    DEFAULT_MCP_REFLECT_DESCRIPTION,
    DEFAULT_MCP_RETAIN_DESCRIPTION,
    ENV_MCP_INSTRUCTIONS,
    ENV_MCP_LOCAL_BANK_ID,
)

# Configure logging - default to warning to avoid polluting stderr during MCP init
# MCP clients interpret stderr output as errors, so we suppress INFO logs by default
_log_level_str = os.environ.get("HINDSIGHT_API_LOG_LEVEL", "warning").lower()
_log_level_map = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
logging.basicConfig(
    level=_log_level_map.get(_log_level_str, logging.WARNING),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stderr,  # MCP uses stdout for protocol, logs go to stderr
)
logger = logging.getLogger(__name__)


def create_local_mcp_server(bank_id: str, memory=None) -> FastMCP:
    """
    Create a stdio MCP server with retain/recall/reflect/list_banks/create_bank tools.

    Args:
        bank_id: The memory bank ID to use for all operations.
        memory: Optional MemoryEngine instance. If not provided, creates one with pg0.

    Returns:
        Configured FastMCP server instance.
    """
    # Import here to avoid slow startup if just checking --help
    from hindsight_api import MemoryEngine
    from hindsight_api.api.utils import parse_json_param, session_from_mode
    from hindsight_api.engine.memory_engine import Budget
    from hindsight_api.engine.response_models import VALID_RECALL_FACT_TYPES
    from hindsight_api.models import RequestContext

    # Create memory engine with pg0 embedded database if not provided
    if memory is None:
        memory = MemoryEngine(db_url="pg0://hindsight-mcp")

    # Get custom instructions from environment variable (appended to tools)
    extra_instructions = os.environ.get(ENV_MCP_INSTRUCTIONS, "")

    retain_description = DEFAULT_MCP_RETAIN_DESCRIPTION
    recall_description = DEFAULT_MCP_RECALL_DESCRIPTION
    reflect_description = DEFAULT_MCP_REFLECT_DESCRIPTION

    if extra_instructions:
        retain_description = f"{DEFAULT_MCP_RETAIN_DESCRIPTION}\n\nAdditional instructions: {extra_instructions}"
        recall_description = f"{DEFAULT_MCP_RECALL_DESCRIPTION}\n\nAdditional instructions: {extra_instructions}"
        reflect_description = f"{DEFAULT_MCP_REFLECT_DESCRIPTION}\n\nAdditional instructions: {extra_instructions}"

    mcp = FastMCP("hindsight")

    @mcp.tool(description=retain_description)
    async def retain(
        content: str,
        mode: str,
        task_context: str,
        context: str = "general",
        timestamp: str | None = None,
        document_id: str | None = None,
        entities: str | None = None,
        metadata: str | None = None,
        expectation: str | None = None,
        outcome: str | None = None,
        tags: str | None = None,
    ) -> dict:
        """
        REQUIRED:
            content: The fact/memory to store (be specific and include relevant details)
            mode: Session mode gating Thalamus scoring. One of precision | exploration
                  | analogy | validation.
            task_context: What the caller is doing when retaining. Feeds Thalamus
                  task_relevance. Minimum 3 characters.

        Optional:
            context: Freetext category (e.g., 'preferences', 'work'). Default: 'general'
            timestamp: ISO datetime when the event occurred.
            document_id: Group related memories (upsert on reuse).
            entities: JSON array of entity hints.
            metadata: JSON object with key-value pairs.
            tags: Comma-separated user tags.
            expectation + outcome: PAIRED — set BOTH or NEITHER. Feeds surprise
                  scoring via prediction error.
        """
        import asyncio

        if bool(expectation) != bool(outcome):
            return {
                "status": "error",
                "message": (
                    "expectation and outcome must be set together or both "
                    "left empty — setting only one half breaks surprise scoring."
                ),
            }
        if not task_context or len(task_context.strip()) < 3:
            return {
                "status": "error",
                "message": "task_context is required (min 3 characters).",
            }

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
        if expectation:
            content_dict["expectation"] = expectation
        if outcome:
            content_dict["outcome"] = outcome
        if tags:
            content_dict["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

        try:
            session = session_from_mode(mode)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Attach task_context to the session so it lands in engram_dictionary
        if session is not None:
            session = session.__class__(
                mode=session.mode,
                task_context=task_context,
                current_expectation=session.current_expectation,
            )

        async def _retain():
            try:
                await memory.retain_batch_async(
                    bank_id=bank_id,
                    contents=[content_dict],
                    session=session,
                    request_context=RequestContext(),
                )
            except Exception as e:
                logger.error(f"Error storing memory: {e}", exc_info=True)

        # Fire and forget - don't block on memory storage
        asyncio.create_task(_retain())
        return {"status": "accepted", "message": "Memory storage initiated"}

    @mcp.tool(description=recall_description)
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
    ) -> dict:
        """
        Args:
            query: Natural language search query (e.g., "user's food preferences", "what projects is user working on")
            max_tokens: Maximum tokens to return in results (default: 4096)
            budget: Search depth. 'low' = fast/fewer results, 'mid' = balanced (default), 'high' = deep search with spreading activation.
            types: Comma-separated fact types to search. Values: world, experience, opinion. Default: all types.
            mode: Session mode affecting result ranking. Values: precision (default), exploration, analogy, validation.
            trace: Set to true to get debug information about the search process.
            query_timestamp: ISO datetime for temporal context. Retrieval ranks memories closer to this timestamp higher.
            include_entities: Include entity observations with results (default: true).
            include_chunks: Include raw text chunks that memories were extracted from (default: false).
            tags: Comma-separated tags to filter by.
        """
        try:
            budget_map = {"low": Budget.LOW, "mid": Budget.MID, "high": Budget.HIGH}
            budget_enum = budget_map.get(budget.lower(), Budget.MID)

            fact_types = [t.strip() for t in types.split(",") if t.strip()] if types else list(VALID_RECALL_FACT_TYPES)
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            question_date = datetime.fromisoformat(query_timestamp) if query_timestamp else None

            try:
                session = session_from_mode(mode)
            except ValueError as e:
                return {"error": str(e), "results": []}

            search_result = await memory.recall_async(
                bank_id=bank_id,
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
            )

            return search_result.model_dump()
        except Exception as e:
            logger.error(f"Error searching: {e}", exc_info=True)
            return {"error": str(e), "results": []}

    @mcp.tool(description=reflect_description)
    async def reflect(
        query: str,
        context: str | None = None,
        budget: str = "low",
        max_tokens: int = 4096,
        mode: str | None = None,
        response_schema: str | None = None,
        include_facts: bool = False,
    ) -> dict:
        """
        Args:
            query: The question or topic to reflect on
            context: Optional context about why this reflection is needed
            budget: Search depth. 'low' = quick opinion (default), 'mid' = moderate analysis, 'high' = deep synthesis.
            max_tokens: Maximum tokens in the response (default: 4096)
            mode: Session mode. 'analogy' finds unexpected cross-domain connections. 'exploration' broadens associations.
            response_schema: JSON Schema string for structured output. The response will include a 'structured_output' field.
            include_facts: Include the facts the answer is based on (default: false). Useful for transparency and verification.
        """
        try:
            budget_map = {"low": Budget.LOW, "mid": Budget.MID, "high": Budget.HIGH}
            budget_enum = budget_map.get(budget.lower(), Budget.LOW)

            try:
                session = session_from_mode(mode)
            except ValueError as e:
                return {"error": str(e), "text": ""}

            parsed_schema: dict | list | None = None
            if response_schema:
                try:
                    parsed_schema = parse_json_param(response_schema, "response_schema")
                except ValueError as e:
                    return {"error": str(e), "text": ""}

            reflect_result = await memory.reflect_async(
                bank_id=bank_id,
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
            from hindsight_api.models import RequestContext as RC

            banks = await memory.list_banks(request_context=RC())
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
            from hindsight_api.models import RequestContext as RC

            profile = await memory.get_bank_profile(bank_id, request_context=RC())

            if name is not None or background is not None:
                await memory.update_bank(
                    bank_id,
                    name=name,
                    background=background,
                    request_context=RC(),
                )
                profile = await memory.get_bank_profile(bank_id, request_context=RC())

            if "disposition" in profile and hasattr(profile["disposition"], "model_dump"):
                profile["disposition"] = profile["disposition"].model_dump()
            return profile
        except Exception as e:
            logger.error(f"Error creating bank: {e}", exc_info=True)
            return {"error": str(e)}

    return mcp


async def _initialize_and_run(bank_id: str):
    """Initialize memory and run the MCP server."""
    from hindsight_api import MemoryEngine

    # Create and initialize memory engine with pg0 embedded database
    # Note: We avoid printing to stderr during init as MCP clients show it as "errors"
    memory = MemoryEngine(db_url="pg0://hindsight-mcp")
    await memory.initialize()

    # Create and run the server
    mcp = create_local_mcp_server(bank_id, memory=memory)
    await mcp.run_stdio_async()


def main():
    """Main entry point for the stdio MCP server."""
    import asyncio

    from hindsight_api.config import ENV_LLM_API_KEY, get_config

    # Check for required environment variables
    config = get_config()
    if not config.llm_api_key:
        print(f"Error: {ENV_LLM_API_KEY} environment variable is required", file=sys.stderr)
        print("Set it in your MCP configuration or shell environment", file=sys.stderr)
        sys.exit(1)

    # Get bank ID from environment, default to "mcp"
    bank_id = os.environ.get(ENV_MCP_LOCAL_BANK_ID, DEFAULT_MCP_LOCAL_BANK_ID)

    # Note: We don't print to stderr as MCP clients display it as "error output"
    # Use HINDSIGHT_API_LOG_LEVEL=debug for verbose startup logging

    # Run the async initialization and server
    asyncio.run(_initialize_and_run(bank_id))


if __name__ == "__main__":
    main()
