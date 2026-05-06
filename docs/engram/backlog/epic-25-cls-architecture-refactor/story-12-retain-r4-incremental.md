# Story 12 — Retain R4 incremental Schema-Fit-Check

## User Story

Als Retain-Pipeline soll ich bei jedem neuen Engram prüfen, ob es zu einem existierenden Schema passt (Cosine ≥ 0.85 zwischen Engram-Embedding und Schema-Centroid), damit Schemas sofort verstärkt werden — ohne auf den nächsten C2-Lauf zu warten.

## Kontext

R4 ("Reinforcement/Growth") läuft sowohl batch in C2 (Story 10) als auch incremental hier beim Retain. Bio-Vorbild: Tse et al. (2007) zeigen, dass schema-konsistente Erinnerungen in Stunden konsolidieren statt Wochen — das modellieren wir als unmittelbare Schema-Verstärkung beim Retain. Der neue Engram landet **trotzdem** im Buffer (PostgreSQL); zusätzlich wird das matchende Schema verstärkt.

## Bestehende Codebasis

- **Retain Pipeline:** `engine/retain/retain_orchestrator.py`.
- **Schema Match:** `engine/consolidation/c2_schema_match.py::match_existing_schema()` (aus Story 06) — wiederverwendbar.
- **Schema Reinforcement:** `engine/consolidation/c2_schema_writer.py::reinforce_schema()` (aus Story 10) — wiederverwendbar, aber für Single-Engram-Update geeignet machen.

## Akzeptanzkriterien

- [ ] Neue Funktion `incremental_schema_fit(engram) -> Optional[Schema]` als Hook in der Retain-Pipeline (nach Engram-Persistierung)
- [ ] Single-Engram-Variante von `reinforce_schema()`: `evidence_count += 1`, Top-N-Update wenn neuer Engram stärker, Centroid laufender Mittelwert mit Gewicht 1, last_reinforced_at = now
- [ ] Property-Refresh nicht nötig (single Engram ändert Aggregation kaum) — optional über Konstante `R4_INCREMENTAL_PROPERTY_REFRESH = false`
- [ ] Match-Threshold identisch zu C2 (Cosine ≥ 0.85)
- [ ] Logging: pro Retain wird vermerkt, ob ein Schema verstärkt wurde
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Single-Engram-Reinforce-Variante:** `reinforce_schema_single_engram(schema, engram) -> Schema` in `c2_schema_writer.py`.
- [ ] **T2 — Hook in Retain-Pipeline:** Nach `persist_engram()` in `retain_orchestrator.py` → `incremental_schema_fit(engram)`. Idempotent (nur ein Schema kann pro Engram getroffen werden — der mit höchster Cosine).
- [ ] **T3 — Konstante:** `R4_INCREMENTAL_ENABLED = true` in `constants.py` als Feature-Flag.
- [ ] **T4 — Property-Refresh-Konstante:** `R4_INCREMENTAL_PROPERTY_REFRESH = false` (Default — billiger ohne Refresh; Refresh kommt im nächsten C2-Lauf).
- [ ] **T5 — Unit-Tests:** (a) Engram passt zu Schema → Schema wird verstärkt. (b) Engram passt zu nichts → Hook ist No-Op. (c) Engram passt zu zwei Schemas → das mit höherer Cosine wird verstärkt.
