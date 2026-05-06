# Story 09 — C2 Schema-Persistierung (Neo4j + Qdrant)

## User Story

Als C2-Phase soll ich neue Schemas atomar in Neo4j (Knoten) und Qdrant (Centroid) persistieren, damit die drei Repräsentationen (Centroid + Description + Properties) konsistent zur selben Schema-ID liegen.

## Kontext

Wenn ein Cluster nicht gematcht hat (Story 06 Creation-Pfad) und Properties + Description berechnet sind (Stories 07, 08), wird ein neuer `:Schema`-Knoten in Neo4j angelegt und sein Centroid in Qdrant geschrieben. Die `evidence_engram_ids` werden auf die Top-N stärksten Cluster-Mitglieder gesetzt (Default N=5, siehe Konstante).

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::create_schema(...)` (aus Story 01).
- **Qdrant Centroid:** `engine/qdrant_client.py::upsert_schema_centroid(schema_id, centroid, payload)` (aus Story 03).
- **Cluster-Kandidaten:** `MaturedClusterCandidate` mit `engram_ids` (alle Member-IDs).

## Akzeptanzkriterien

- [ ] Neue Funktion `persist_new_schema(cluster, properties, description, centroid) -> Schema`
- [ ] Top-N Auswahl: aus den Cluster-Engrams die N=5 mit höchstem Composite-Score als `evidence_engram_ids`
- [ ] Schema-Knoten in Neo4j angelegt mit allen Feldern (id, description, properties, evidence_engram_ids, evidence_count, centroid_qdrant_id, created_at=now, last_reinforced_at=now, cycles_survived=1, status="active")
- [ ] Centroid in Qdrant geschrieben mit `payload = {kind: "schema", schema_id, bank_id, description_short}`
- [ ] Atomarität: Bei Qdrant-Failure → Neo4j-Rollback (Saga-Pattern oder explizites Cleanup)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Top-N Selector:** Helper `select_top_n_evidence(engrams, n=5) -> list[UUID]` sortiert nach Composite-Score (`thalamus_overall × decay`) und nimmt die Top-N.
- [ ] **T2 — `persist_new_schema()`:** In `engine/consolidation/c2_schema_writer.py`. Schritte: schema_id generieren, Top-N auswählen, Schema-Pydantic-Modell bauen, `create_schema()` rufen, `upsert_schema_centroid()` rufen.
- [ ] **T3 — Atomarität:** Bei Failure nach Neo4j-Insert aber vor Qdrant-Upsert → Try/Except mit Cleanup-Aufruf `archive_schema(id)` als Fallback.
- [ ] **T4 — Konstante:** `SCHEMA_TOP_N_EVIDENCE = 5` in `engine/consolidation/constants.py`.
- [ ] **T5 — Pipeline-Integration:** In `c2_pattern_recognition.py` Creation-Pfad ruft `persist_new_schema()`.
- [ ] **T6 — Unit-Tests:** (a) Happy-Path → Schema-Knoten + Centroid erscheinen. (b) Qdrant-Failure → Neo4j-Knoten archived (kein Waisenkind). (c) Top-N-Auswahl korrekt sortiert.
