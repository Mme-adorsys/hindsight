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

- [x] `reconsolidate_schema_hit(hit, ...)` als Entry-Point erkennt Schema-Hits (`hit.kind == "schema"`); Engram-Hits → No-Op `None`-Return.
- [x] Mode-abhängige Stufen:
  - **Precision:** `access_count++`, `last_accessed=now` (touch-only)
  - **Exploration:** + Property-Refresh aus übergebenen Top-N Evidence (deterministische `aggregate_properties` Wiederverwendung).
  - **Analogy:** + Logging-Hint für nächsten R3-Sweep (kein zusätzlicher persistenter State).
  - **Validation + prediction_error=True:** + Centroid-Drift via `drift_centroid` (α-Default = 0.05).
- [x] `drift_centroid(old, query, alpha)` Helper: `(1-α)·old + α·query` + L2-Renorm; ValueError bei Dim-Mismatch / α∉[0,1].
- [x] Schema-Modell um `access_count: int = 0` und `last_accessed: datetime | None` erweitert; `to_neo4j_props`/`from_neo4j_props` und `create_schema`-Cypher entsprechend ergänzt. Alembic-Migration nicht nötig — Schemas leben in Neo4j, properties sind dort dynamisch.
- [x] `SCHEMA_CENTROID_DRIFT_ALPHA = 0.05` in `engine/consolidation/constants.py` mit Drift-Guard im Test.
- [x] 14 Unit-Tests grün; Integration-Test verschoben auf Block E (Story 19/20 hat den Recall-Reconsolidation-Pfad als Coffee-Meeting-E2E ohnehin schon im Smoke-Test).

## Tasks

- [x] **T1 — Schema-Modell erweitern:** `_SchemaBase` bekommt `access_count` (default 0) + `last_accessed` (default None); Cypher-MERGE in `create_schema` setzt beide Felder.
- [x] **T2 — Schema-Reconsolidation-Branch:** Neues Modul `engine/reflect/schema_reconsolidation.py::reconsolidate_schema_hit` (statt Erweiterung des bestehenden `reflect_orchestrator._reconsolidate_engrams_async` — Schema-Pfad ist disjunkt vom Engram-Queue-Loop und braucht andere Inputs). Best-effort: Per-hit-Failures werden geloggt, Recall-Pfad kracht nicht.
- [x] **T3 — Property-Refresh-Helper:** Inline `_refresh_properties(evidence)` ruft `aggregate_properties` auf den `EvidenceEngram.tags`-Listen; persistiert über `update_schema(properties_json=...)`.
- [x] **T4 — Centroid-Drift:** `drift_centroid` Modul-level; Validation-Branch lädt aktuellen Centroid via `qdrant.get_by_id`, drift, schreibt zurück per `qdrant.upsert_schema_centroid`. Per-Step-Failures geloggt.
- [x] **T5 — Konstante:** `SCHEMA_CENTROID_DRIFT_ALPHA = 0.05` mit Test-Drift-Guard.
- [x] **T6 — Unit-Tests:** 14 Tests in `tests/test_schema_reconsolidation.py` — 7×`drift_centroid` (alpha-Endpunkte, Norm-Erhalt, Dim-Mismatch, Drift-Guard) + 7×`reconsolidate_schema_hit` (engram-no-op, Precision-touch, Exploration-property-refresh, Validation-PE-drift, Validation-no-PE-skip, missing-schema, update-failure-best-effort).
