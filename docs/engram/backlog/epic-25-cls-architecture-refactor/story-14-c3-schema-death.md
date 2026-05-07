# Story 14 — C3 Schema Death (R5)

## User Story

Als C3-Phase soll ich Schemas, die seit ≥ K Zyklen nicht mehr verstärkt wurden und unter einem Evidence-Threshold liegen, als `archived` markieren — damit der Schema-Graph nicht unbegrenzt wächst (synaptic homeostasis im Cortex).

## Kontext

R5 ("Competition/Death") verhindert Schema-Inflation. Schemas, die einmal entstanden sind aber nie wieder verstärkt werden, sind wahrscheinlich Rauschen oder veraltetes Wissen. Sie werden nicht gelöscht — sie bleiben für historische Recalls verfügbar — aber als `status='archived'` markiert und aus aktiven Recall-Suchen ausgeschlossen.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::archive_schema(id)` (aus Story 01).
- **Schema-Felder:** `last_reinforced_at`, `evidence_count`, `cycles_survived` (aus Story 01).

## Akzeptanzkriterien

- [x] `archive_dead_schemas(bank_id, *, neo4j, k_cycles, evidence_threshold, ...) -> R5Report` in `c3_schema_restructure.py`
- [x] Bedingung: `cycles_since_reinforced > K` UND `evidence_count < EVIDENCE_THRESHOLD` (AND-Gate)
- [x] **Bootstrap-Defaults** (User-Entscheidung): `R5_K_CYCLES = 8` und `R5_EVIDENCE_THRESHOLD = 3` — entspannter als Concept-Default (K=4, threshold=5), damit junge Banken keine selten-getriggerten Schemas verlieren. Concept-Default als Comment dokumentiert.
- [x] `cycles_since_reinforced` aus `(now - last_reinforced_at).days // C3_CYCLE_PERIOD_DAYS` berechnet (concept §13: C3 läuft alle 7 Tage)
- [x] Status-Update via `archive_schema` (kein DELETE — historische Recalls bleiben möglich)
- [x] R5Report mit bank_id, schemas_scanned, archived count, archived_ids
- [x] 11 neue Unit-Tests; Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — `archive_dead_schemas`:** Listet aktive `:Schema`-Knoten, filtert per Doppelgate, ruft `archive_schema(neo4j, schema.id, label="Schema")`. Per-Schema-Failures werden geloggt aber brechen den Lauf nicht ab (best-effort).
- [x] **T2 — `cycles_since_reinforced(schema, now, cycle_period_days)` Helper:** Wallclock-basiert (delta.days // cycle_period). Schema ohne `last_reinforced_at` → 0 Zyklen.
- [x] **T3 — Konstanten** in `constants.py`: `R5_K_CYCLES = 8`, `R5_EVIDENCE_THRESHOLD = 3`, `C3_CYCLE_PERIOD_DAYS = 7`. Concept-Default (K=4, threshold=5) als Comment-Doku.
- [x] **T4 — Pipeline-Integration:** Im selben `c3_schema_restructure.py`-Modul aufrufbar; ein dedizierter `c3_orchestrator` (R3 → R5) folgt mit Block-D-Anbindung.
- [x] **T5 — Unit-Tests:** 11 Tests in `tests/test_c3_schema_restructure.py`: 1 Drift-Guard (Bootstrap-Defaults < concept), 4 cycles_since_reinforced (0, 7d, 56d, no-last_reinforced), 6 archive_dead_schemas (dead → archived, evidence-protected, fresh-protected, per-schema-failure, concept-default-override, empty bank).

## Implementation Notes

- **Bootstrap-Konservativ:** Default K=8 (≈ 56 Tage) statt §13-Default K=4 (≈ 28 Tage); Default threshold=3 statt 5. Ein junges System mit dünnen Banken verliert sonst Schemas nur weil sie im Bootstrap-Stadium selten getriggert werden. Override per Funktions-Argument möglich; Test `test_explicit_overrides_match_concept_default` pinnt das.
- **Wallclock vs. Cycle-Counter:** Story-Spec sah einen separaten C3-Cycle-Counter pro Schema vor. Wir leiten cycles aus `last_reinforced_at` ab — billiger, kein zusätzliches DB-Feld, und der Schema-Lifecycle ist inhärent wallclock-getrieben (concept §13: C3 alle 7 Tage). `now`-Override im Helper macht Tests deterministisch.
- **No-DELETE:** R5 archiviert; tatsächliches Löschen ist nicht Teil dieses Refactors. HybridRetriever (Story 15) filtert auf `status='active'`.
- **Per-Schema-Failure swallowed:** Wenn `archive_schema` für ein einzelnes Schema fehlschlägt, läuft der Batch weiter — best-effort C3-Semantik konsistent mit Block B.
