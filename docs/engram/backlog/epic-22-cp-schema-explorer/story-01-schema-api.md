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

- [ ] `GET /v1/default/banks/{bank_id}/schemas?limit=50` liefert Schema-Liste
- [ ] `GET /v1/default/banks/{bank_id}/schemas/{schema_id}` liefert Schema + Member-Engrams
- [ ] Response enthält: schema_id, label, member_count, maturity, avg_strength, created_at, last_activated
- [ ] Detail-Response enthält zusätzlich: members[] mit Engram-ID, Text-Preview, Strength
- [ ] Leere Bank (keine Schemas) liefert leere Liste, keinen Fehler
- [ ] Limit-Parameter funktioniert (default 50, max 200)

## Tasks

- [ ] **T1 — Neo4j Query: Schema List** — In `graph_storage.py` neue Methode `list_schemas(bank_id, limit)`. Cypher-Query: Match Schema-Nodes für die Bank, return Properties + Member Count. Sortiert nach `last_activated DESC`.

- [ ] **T2 — Neo4j Query: Schema Detail** — In `graph_storage.py` neue Methode `get_schema_detail(bank_id, schema_id)`. Cypher-Query: Match Schema-Node + alle `BELONGS_TO` Relationships + Member-Engram Nodes. Return Schema Properties + Member List (id, text_preview, strength).

- [ ] **T3 — Dataplane Endpoints** — Zwei neue Route Handler in `http.py`:
  - `GET /v1/default/banks/{bank_id}/schemas` — List mit limit Parameter
  - `GET /v1/default/banks/{bank_id}/schemas/{schema_id}` — Detail
  - Response-Models: `SchemaListResponse`, `SchemaDetailResponse`

- [ ] **T4 — CP API Routes** — Zwei Routes:
  - `src/app/api/schemas/route.ts` — GET, proxy mit bank_id und limit
  - `src/app/api/schemas/[schemaId]/route.ts` — GET, proxy mit bank_id und schema_id

- [ ] **T5 — CP Client erweitern** — In `src/lib/api.ts`:
  - `listSchemas(params: { bank_id: string; limit?: number }): Promise<SchemaListResponse>`
  - `getSchema(schemaId: string, bankId: string): Promise<SchemaDetailResponse>`
  - Typed Interfaces für Schema, SchemaMember
