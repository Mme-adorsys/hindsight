# Story 23 — Multi-Bank: Schema-Promotion in Shared Bank

## User Story

Als System soll ich starke Schemas einer Agent-Bank in die Shared Memory Bank promoten können (statt wie früher Neocortex-Engrams), damit cross-agent Wissen geteilt wird.

## Kontext

Die alte Multi-Bank-Promotion (Epic 14) promotet Engrams mit `layer='neocortex'` und Strength ≥ 0.6 in die Shared Bank. In der neuen Architektur gibt es keine Neocortex-Engrams mehr — die generalisierten Strukturen sind die Schemas. Heißt: cross-agent Wissensaustausch passiert auf Schema-Ebene, nicht mehr auf Engram-Ebene.

## Bestehende Codebasis

- **Multi-Bank-Promoter:** `engine/multi_bank/multi_bank_promoter.py` (aus Epic 14) — operiert heute auf Neocortex-Engrams.
- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01).
- **Shared Bank Setup:** Bank mit `tier='shared'`.

## Akzeptanzkriterien

- [x] Promotion-Kandidaten sind ab jetzt **Schemas** der Agent-Banks (Shared-side `evidence_engram_ids=[]`).
- [x] Promotion-Bedingungen drift-guarded in `engine/consolidation/constants.py`:
  - `evidence_count ≥ SHARED_PROMOTION_MIN_EVIDENCE = 10`
  - `cycles_survived ≥ SHARED_PROMOTION_MIN_CYCLES = 3`
  - `last_reinforced_at > now − SHARED_PROMOTION_MAX_DAYS_INACTIVE = 7d`
- [x] Promotion ist eine **Schema-Kopie** mit neuer UUID; Original-Schema in Agent-Bank bleibt unverändert (Replikation, kein Move).
- [x] `source_bank_id` und `promoted_from_schema_id` werden in den `properties`-JSON des Shared-Schemas gestempelt (Audit-Pfad ohne neue Neo4j-Property).
- [x] Best-effort Qdrant-Centroid-Kopie: schlägt der Qdrant-Write fehl, bleibt das Cortex-Schema bestehen — der nächste C2-Lauf in der Shared-Bank kann den Centroid neu setzen.
- [x] Per-Lauf-Logging: `SchemaPromotionResult{scanned, promoted, skipped_below_evidence, skipped_below_cycles, skipped_inactive, promoted_ids, errors}`.
- [x] 13 Unit-Tests grün; Integration-Test verschoben auf Block E (Story 19/20 KE-Suite ist DB-tauglich, ein dezidierter Multi-Bank-Smoke folgt mit Block-G-Wiring).

## Tasks

- [x] **T1 — `promote_schema_to_shared` + `promote_schemas_batch`:** in neuem `engine/multi_bank/schema_promoter.py` (+ `__init__.py` für das Package).
- [ ] **T2 — Promotion-Trigger via API-Endpoint:** verschoben — der bestehende `POST /v1/default/banks/{bank_id}/ncr/trigger?phase=shared`-Endpoint deckt das Triggering im NCR-Pfad bereits ab; Block-G-Wiring nach Stories 24–26 baut die schemapfad-spezifische Route auf.
- [x] **T3 — Konstanten:** drei Defaults in `constants.py` (drift-guard im Test).
- [x] **T4 — Audit-Stempel:** `properties.source_bank_id` und `properties.promoted_from_schema_id` (statt einer dedizierten Neo4j-Property — kein Schema-Migration-Bedarf, JSON-Round-Trip bewährt aus Story 01).
- [ ] **T5 — Cleanup alter Multi-Bank-Engram-Promoter:** verschoben auf Story 26 (eigene Story für Engram-Promotion-Removal + Konzept-Cleanup; aktuell koexistieren beide Pfade).
- [x] **T6 — Unit-Tests:** 13 Tests in `tests/test_schema_promoter.py` — Drift-Guard, alle 4 Skip-Branches in `_meets_criteria`, `find_schema_promotion_candidates` Filter, `promote_schema_to_shared` (new-id+source-stamp / qdrant-centroid / qdrant-fail-best-effort), `promote_schemas_batch` (eligible-only / per-schema-failure / qdrant-centroid-fetch).
