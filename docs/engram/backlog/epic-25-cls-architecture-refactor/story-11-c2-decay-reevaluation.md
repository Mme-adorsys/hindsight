# Story 11 — C2 Decay-Re-Evaluation Buffer-Engrams

## User Story

Als C2-Phase soll ich nach jedem Lauf den Composite-Score aller aktiven Buffer-Engrams neu berechnen und Engrams mit Composite < 0.05 archivieren, damit die ehemalige separate C2a-Phase entfällt und Buffer-Engrams allein durch Zeit veralten — ohne aktives Rauskicken durch Schemas.

## Kontext

In der alten Architektur lief C2a (Decay) und C2b (Strengthen) als getrennte Phasen. In der neuen Architektur fasst C2 beides zusammen: nach Pattern Recognition wird einmal `bank.session_counter += 1` durchgeführt, alle aktiven Buffer-Engrams werden re-evaluiert (Composite-Recompute) und Engrams unter Schwelle landen in `archived`. Engrams werden **nicht** durch Schema-Erzeugung entfernt — sie altern allein durch Zeit (und Composite-Score).

## Bestehende Codebasis

- **Composite Score:** `engine/consolidation/scoring.py::compute_composite(engram, bank)` (aus Epic 24).
- **Bank Session Counter:** `bank.session_count` (aus Epic 24 Story 01).
- **Engram Repository:** `engram_repository.py::list_active(layer="buffer", bank_id)`, `archive_engram(id)`.

## Akzeptanzkriterien

- [ ] Neue Funktion `decay_reevaluate_buffer(bank_id) -> DecayReport` als Bestandteil des C2-Flows
- [ ] Schritte:
  1. Atomarer SQL-UPDATE: `bank.session_count += 1`
  2. Alle aktiven Buffer-Engrams listen
  3. Composite-Score neu berechnen (`sessions_alive` derived aus `bank.session_count - engram.created_at_session`)
  4. Composite < 0.05 → `status='archived'`, `archived_at=now`
- [ ] DecayReport: `{ total: int, archived: int, retained: int }`
- [ ] Idempotenz: Wenn C2 zweimal hintereinander läuft (Bug-Szenario), wird `session_count` nur einmal pro Lauf inkrementiert (Lock-Mechanismus)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `decay_reevaluate_buffer()`:** In `engine/consolidation/c2_decay.py` (neue Datei). Schritte aus Akzeptanzkriterien.
- [ ] **T2 — Session-Counter-Lock:** Advisory-Lock (`pg_try_advisory_lock(bank_id_hash)`) während des Re-Evaluations-Flows, damit doppelte C2-Läufe nicht doppelt inkrementieren.
- [ ] **T3 — Threshold-Konstante:** `BUFFER_ARCHIVE_COMPOSITE_THRESHOLD = 0.05` in `constants.py`.
- [ ] **T4 — Pipeline-Integration:** In `c2_pattern_recognition.py` (oder neuem `c2_orchestrator.py`) nach Pattern-Recognition + Persistierung → `decay_reevaluate_buffer()` aufrufen.
- [ ] **T5 — Unit-Tests:** (a) Engram mit hohem Composite bleibt aktiv. (b) Engram mit altem Datum + niedrigem Access-Count → Composite fällt < 0.05 → archived. (c) Doppelter C2-Run → session_count inkrementiert nur einmal pro Lauf.
