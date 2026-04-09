# Story 01 — Schema API Endpoints

## User Story

Als Control Plane will ich Schemas (Meta-Engrams) und ihre Member-Engrams über die API abfragen können, damit ich sie dem Operator im Schema Explorer anzeigen kann.

## Kontext

Der Schema Processor (`engram_schema_processor.py`) erstellt Schema-Nodes in Neo4j wenn Cluster aus häufig co-aktivierten Engrams erkannt werden (Game-of-Life Regeln R1–R5). Diese Schemas existieren nur in Neo4j — es gibt keinen API-Endpoint um sie abzufragen. Der Schema Explorer braucht mindestens List und Detail Endpoints.

## Bestehende Codebasis

- **Schema Processor:** `hindsight_api/engine/ncr/engram_schema_processor.py` — erstellt/aktualisiert Schema-Nodes in Neo4j. Schema-Node Properties: label, member_count, maturity, avg_strength, created_at, last_activated.
- **Neo4j Storage:** `hindsight_api/engine/graph_storage.py` — Neo4j Client. Schema-Nodes haben `BELONGS_TO` Relationships zu Member-Engrams.
- **HTTP API:** `hindsight_api/api/http.py` — Router.

## Akzeptanzkriterien

- [x] `GET /v1/default/banks/{bank_id}/schemas?limit=50` liefert Schema-Liste
- [x] `GET /v1/default/banks/{bank_id}/schemas/{schema_id}` liefert Schema + Member-Engrams
- [x] Response enthält: schema_id, label (=content), member_count, maturity (berechnet), avg_strength, created_at, last_activated (=last_reinforced_at)
- [x] Detail-Response enthält zusätzlich: members[] mit Engram-ID, Text-Preview, Strength
- [x] Leere Bank (keine Schemas) liefert leere Liste, keinen Fehler (Neo4j=None → leere Liste)
- [x] Limit-Parameter funktioniert (default 50, max 200)

## Tasks

- [x] **T1 — Neo4j Query: Schema List** — In `neo4j_client.py` neue Methode `list_schemas(bank_id, limit)`. Cypher: MATCH Schema-Nodes mit OPTIONAL MATCH auf `:SCHEMA`-Relationship für member_count. Maturity berechnet aus strength+member_count (dominant/stable/emerging). Sortiert nach `last_reinforced_at DESC`.

- [x] **T2 — Neo4j Query: Schema Detail** — In `neo4j_client.py` neue Methode `get_schema_detail(bank_id, schema_id)`. Zwei Cypher-Queries: Schema-Node Properties + Member-Engrams via `:SCHEMA`-Relationship. Returns None wenn Schema nicht existiert. Members mit `substring(content, 0, 200)` als text_preview.

- [x] **T3 — Dataplane Endpoints** — Zwei neue Route Handler in `http.py`: `GET /schemas` (SchemaListResponse, limit default=50 max=200) und `GET /schemas/{schema_id}` (SchemaDetailResponse, 404 wenn nicht gefunden). Pydantic Models: SchemaItem, SchemaMember, SchemaListResponse, SchemaDetailResponse. Neo4j-Zugriff via `app.state.memory._neo4j`.

- [x] **T4 — CP API Routes** �� `src/app/api/schemas/route.ts` (GET, bank_id+limit aus Query) und `src/app/api/schemas/[schemaId]/route.ts` (GET, bank_id aus Query, schemaId aus Path, 404 Forwarding). Direct-fetch Pattern wie Epic 21.

- [x] **T5 — CP Client erweitern** — In `api.ts`: Interfaces SchemaMember, SchemaItem, SchemaListResponse, SchemaDetailResponse. Methoden `listSchemas(bankId, limit?)` und `getSchemaDetail(schemaId, bankId)` in ControlPlaneClient.
