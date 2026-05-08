# Story 22 — Schema Reconsolidation Window & Drift-Tracking

## User Story

Als System soll ich Schema-Centroid-Drifts und Property-Änderungen aus Reconsolidation auditieren können (last_drifted_at, drift_history), damit nachvollziehbar bleibt, wie sich ein Schema über Recalls hinweg verändert hat — analog zum biologischen Reconsolidation-Window.

## Kontext

Reconsolidation kann den Schema-Centroid und die Properties verschieben (Story 21). Das ist gewünscht (Schemas adaptieren sich an neue Realität), aber auch riskant (ein einzelner falscher Recall könnte ein Schema "verbiegen"). Wir brauchen ein Audit-Trail und eine Throttle-Mechanik, damit Drift kontrollierbar bleibt.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01).
- **Reconsolidation-Branch:** aus Story 21.

## Akzeptanzkriterien

- [x] `_SchemaBase` um `drift_count: int = 0` und `last_drifted_at: datetime | None` erweitert; `to_neo4j_props`/`from_neo4j_props` und `create_schema`-Cypher entsprechend aktualisiert.
- [x] Pro erfolgreich gefeuertem Drift-Event: `drift_count++` und `last_drifted_at=now` (über die rollende 24h-Window-Logik in `_throttle_check`).
- [x] Throttle: `MAX_SCHEMA_DRIFTS_PER_DAY = 5` (rollender 24h-Reset — kein cron-Job nötig). Bei Überschreitung wird Qdrant nicht angefasst und kein Audit-Row geschrieben; access_count/last_accessed werden trotzdem aktualisiert.
- [x] Audit-Tabelle `schema_drift_events {id, bank_id, schema_id, alpha, query_hash, mode, occurred_at}` via Alembic-Migration `e25s22drift` (down_revision=e25c2fingerprint).
- [x] C2 Reset: `reinforce_schema` und `reinforce_schema_single_engram` schreiben `drift_count=0`/`last_drifted_at=None` in den frisch konstruierten SchemaModel — frische Evidenz öffnet ein neues Drift-Budget. Gleichzeitig werden `access_count`/`last_accessed` aus Story 21 jetzt explizit über den Reinforce-Pfad getragen (war vorher implizit defaulting zu 0).
- [x] 22 Unit-Tests insgesamt (14 Story 21 + 8 Story 22): _throttle_check Boundaries, drift-fires-audit, throttle-blocks-qdrant-und-audit, 24h-rolling-reset.

## Tasks

- [x] **T1 — Schema-Modell erweitern:** Neue Felder `drift_count`/`last_drifted_at` auf `_SchemaBase`; Cypher in `create_schema` ergänzt. Keine Alembic-Migration nötig — Schemas leben in Neo4j (Properties dynamisch).
- [x] **T2 — Drift-Events-Tabelle:** Alembic-Migration `e25s22drift_schema_drift_events.py` mit Bank-FK + 3 Indizes (`bank_id`, `schema_id`, recent on `(schema_id, occurred_at DESC)`).
- [x] **T3 — Throttle-Logic:** `_throttle_check(schema, now)` (rollender 24h-Reset → drop Counter falls last_drifted_at > 24h alt) eingebaut in `reconsolidate_schema_hit` Validation-Branch. Throttled = log-only, keine Qdrant-/DB-Schreibvorgänge.
- [x] **T4 — Reset-Hook:** In `c2_schema_writer.reinforce_schema` und `reinforce_schema_single_engram` resettet der frisch konstruierte SchemaModel `drift_count`/`last_drifted_at` explizit (zusätzlich Story-21-Carry-over für `access_count`/`last_accessed`).
- [x] **T5 — Konstante:** `MAX_SCHEMA_DRIFTS_PER_DAY = 5` in `engine/consolidation/constants.py` mit Bio-Begründung (~15° max Drift/Tag bei α=0.05).
- [x] **T6 — Unit-Tests:** 8 neue Tests in `tests/test_schema_reconsolidation.py` plus die 14 aus Story 21 — TestThrottleCheck (4), `test_validation_drift_persists_audit_row`, `test_throttled_drift_skips_qdrant_and_audit`, `test_drift_after_24h_window_rolls_counter`. Reinforce-Reset-Path durch bestehende `test_c2_schema_writer.py`-Suite mit abgedeckt (alle 53 Tests grün).
