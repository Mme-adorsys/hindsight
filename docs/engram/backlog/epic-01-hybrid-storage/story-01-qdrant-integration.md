# Story 01 — Qdrant Integration

## User Story

Als System brauche ich Qdrant als dedizierten Vector Store neben dem bestehenden PostgreSQL+pgvector, damit Engram-Content skalierbar gespeichert und durchsucht werden kann.

## Kontext

Hindsight nutzt aktuell PostgreSQL+pgvector für Vektor-Operationen (HNSW Index auf `memory_units.embedding`, 384-dim). Für die Engram-Schicht (Shared Memory) reicht das nicht — Qdrant übernimmt die Kernkompetenz Vector Similarity Search auf Skalierung. PostgreSQL+pgvector bleibt für den Agent Session Bank (kurzfristiger Working Memory).

## Bestehende Codebasis

- **Config Pattern:** `hindsight_api/config.py` → HindsightConfig Dataclass, Env-Vars. Neue Qdrant-Config hier einfügen.
- **DB Connection Pattern:** `hindsight_api/engine/db_utils.py` → asyncpg Pool mit Retry. Analoges Pattern für Qdrant-Client.
- **Dependencies:** `hindsight-api/pyproject.toml` → `qdrant-client` als neue Dependency hinzufügen.
- **Embedding-Modell:** `hindsight_api/engine/embeddings.py` → BAAI/bge-small-en-v1.5, 384-dim. Qdrant Collection muss gleiche Dimension nutzen.

## Akzeptanzkriterien

- [ ] Qdrant-Config in `config.py` (URL, API Key, Collection Name) über Env-Vars steuerbar
- [ ] Qdrant-Client-Modul im Engine-Layer mit Connection-Handling analog zu `db_utils.py`
- [ ] Collection `engrams` mit 384-dim Cosine Index angelegt
- [ ] Docker-Compose um Qdrant-Service erweitert
- [ ] Basis-Operationen (upsert, search, get, delete) funktionieren
- [ ] Bestehende PostgreSQL+pgvector Funktionalität ist unverändert

## Tasks

- [x] **T1 — Qdrant zu Docker-Compose hinzufügen:** `docker/docker-compose.yml` neu erstellt. Qdrant Service mit Volume, Port 6333/6334 und Health-Check.
- [x] **T2 — Config erweitern:** In `hindsight_api/config.py` neue Env-Vars: `QDRANT_URL` (default: "http://localhost:6333"), `QDRANT_API_KEY` (optional), `QDRANT_COLLECTION` (default: "engrams"). In HindsightConfig Dataclass + from_env() + main.py aufgenommen.
- [x] **T3 — Dependency hinzufügen:** `qdrant-client>=1.9.0` in `pyproject.toml` ergänzt.
- [x] **T4 — Qdrant Client-Modul erstellen:** `hindsight_api/engine/qdrant_client.py` mit `QdrantEngineClient`. Async Client + Retry-Logik analog `db_utils.py`. Methoden: `connect()`, `close()`, `ensure_collection()`, `upsert_point()`, `search_similar()`, `get_by_id()`, `delete_by_id()`, `batch_upsert()`.
- [x] **T5 — Collection-Initialisierung:** `ensure_collection()` in FastAPI-Lifespan (`api/http.py`) eingehängt. Qdrant-Client wird nach `memory.initialize()` gestartet, Collection idempotent angelegt, Client auf `app.state.qdrant` gespeichert. Shutdown via `app.state.qdrant.close()`.
- [x] **T6 — Connectivity-Test:** `tests/test_qdrant_connectivity.py` mit 4 Tests: upsert→search→get→delete Round-trip, batch_upsert, ensure_collection idempotent, get_by_id für fehlende ID.
