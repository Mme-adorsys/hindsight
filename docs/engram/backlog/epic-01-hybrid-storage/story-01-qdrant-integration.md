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

- [ ] **T1 — Qdrant zu Docker-Compose hinzufügen:** `docker/` Verzeichnis prüfen, Qdrant Service mit Volume und Port ergänzen. Health-Check konfigurieren.
- [ ] **T2 — Config erweitern:** In `hindsight_api/config.py` neue Env-Vars hinzufügen: `QDRANT_URL` (default: "http://localhost:6333"), `QDRANT_API_KEY` (optional), `QDRANT_COLLECTION` (default: "engrams"). In HindsightConfig Dataclass aufnehmen.
- [ ] **T3 — Dependency hinzufügen:** `qdrant-client` in `pyproject.toml` unter `[tool.poetry.dependencies]` ergänzen.
- [ ] **T4 — Qdrant Client-Modul erstellen:** Neues Modul `hindsight_api/engine/qdrant_client.py`. Async QdrantClient mit Retry-Logik analog zu `db_utils.py`. Methoden: `ensure_collection()` (erstellt Collection wenn nicht vorhanden), `upsert_point(engram_id, embedding, payload)`, `search_similar(embedding, limit, filters)`, `get_by_id(engram_id)`, `delete_by_id(engram_id)`, `batch_upsert(points)`.
- [ ] **T5 — Collection-Initialisierung:** `ensure_collection()` wird beim Start aufgerufen (analog zu Alembic Migrations). Prüft ob Collection existiert, erstellt sie mit 384-dim Cosine Distance wenn nicht. Payload-Index auf `engram_id`, `tags`, `source`.
- [ ] **T6 — Connectivity-Test:** Test in `hindsight_api/tests/` der Qdrant-Client instantiiert, Point schreibt, per Vector Search zurückholt und löscht.
