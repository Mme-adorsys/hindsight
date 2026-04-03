# Story 02 — Neo4j Integration

## User Story

Als System brauche ich Neo4j als dedizierten Graph Store neben dem bestehenden PostgreSQL, damit Engrams als Knoten mit typisierten Beziehungen gespeichert und per Graph-Traversierung effizient durchsucht werden können.

## Kontext

Hindsight speichert Beziehungen aktuell in der `memory_links` Tabelle (PostgreSQL) mit 4 Link-Typen (temporal, semantic, entity, causal). Für die Engram-Schicht reicht das nicht — Neo4j bietet Index-free Adjacency für O(1)-per-Hop Graph-Traversierung und unterstützt die erweiterten Relationship-Typen (co_activated, temporal_proximity, schema, contradiction). PostgreSQL `memory_links` bleibt für den Agent Session Bank.

## Bestehende Codebasis

- **Config Pattern:** `hindsight_api/config.py` → Env-Vars + Dataclass. Neo4j-Config hier einfügen.
- **DB Connection Pattern:** `hindsight_api/engine/db_utils.py` → Retry-Logik. Analoges Pattern für Neo4j Driver.
- **Graph Retrieval:** `hindsight_api/engine/search/graph_retrieval.py` → `GraphRetriever` ABC mit `retrieve()` Methode. BFS und MPFP Implementierungen existieren. Der neue EngramRetriever (Epic 07) wird dieses Interface implementieren.
- **Link-Typen:** `hindsight_api/models.py` → `MemoryLink` Model mit `link_type` (temporal, semantic, entity, causes, caused_by, enables, prevents). Neo4j erweitert diese um co_activated, temporal_proximity, schema, contradiction.
- **Dependencies:** `pyproject.toml` → `neo4j` (async Driver) als neue Dependency.

## Akzeptanzkriterien

- [ ] Neo4j-Config in `config.py` (Bolt URL, Auth, Database Name) über Env-Vars steuerbar
- [ ] Neo4j-Client-Modul im Engine-Layer mit Connection-Handling und Session-Management
- [ ] Engram Node-Label mit allen Properties definiert (Constraints + Indexe)
- [ ] Alle 8 Relationship-Types definiert mit Properties
- [ ] Docker-Compose um Neo4j-Service erweitert
- [ ] Basis-Operationen (create node, create relationship, traverse, delete) funktionieren
- [ ] Bestehende PostgreSQL `memory_links` Funktionalität unverändert

## Tasks

- [x] **T1 — Neo4j zu Docker-Compose hinzufügen:** `docker/compose.yml` um Neo4j 5-community erweitert. Bolt (7687) + HTTP (7474), Volumes für data/logs, Auth `neo4j/hindsight`, APOC Plugin aktiviert, Health-Check via wget.
- [x] **T2 — Config erweitern:** `NEO4J_BOLT_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` in `config.py` + HindsightConfig Dataclass + from_env() + main.py.
- [x] **T3 — Dependency hinzufügen:** `neo4j>=5.0.0` in `pyproject.toml`.
- [x] **T4 — Neo4j Client-Modul erstellen:** `hindsight_api/engine/neo4j_client.py` mit `Neo4jEngineClient`. AsyncGraphDatabase Driver + Session-Management + Retry-Logik. Methoden: `connect()`, `close()`, `ensure_schema()`, `create_node()`, `create_relationship()`, `get_node()`, `get_relationships()`, `delete_node()`, `traverse()`, `run_cypher()`.
- [x] **T5 — Graph-Definition bei Start:** `ensure_schema()` in FastAPI-Lifespan (`api/http.py`) eingehängt. Unique Constraint + Composite Index (layer+status) + Index strength + Index thalamus_overall. Idempotent via `IF NOT EXISTS`.
- [x] **T6 — Relationship-Types dokumentieren:** Alle 8 Types im Modul-Docstring + `RELATIONSHIP_TYPES` Konstante. Validation in `create_relationship()` mit ValueError bei unbekanntem Type.
- [x] **T7 — Connectivity-Test:** `tests/test_neo4j_connectivity.py` mit 7 Tests: create+get, relationship, traversal, delete, idempotenz, invalid type, run_cypher.
