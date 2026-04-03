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

- [ ] **T1 — Neo4j zu Docker-Compose hinzufügen:** Neo4j Service mit Bolt-Port (7687), HTTP-Port (7474), Volume für Data, Auth-Config. APOC Plugin aktivieren (für erweiterte Graph-Algorithmen). Health-Check.
- [ ] **T2 — Config erweitern:** In `hindsight_api/config.py` neue Env-Vars: `NEO4J_BOLT_URL` (default: "bolt://localhost:7687"), `NEO4J_USERNAME` (default: "neo4j"), `NEO4J_PASSWORD`, `NEO4J_DATABASE` (default: "neo4j"). In HindsightConfig Dataclass aufnehmen.
- [ ] **T3 — Dependency hinzufügen:** `neo4j` (async Driver) in `pyproject.toml` ergänzen.
- [ ] **T4 — Neo4j Client-Modul erstellen:** Neues Modul `hindsight_api/engine/neo4j_client.py`. AsyncGraphDatabase Driver mit Session-Management. Retry-Logik analog zu `db_utils.py`. Methoden: `ensure_schema()`, `create_node(label, properties)`, `create_relationship(from_id, to_id, rel_type, properties)`, `get_node(engram_id)`, `get_relationships(engram_id, rel_types)`, `delete_node(engram_id, cascade_relationships=True)`, `traverse(start_id, rel_types, max_depth, min_weight)`, `run_cypher(query, params)`.
- [ ] **T5 — Graph-Definition bei Start:** `ensure_schema()` wird beim Start aufgerufen. Erstellt: Unique Constraint auf `Engram.engram_id`. Composite Index auf `layer` + `status`. Indexe auf `strength`, `thalamus_overall`, `tags`. Prüft ob bereits vorhanden (idempotent).
- [ ] **T6 — Relationship-Types dokumentieren:** In `ensure_schema()` Kommentare zu allen 8 Types: `SEMANTIC` (weight), `TEMPORAL` (weight), `ENTITY` (weight, entity_id), `CAUSAL` (weight, subtype: causes/caused_by/enables/prevents), `CO_ACTIVATED` (weight, activation_count), `TEMPORAL_PROXIMITY` (weight, time_delta), `SCHEMA` (weight), `CONTRADICTION` (weight, resolution). Neo4j erstellt Types implizit beim ersten Create.
- [ ] **T7 — Connectivity-Test:** Test der Node anlegt, Relationship erstellt, per Cypher traversiert und aufräumt.
