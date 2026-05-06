# Story 01 — Schema als eigenständige Neo4j-Entität

## User Story

Als System soll ich `:Schema`- und `:HyperSchema`-Knoten in Neo4j als eigenständige Entitäten halten (mit Description, Properties, evidence_engram_ids, evidence_count, centroid_qdrant_id), damit Schemas vom Engram-Datenmodell entkoppelt sind und die CLS-konforme Trennung Buffer/Cortex umgesetzt ist.

## Kontext

Bisher repräsentiert ein "Schema" indirekt einen Engram mit `layer='neocortex'`. Mit Epic 25 wird das Schema zur eigenen Entität — eigener Knoten-Typ in Neo4j, eigenes Datenmodell. Engrams haben kein `neocortex`-Layer mehr (Story 02). Die Schema-Properties (description, properties, evidence_engram_ids, evidence_count, centroid_qdrant_id, cycles_survived, last_reinforced_at) sind in Kapitel 4.2 des Konzepts beschrieben.

## Bestehende Codebasis

- **Neo4j Client:** `engine/neo4j_client.py` — Cypher-Operationen für Knoten/Edges. Aktuell keine `:Schema`-Konstruktoren.
- **Schema Processor:** `engine/consolidation/schema_processor.py` — operiert heute auf Engram-Knoten mit `layer='neocortex'`.
- **Migrations:** `migrations/` — Neo4j-Schema-Constraints werden hier verwaltet.

## Akzeptanzkriterien

- [x] Neuer Knoten-Typ `:Schema` mit Properties (id, description, properties, evidence_engram_ids, evidence_count, centroid_qdrant_id, created_at, last_reinforced_at, cycles_survived, status)
- [x] Neuer Knoten-Typ `:HyperSchema` mit denselben Property-Feldern
- [x] Edge-Typ `:SPECIALIZES` zwischen Schema und HyperSchema definiert
- [x] Cypher-Helper-Funktionen: `create_schema()`, `get_schema(id)`, `update_schema(id, props)`, `archive_schema(id)`, `link_specialization(schema_id, hyper_id)`
- [x] Constraints: `id` unique pro Knoten-Typ
- [x] Indizes: `centroid_qdrant_id`, `last_reinforced_at`
- [x] Unit-Tests für CRUD-Operationen

## Tasks

- [x] **T1 — Neo4j Schema-Migration:** Cypher-Constraints `CREATE CONSTRAINT schema_id IF NOT EXISTS FOR (s:Schema) REQUIRE s.id IS UNIQUE` analog für `:HyperSchema`. Indizes für `centroid_qdrant_id` und `last_reinforced_at`.
- [x] **T2 — Schema-Helper-Modul:** Neue Datei `engine/schema/schema_repository.py` mit allen CRUD-Funktionen (`create_schema`, `get_schema`, `update_schema`, `archive_schema`, `list_active_schemas`, `link_specialization`).
- [x] **T3 — Pydantic-Modelle:** ~~`models/schema.py`~~ → `engine/schema/models.py` (Naming-Abweichung: `models.py` ist die SQLAlchemy-ORM-Datei; ein `models/`-Package würde sie shadowen — Pydantic-Modelle kolozieren neben dem Repository wie in `engine/constructive/models.py`). `SchemaModel` und `HyperSchemaModel` mit allen Feldern typisiert, default-Werte für created_at/cycles_survived/status.
- [x] **T4 — Helper-Funktion `materialize_schema_node`:** Nimmt Pydantic-Modell, schreibt nach Neo4j (idempotent — bei existierender ID Update statt Create). Dispatch via `isinstance(model, HyperSchemaModel)`.
- [x] **T5 — Unit-Tests:** 17 Unit-Tests in `tests/test_schema_repository.py` (mocked `run_cypher`); 5 Integration-Tests gegen echtes Neo4j gated via `@pytest.mark.integration` und graceful skip ohne Live-Instanz.
