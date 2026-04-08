"""
Tests for GET /v1/default/config endpoint.

T4: Unit-Test Response-Struktur + Integration-Test DB-Status.

Note: TestClient without context manager skips the lifespan (no real DB connections).
State is set directly on app.state before each test.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Build a minimal FastAPI app with the config endpoint wired up.

    Uses initialize_memory=False and does NOT use TestClient as context manager,
    so the lifespan (which connects to real Qdrant/Neo4j) is never triggered.
    """
    from hindsight_api.api.http import create_app
    from hindsight_api.engine.memory_engine import MemoryEngine

    memory = MagicMock(spec=MemoryEngine)
    memory.initialize = AsyncMock()
    memory.health_check = AsyncMock(return_value={"status": "healthy"})
    memory._pool = None  # skip EngramStorageService init
    memory._ctx = MagicMock()
    memory._ctx.llm_registry = MagicMock()

    app = create_app(memory, initialize_memory=False)
    # Set state directly — no lifespan needed
    app.state.memory = memory
    return app


# ---------------------------------------------------------------------------
# Unit tests: Response structure
# ---------------------------------------------------------------------------


class TestConfigEndpointStructure:
    """Verify the response shape without live DB connections."""

    def setup_method(self):
        self.app = _make_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def test_returns_200(self):
        response = self.client.get("/v1/default/config")
        assert response.status_code == 200

    def test_has_llm_section(self):
        data = self.client.get("/v1/default/config").json()
        assert "llm" in data
        assert "provider" in data["llm"]
        assert "model" in data["llm"]
        assert "tier_routing" in data["llm"]

    def test_has_embeddings_section(self):
        data = self.client.get("/v1/default/config").json()
        assert "embeddings" in data
        assert "provider" in data["embeddings"]
        assert "model" in data["embeddings"]

    def test_has_reranker_section(self):
        data = self.client.get("/v1/default/config").json()
        assert "reranker" in data
        assert "provider" in data["reranker"]
        assert "model" in data["reranker"]

    def test_has_database_section(self):
        data = self.client.get("/v1/default/config").json()
        assert "database" in data
        assert "postgres" in data["database"]
        assert "qdrant" in data["database"]
        assert "neo4j" in data["database"]

    def test_has_ncr_section(self):
        data = self.client.get("/v1/default/config").json()
        assert "ncr" in data
        assert "enabled" in data["ncr"]
        assert "interval_hours" in data["ncr"]

    def test_no_api_key_in_response(self):
        data = self.client.get("/v1/default/config").json()
        response_str = str(data)
        assert "api_key" not in response_str
        assert "password" not in response_str
        assert "connection_string" not in response_str

    def test_tier_routing_is_dict(self):
        data = self.client.get("/v1/default/config").json()
        assert isinstance(data["llm"]["tier_routing"], dict)

    def test_ncr_enabled_is_bool(self):
        data = self.client.get("/v1/default/config").json()
        assert isinstance(data["ncr"]["enabled"], bool)

    def test_ncr_interval_hours_is_number(self):
        data = self.client.get("/v1/default/config").json()
        assert isinstance(data["ncr"]["interval_hours"], (int, float))


# ---------------------------------------------------------------------------
# Integration tests: DB status reflects actual connectivity
# ---------------------------------------------------------------------------


class TestConfigEndpointDBStatus:
    """Verify DB status fields reflect actual connection state.

    All tests use TestClient without context manager to avoid triggering
    the lifespan (which would try to connect to real Qdrant/Neo4j).
    app.state is set directly before each call.
    """

    def setup_method(self):
        self.app = _make_app()

    def _get_config(self) -> dict:
        client = TestClient(self.app, raise_server_exceptions=True)
        return client.get("/v1/default/config").json()

    def test_postgres_connected_when_health_check_healthy(self):
        self.app.state.memory.health_check = AsyncMock(return_value={"status": "healthy"})
        data = self._get_config()
        assert data["database"]["postgres"] == "connected"

    def test_postgres_disconnected_when_health_check_unhealthy(self):
        self.app.state.memory.health_check = AsyncMock(return_value={"status": "unhealthy"})
        data = self._get_config()
        assert data["database"]["postgres"] == "disconnected"

    def test_postgres_disconnected_when_health_check_raises(self):
        self.app.state.memory.health_check = AsyncMock(side_effect=RuntimeError("pool gone"))
        data = self._get_config()
        assert data["database"]["postgres"] == "disconnected"

    def test_qdrant_connected_when_client_responds(self):
        mock_qdrant = MagicMock()
        mock_inner = AsyncMock()
        mock_qdrant._require_client.return_value = mock_inner
        self.app.state.qdrant = mock_qdrant
        data = self._get_config()
        assert data["database"]["qdrant"] == "connected"

    def test_qdrant_disconnected_when_client_raises(self):
        mock_qdrant = MagicMock()
        mock_qdrant._require_client.side_effect = RuntimeError("not connected")
        self.app.state.qdrant = mock_qdrant
        data = self._get_config()
        assert data["database"]["qdrant"] == "disconnected"

    def test_neo4j_connected_when_driver_responds(self):
        mock_neo4j = MagicMock()
        mock_driver = MagicMock()
        mock_driver.verify_connectivity = AsyncMock()
        mock_neo4j._require_driver.return_value = mock_driver
        self.app.state.neo4j = mock_neo4j
        data = self._get_config()
        assert data["database"]["neo4j"] == "connected"

    def test_neo4j_disconnected_when_driver_raises(self):
        mock_neo4j = MagicMock()
        mock_neo4j._require_driver.side_effect = RuntimeError("not connected")
        self.app.state.neo4j = mock_neo4j
        data = self._get_config()
        assert data["database"]["neo4j"] == "disconnected"

    def test_qdrant_disconnected_when_not_on_state(self):
        # Ensure qdrant is absent from state
        if hasattr(self.app.state, "qdrant"):
            del self.app.state.qdrant
        data = self._get_config()
        assert data["database"]["qdrant"] == "disconnected"

    def test_neo4j_disconnected_when_not_on_state(self):
        if hasattr(self.app.state, "neo4j"):
            del self.app.state.neo4j
        data = self._get_config()
        assert data["database"]["neo4j"] == "disconnected"
