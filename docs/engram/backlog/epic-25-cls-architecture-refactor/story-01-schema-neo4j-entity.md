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

- [ ] Neuer Knoten-Typ `:Schema` mit Properties (id, description, properties, evidence_engram_ids, evidence_count, centroid_qdrant_id, created_at, last_reinforced_at, cycles_survived, status)
- [ ] Neuer Knoten-Typ `:HyperSchema` mit denselben Property-Feldern
- [ ] Edge-Typ `:SPECIALIZES` zwischen Schema und HyperSchema definiert
- [ ] Cypher-Helper-Funktionen: `create_schema()`, `get_schema(id)`, `update_schema(id, props)`, `archive_schema(id)`, `link_specialization(schema_id, hyper_id)`
- [ ] Constraints: `id` unique pro Knoten-Typ
- [ ] Indizes: `centroid_qdrant_id`, `last_reinforced_at`
- [ ] Unit-Tests für CRUD-Operationen

## Tasks

- [ ] **T1 — Neo4j Schema-Migration:** Cypher-Constraints `CREATE CONSTRAINT schema_id IF NOT EXISTS FOR (s:Schema) REQUIRE s.id IS UNIQUE` analog für `:HyperSchema`. Indizes für `centroid_qdrant_id` und `last_reinforced_at`.
- [ ] **T2 — Schema-Helper-Modul:** Neue Datei `engine/schema/schema_repository.py` mit allen CRUD-Funktionen (`create_schema`, `get_schema`, `update_schema`, `archive_schema`, `list_active_schemas`, `link_specialization`).
- [ ] **T3 — Pydantic-Modelle:** `models/schema.py` mit `SchemaModel` und `HyperSchemaModel` (alle Felder typisiert, default-Werte für created_at/cycles_survived/status).
- [ ] **T4 — Helper-Funktion `materialize_schema_node`:** Nimmt Pydantic-Modell, schreibt nach Neo4j (idempotent — bei existierender ID Update statt Create).
- [ ] **T5 — Unit-Tests:** Create + Read + Update + Archive je Knoten-Typ. Spezialisierungs-Edge schreiben + lesen. Konstraint-Verletzung (doppelte ID) wirft sauberen Fehler.
