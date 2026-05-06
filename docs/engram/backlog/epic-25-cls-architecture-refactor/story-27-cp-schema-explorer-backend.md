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

- [ ] Endpoint `GET /v1/cp/banks/{bank_id}/schemas` — Liste aller aktiven Schemas mit description, evidence_count, last_reinforced_at, cycles_survived
- [ ] Endpoint `GET /v1/cp/schemas/{schema_id}` — Detail-View mit Properties, evidence_engram_ids, Centroid-Vektor (optional, für 2D-Plot)
- [ ] Endpoint `GET /v1/cp/schemas/{schema_id}/evidence` — Liefert die Top-N Evidence-Engrams mit Content
- [ ] Endpoint `GET /v1/cp/banks/{bank_id}/hyper-schemas` — Liste der Hyper-Schemas mit `:SPECIALIZES`-Children
- [ ] Filter-Parameter: status, sort_by, limit, offset
- [ ] OpenAPI-Doku aktualisiert
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Endpoint-Implementation:** `controlplane/api/schemas.py` umschreiben — alte Engram-basierte Logik entfernen, neue Schema-Knoten-basierte Logik einbauen.
- [ ] **T2 — Pydantic-DTOs:** `SchemaListItemDTO`, `SchemaDetailDTO`, `EvidenceEngramDTO`, `HyperSchemaDTO`.
- [ ] **T3 — Centroid-2D-Reduktion:** Optional via UMAP — neuer Endpoint `GET /v1/cp/banks/{bank_id}/schemas/centroid-2d?method=umap` für Frontend-Visualisierung.
- [ ] **T4 — Hyper-Schema-Cypher:** Cypher-Query für Hyper-Schemas: `MATCH (h:HyperSchema)<-[:SPECIALIZES]-(s:Schema) WHERE h.bank_id = $bank_id RETURN h, collect(s)`.
- [ ] **T5 — OpenAPI-Spec:** `controlplane/openapi.yaml` aktualisieren (alte Endpoints entfernen, neue hinzufügen).
- [ ] **T6 — Integration-Test:** Smoke-Test alle 4 neuen Endpoints liefern erwartete Struktur.
