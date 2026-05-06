# Story 14 — C3 Schema Death (R5)

## User Story

Als C3-Phase soll ich Schemas, die seit ≥ K Zyklen nicht mehr verstärkt wurden und unter einem Evidence-Threshold liegen, als `archived` markieren — damit der Schema-Graph nicht unbegrenzt wächst (synaptic homeostasis im Cortex).

## Kontext

R5 ("Competition/Death") verhindert Schema-Inflation. Schemas, die einmal entstanden sind aber nie wieder verstärkt werden, sind wahrscheinlich Rauschen oder veraltetes Wissen. Sie werden nicht gelöscht — sie bleiben für historische Recalls verfügbar — aber als `status='archived'` markiert und aus aktiven Recall-Suchen ausgeschlossen.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::archive_schema(id)` (aus Story 01).
- **Schema-Felder:** `last_reinforced_at`, `evidence_count`, `cycles_survived` (aus Story 01).

## Akzeptanzkriterien

- [ ] In `engine/consolidation/c3_schema_restructure.py` neue Funktion `archive_dead_schemas(bank_id) -> ArchiveReport`
- [ ] Bedingung: `cycles_since_last_reinforced > K` UND `evidence_count < EVIDENCE_THRESHOLD` (beide müssen erfüllt sein)
- [ ] Default-Konstanten: `K=4`, `EVIDENCE_THRESHOLD=5` (konfigurierbar via constants.py)
- [ ] `cycles_since_last_reinforced` berechenbar aus C3-Zyklen (jeder C3-Lauf inkrementiert einen Counter, den `last_reinforced_at` zurücksetzt)
- [ ] Status-Update auf `archived` (nicht löschen)
- [ ] Logging: pro C3-Lauf gezählt
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `archive_dead_schemas()`:** In `c3_schema_restructure.py`. Listet alle aktiven Schemas, prüft Bedingung, ruft `archive_schema()`.
- [ ] **T2 — `cycles_since_last_reinforced` Berechnung:** Helper, der die C3-Zyklen seit `last_reinforced_at` zählt. Persistiert pro Schema oder zur Laufzeit aus C3-Zyklus-Tabelle abgeleitet.
- [ ] **T3 — Konstanten:** `R5_K_CYCLES = 4`, `R5_EVIDENCE_THRESHOLD = 5` in `constants.py`.
- [ ] **T4 — Pipeline-Integration:** In `c3_orchestrator.py` nach R3 → R5.
- [ ] **T5 — Unit-Tests:** (a) Schema mit `last_reinforced_at` 4 Zyklen alt + evidence_count=3 → archived. (b) Schema 4 Zyklen alt aber evidence_count=20 → bleibt aktiv. (c) Schema 1 Zyklus alt + evidence_count=3 → bleibt aktiv. (d) Archived Schemas erscheinen nicht in `list_active_schemas()`.
