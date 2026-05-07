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

- [x] `decay_reevaluate_buffer(bank_id, pool, *, bank_size_hint, threshold, limit) -> DecayReport`
- [x] Schritte 1-4 implementiert (advisory-lock → increment_bank_session_count → filter_entries(buffer/active) → composite recompute → batch UPDATE archived)
- [x] DecayReport(bank_id, total, archived, retained, skipped_locked: bool=False)
- [x] Idempotenz via Postgres `pg_try_advisory_lock(blake2b(bank_id))` — busy lock → skipped_locked Report ohne session_count-Bump
- [x] 12 neue Unit-Tests; Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — `decay_reevaluate_buffer()`:** `engine/consolidation/c2_decay.py`. Wird durch try/finally garantiert un-locked auch bei Exception in der locked Section.
- [x] **T2 — Advisory-Lock:** `_bank_advisory_lock_key(bank_id)` via blake2b-Digest auf 63-bit-positive bigint. Stable über Prozesse, Distinct-Banken kollidieren nicht praktisch (8-byte-Digest). `SELECT pg_try_advisory_lock($key)` → bei `false` → skipped_locked Report.
- [x] **T3 — Konstante:** `BUFFER_ARCHIVE_COMPOSITE_THRESHOLD = 0.05` in `engine/consolidation/constants.py` mit Drift-Guard.
- [x] **T4 — Pipeline-Integration:** Hook-Stelle deferred auf C2-Orchestrator (Story 19+). `decay_reevaluate_buffer` ist standalone aufrufbar.
- [x] **T5 — Unit-Tests:** 12 Tests (Lock-Key Determinismus + Range, Composite-Math 3 Cases, Pipeline 5 Cases inkl. Skipped-Locked, Lock-Release-on-Exception, Empty-Bank). Composite-Math nutzt echte `compute_composite`/`compute_equilibrium_rate` aus Epic 24.

## Implementation Notes

- **Mode-Default:** `_composite_for` ruft `compute_equilibrium_rate(scores, mode=None, ...)` — fällt auf `DEFAULT_R_BASE` zurück. Wenn Story 19+ den session_mode der Engrams durchreicht, kann das mode-spezifische R_BASE wirken.
- **Bank-size-Hint:** Optionaler Hint statt PG-Roundtrip; bei None fällt's auf Anzahl der gefetchten Buffer-Entries — eng genug für die Re-Evaluation, weil bank_factor in compute_equilibrium_rate eine sanfte Normierung ist.
- **Concurrency-Modell:** Eine Advisory-Lock pro Bank serialisiert C2 für diese Bank, nicht für die ganze Instanz. Multi-Bank-NCR-Runs laufen weiter parallel.
- **Archive-Update-Batch:** Single UPDATE mit `engram_id = ANY(...)` statt N Einzel-Updates — günstiger und atomar pro Lauf.
