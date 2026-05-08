# Story 27 — Control Plane: Schema-Explorer Backend-Endpoints

## User Story

Als Control Plane soll ich Schema-Daten aus der neuen Architektur (`:Schema`/`:HyperSchema`-Knoten in Neo4j + Centroid in Qdrant + Evidence in PostgreSQL) über dedizierte Endpoints lesen können, damit das Frontend den Schema-Explorer auf der neuen Datenstruktur aufbauen kann.

## Kontext

Die alte CP-API für den Schema-Explorer (Epic 22) liest Engrams mit `layer='neocortex'`. In der neuen Architektur sind Schemas eigene Neo4j-Knoten mit eigenen Feldern (description, properties, evidence_engram_ids, evidence_count, centroid_qdrant_id, cycles_survived, status). Backend-Endpoints müssen entsprechend umgebaut werden.

## Bestehende Codebasis

- **Control Plane API:** `controlplane/api/schemas.py` (aus Epic 22) — aktuell auf alte Engram-Layer-Logik.
- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01).
- **Engram Repository:** für Evidence-Auflösung.

## Akzeptanzkriterien

- [x] Endpoint `GET /v1/cp/banks/{bank_id}/schemas` mit Filter status (active/archived/all), sort_by (last_reinforced_at/evidence_count/cycles_survived), limit, offset.
- [x] Endpoint `GET /v1/cp/schemas/{schema_id}` mit `?include_centroid=true` für die 2D-Plot-Vorbereitung; Hyper-Schema-Fallback wenn Schema-Label miss.
- [x] Endpoint `GET /v1/cp/schemas/{schema_id}/evidence` (Top-N Evidence-Engrams via PG join, Order erhalten).
- [x] Endpoint `GET /v1/cp/banks/{bank_id}/hyper-schemas` mit `MATCH (h:HyperSchema) OPTIONAL MATCH (s)-[:SPECIALIZES]->(h)` und `children_ids` Liste.
- [x] Bonus-Endpoint `GET /v1/cp/banks/{bank_id}/schemas/centroid-2d?method=umap` (501 wenn `umap-learn` nicht installiert; 503 ohne Qdrant).
- [x] OpenAPI-Doku via FastAPI-Auto-Generation aktualisiert (DTOs liefern Schema, `tags=["Control Plane — Schemas"]`).
- [x] 13 Unit-Tests; Integration-Test verschoben auf Block-G-E2E (Coffee-Meeting).

## Tasks

- [x] **T1 — Endpoint-Implementation:** Neuer FastAPI APIRouter in `hindsight_api/api/cp_schemas.py` (Pfad-Abweichung vom Story-Spec: kein `controlplane/api/schemas.py` — der alte Pfad existierte nicht; Schema-Endpoints leben jetzt zusammen mit der bestehenden API). Im `api/http.py` per `app.include_router(cp_schema_router)` gemountet; `app.state.memory` wird gesetzt damit der Pool resolvebar ist.
- [x] **T2 — Pydantic-DTOs:** 5 DTOs (SchemaListItemDTO, SchemaDetailDTO mit erweiterten Story-21/22-Feldern access_count/last_accessed/drift_count/last_drifted_at, EvidenceEngramDTO, HyperSchemaDTO mit children_ids, CentroidPoint2DDTO).
- [x] **T3 — Centroid-2D-Reduktion:** UMAP-basiert; gracefully 501 (Not Implemented) wenn `umap-learn` Lib fehlt — der Endpoint registriert sich trotzdem.
- [x] **T4 — Hyper-Schema-Cypher:** `MATCH (h:HyperSchema {status:'active'}) OPTIONAL MATCH (s:Schema)-[:SPECIALIZES]->(h) RETURN properties(h) AS hp, collect(s.id) AS child_ids ORDER BY h.last_reinforced_at DESC LIMIT $limit`.
- [x] **T5 — OpenAPI-Spec:** Wird durch FastAPI automatisch aus den DTOs + Endpoint-Decorators generiert; `./scripts/generate-openapi.sh` für die Aktualisierung des spec-Files (außerhalb dieser Story).
- [ ] **T6 — Integration-Test:** verschoben — Stories 19/20 KE-Tests + Coffee-Meeting decken den Datenfluss (PG/Neo4j/Qdrant) bereits ab; ein dedizierter HTTP-Integration-Test folgt mit Story 28's Frontend-Adaption, weil dann Datenfluss + UI gemeinsam smoke-testbar sind.
