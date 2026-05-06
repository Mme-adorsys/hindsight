# Story 10 — C2 Schema-Reinforcement (R4 batch)

## User Story

Als C2-Phase soll ich bei einem Schema-Match (aus Story 06) das bestehende Schema verstärken — `evidence_count` erhöhen, Top-N aktualisieren, Centroid neu mitteln, Properties verfeinern, `last_reinforced_at` setzen — ohne ein neues Schema anzulegen.

## Kontext

R4 ("Reinforcement/Growth") läuft sowohl batch in C2 (für Cluster-Treffer) als auch incremental beim Retain (Story 12). Diese Story implementiert nur den batch-Pfad: ein gereifter Cluster matcht ein bestehendes Schema → das Schema absorbiert die neuen Engrams als Evidence.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::update_schema(id, props)` (aus Story 01).
- **Qdrant Centroid:** Update via `upsert_schema_centroid()` (Idempotent, aus Story 03).
- **Property Aggregator:** wiederverwendbar — wird mit aktuellem Evidence-Set + neuen Engrams gefüttert.

## Akzeptanzkriterien

- [ ] Neue Funktion `reinforce_schema(schema, cluster) -> Schema`
- [ ] Schritte:
  1. `evidence_count += len(cluster.engram_ids)`
  2. Top-N-Liste neu berechnen: alte Top-N + neue Cluster-Engrams → wieder Top-N nach Composite-Score
  3. Neuer Centroid: laufender Mittelwert über alte (gewichtet mit altem evidence_count) + neue (gewichtet mit cluster_size)
  4. Properties verfeinern: `aggregate_properties(top_n_engrams_post_update)` (Refresh anhand der aktuellen Top-N)
  5. `last_reinforced_at = now`
  6. `cycles_survived++` (optional — wenn der Schema-Status getrackt werden soll)
- [ ] Persistierung: Neo4j-Update + Qdrant-Centroid-Upsert (atomar)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `reinforce_schema()`:** In `engine/consolidation/c2_schema_writer.py`.
- [ ] **T2 — Centroid-Mittelwert:** Helper `weighted_centroid(old_centroid, old_count, new_centroid, new_count) -> Vector` (laufender Mittelwert).
- [ ] **T3 — Property-Refresh:** Nach Top-N-Update werden die Properties neu aggregiert — über den **aktuellen** Top-N-Engram-Satz, nicht historisch.
- [ ] **T4 — Atomarität:** Wie Story 09 — Try/Except mit Rollback-Pfad bei Qdrant-Failure.
- [ ] **T5 — Pipeline-Integration:** In `c2_pattern_recognition.py` Reinforcement-Pfad ruft `reinforce_schema()`.
- [ ] **T6 — Unit-Tests:** (a) Bestehendes Schema mit 8 Evidence + Cluster mit 3 → evidence_count=11, Top-5 enthält die 5 stärksten aus 11. (b) Centroid bewegt sich in Richtung neuer Cluster, gewichtet. (c) Property-Refresh greift bei dominantem-Wert-Wechsel.
