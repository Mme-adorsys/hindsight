# Story 18 — Cleanup alte ncr_decay/ncr_strengthen/schema_processor

## User Story

Als Codebasis soll ich die alten Module der 5-Phasen-Pipeline (`ncr_decay.py`, `ncr_strengthen.py`, `schema_processor.py`) entfernen, damit nur noch die neue 3-Phasen-Architektur (C1, C2, C3) im Code lebt — kein toter Code, keine zwei parallelen Wahrheiten.

## Kontext

Die alte Architektur ist durch die Stories 01–17 ersetzt. Wir entfernen jetzt die alten Module, damit der Code-State eindeutig ist. Die Logik wandert in `consolidation1.py` (C1, leicht angepasst), `c2_*.py` (Pattern Recognition + Decay-Re-Eval), `c3_schema_restructure.py` (R3 + R5).

## Bestehende Codebasis

- **`engine/consolidation/ncr_decay.py`:** alte C2a-Logik — entfernen (Decay-Re-Eval ist jetzt in `c2_decay.py`).
- **`engine/consolidation/ncr_strengthen.py`:** alte C2b-Logik (buffer→neocortex Promotion) — entfernen (gibt's nicht mehr).
- **`engine/consolidation/schema_processor.py`:** alte Schema-Compression-Hook — entfernen (R1+R2 in C2, R3+R5 in C3).
- **`engine/consolidation/ncr_orchestrator.py`:** umbauen auf 3-Phasen-Schema mit Aufrufen an `c1`, `c2`, `c3`.
- **Tests:** alte Tests anpassen oder löschen.

## Akzeptanzkriterien

- [x] 7 Legacy-Module gelöscht: `ncr_decay.py`, `ncr_strengthen.py`, `schema_processor.py`, `engram_schema_processor.py`, `schema_clustering.py`, `schema_maturation.py`, `schema_competition.py` (~1700 LOC). Die letzten drei waren nur über `engram_schema_processor` erreichbar — toter Code ohne Caller.
- [x] Alle Importe weg; `ruff check hindsight_api/` ist clean.
- [x] `ncr_orchestrator.py` umgebaut: NCROrchestrator komponiert `consolidation1.run` (C1), `run_c2_phase` (R1+R2+R4 + Decay), `run_c3_phase` (R3 + R5) und optional `promote_batch` (Shared). Keine DecayProcessor/StrengthenProcessor/SchemaProcessor-Klassen mehr.
- [x] API-Endpoint `POST /v1/default/banks/{bank_id}/ncr/trigger?phase=...` akzeptiert nur `c1, c2, c3, shared` (Doku auf neue Semantik aktualisiert: C2 = Pattern Recognition + Decay, C3 = R3 Hyper-Schema + R5 Schema Death).
- [x] Alte Migrations + DB-Schema bleiben unverändert; `strengthen_stats` jsonb-Spalte wird permanent NULL geschrieben (Story-18-Invariante als Comment dokumentiert).
- [x] `engine/consolidation/__init__.py` Doc-String auf 3-Phasen-Struktur umgestellt; kein README-File vorhanden.
- [x] 118 Tests grün (orchestrator + alle C2/C3/Recall-Tests). 1700 LOC Legacy-Tests gelöscht (test_ncr_schema_hook + test_ncr_scoring_dispatch + 4 schema_*-Tests).

## Tasks

- [x] **T1 — Code löschen:** `git rm` der 7 Module + 6 zugehöriger Test-Files (test_ncr_orchestrator.py wurde stattdessen rebuilt).
- [x] **T2 — Importe bereinigen:** `api/http.py` entfernt 4 Imports (DecayProcessor/StrengthenProcessor/EngramSchemaProcessor + die Wiring-Block). Wiring auf neue NCROrchestrator-Signatur (qdrant_client/neo4j_client/description_llm_caller) umgestellt; description_llm_caller bleibt vorerst None (template-Fallback aus Story 08).
- [x] **T3 — Orchestrator umbauen:** Zwei neue Top-Level-Composer `run_c2_phase` (R1 detect → R2 mature → R4 partition+reinforce/persist + decay_reevaluate_buffer) und `run_c3_phase` (R3 + R5). C2Report/C3Report dataclasses bündeln Sub-Reports. Persist-Mapping: legacy `decay_stats`-Spalte trägt das C2-Bundle, `schema_stats` das C3-Bundle, `strengthen_stats` permanent NULL.
- [x] **T4 — API-Endpoints:** Phase-Enum war bereits `{c1, c2, c3, shared}` — kein `c2a`/`c2b` zu strippen. Endpoint-Beschreibung auf neue Semantik (Pattern Recognition + Decay / R3 + R5) aktualisiert. Unbekannte Phasen werfen 400.
- [x] **T5 — Tests bereinigen:** `test_ncr_orchestrator.py` neu geschrieben (15 Tests, Composer-Mocks via patch). Sechs Legacy-Test-Files gelöscht — die neuen R1–R5-Tests leben in `test_c2_pattern_recognition.py` / `test_c2_schema_writer.py` / `test_c2_decay.py` / `test_c3_schema_restructure.py` (Stories 04–14).
- [x] **T6 — Doku:** `engine/consolidation/__init__.py` umgeschrieben auf 3-Phasen-Modulübersicht (consolidation1, c2_pattern_recognition, c2_schema_writer, c2_decay, c3_schema_restructure, ncr_orchestrator).
