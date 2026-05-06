# Story 21 — Reconsolidation auf Schema-Hits

## User Story

Als Recall-Pipeline soll ich bei Schema-Treffern eine angepasste Reconsolidation auslösen — Property-Refresh + Centroid-Drift + access_count am Schema — analog zur bestehenden Engram-Reconsolidation, damit Schemas durch Recall genauso labil/anpassungsfähig sind wie Engrams.

## Kontext

Die bestehende Reconsolidation-Pipeline (Epic 10) operiert auf Engrams: bei Recall mit Cosine ≥ 0.6 wird der Engram modifiziert (access_count, Tag-Updates, Strength-Adjustment). In der neuen Architektur sind Schema-Hits gleichwertig mit Engram-Hits — sie müssen ebenfalls bei Recall reconsolidiert werden können. Bio-Vorbild: jeder Recall reaktiviert das Schema-Aktivierungsmuster und macht es kurzzeitig veränderbar (Reconsolidation Window).

## Bestehende Codebasis

- **Reconsolidation Pipeline:** `engine/reflect/reconsolidation_orchestrator.py` (aus Epic 10).
- **Mode-spezifische Reconsolidation-Level:** `engine/reflect/reconsolidation_levels.py` (minimal/moderate/aggressive/schema_update).
- **Schema Repository:** `engine/schema/schema_repository.py::update_schema()` (aus Story 01).
- **HybridRetriever:** liefert RetrievalHits mit `kind ∈ {"engram", "schema"}` (aus Story 15).

## Akzeptanzkriterien

- [ ] Reconsolidation-Orchestrator erkennt Schema-Hits (`hit.kind == "schema"`)
- [ ] Schema-Reconsolidation-Level (mode-abhängig):
  - **Precision:** nur `access_count++`, `last_accessed=now`
  - **Exploration:** access_count + Property-Refresh aus aktuellen Top-N Evidence-Engrams
  - **Analogy:** access_count + ggf. Hyper-Schema-Linking-Hint
  - **Validation:** access_count + Centroid-Drift bei Prediction Error
- [ ] Centroid-Drift: bei Validation-Mode mit Prediction Error wird der Schema-Centroid leicht in Richtung Query-Embedding gezogen (Faktor ≤ 0.05, damit ein einzelner Recall keine starke Verschiebung macht)
- [ ] `access_count` neue Spalte am Schema-Knoten ergänzen (falls nicht in Story 01 schon drin)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Schema-Modell erweitern:** Falls nicht in Story 01: `access_count: Integer = 0`, `last_accessed: Timestamp` als Felder am `:Schema`-Knoten ergänzen. Alembic + Cypher-Migration.
- [ ] **T2 — Schema-Reconsolidation-Branch:** In `reconsolidation_orchestrator.py` neuen Branch für `hit.kind == "schema"` mit eigener Logik je Mode.
- [ ] **T3 — Property-Refresh-Helper:** Wiederverwendung von `aggregate_properties()` mit aktuellem Top-N Evidence-Set. Update via `schema_repository.update_schema()`.
- [ ] **T4 — Centroid-Drift:** Helper `drift_centroid(old_centroid, query_embedding, alpha=0.05) -> Vector` mit normalisierter Verschiebung.
- [ ] **T5 — Konstante:** `SCHEMA_CENTROID_DRIFT_ALPHA = 0.05` in `constants.py`.
- [ ] **T6 — Unit-Tests:** (a) Schema-Hit in Precision → nur access_count++. (b) Schema-Hit in Exploration → Properties refreshed wenn Top-N stabil. (c) Schema-Hit in Validation mit Prediction Error → Centroid driftet leicht. (d) Engram-Hit unbeeinflusst von neuer Logik.
