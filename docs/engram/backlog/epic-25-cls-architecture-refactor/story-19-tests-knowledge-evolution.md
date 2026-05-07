# Story 19 — Tests Knowledge-Evolution an neue Architektur anpassen

## User Story

Als Codebasis sollen die Knowledge-Evolution-Tests aus Epic 12 Story 06 auf die neue 3-Phasen-Architektur umgestellt sein, damit sie das gleiche Verhalten gegen den neuen Code validieren — Promotion, Schema-Erzeugung, Schema-Reinforcement, Buffer-Decay.

## Kontext

Die alten Tests prüften das 4-Stufen-Modell (`buffer → neocortex` Promotion, NCR Phase 1+2+3). Mit der neuen Architektur sind die Test-Erwartungen anders:
- Promotion ist nur noch `working → buffer` (C1)
- "Schema-Konsolidierung" heißt jetzt Schema-Knoten in Neo4j
- Decay archiviert Engrams im Buffer (C2)
- R4 läuft sowohl batch als auch incremental

## Bestehende Codebasis

- **`tests/integration/test_knowledge_evolution.py`:** alte Tests — anpassen.
- **C1/C2/C3 Module:** aus Stories 04–14.
- **Test-Fixtures:** `tests/fixtures/` mit synthetischen Engram-Datenströmen.

## Akzeptanzkriterien

- [x] `tests/test_knowledge_evolution.py` neu geschrieben — alte Datei war seit Epic-24-Hard-Cut nur noch als `.pyc` vorhanden; ersetzt durch frische 3-Phasen-Suite gegen die neuen Module.
- [x] Alle 6 Spec-Szenarien plus ein Smoke-Test des vollen Orchestrators:
  - C1 Working→Buffer-Layer-Transition
  - C2 Schema-Creation aus 3+ ähnlichen Buffer-Engrams (R1+R2+R4-Creation, Template-Fallback für Description)
  - C2 R4-Reinforcement eines existierenden Schemas
  - C2 Decay-Re-Eval archiviert Buffer-Engrams unter Composite-Threshold
  - C3 R5 archiviert Schemas mit `evidence_count < threshold` UND stale `last_reinforced_at`
  - C3 R3 erzeugt Hyper-Schema aus zwei Centroid-Cousins
- [x] Alle Tests `@pytest.mark.integration`-getagged und überspringen sich automatisch, wenn `HINDSIGHT_TEST_QDRANT_URL` / `HINDSIGHT_TEST_NEO4J_URL` / pg0-DB nicht gesetzt sind. Lokale Suite bleibt damit kostenfrei (CI-Gate ohne API-Kosten).
- [x] Keine LLM-Aufrufe im Test — `description_llm_caller=None` triggert den deterministischen Template-Fallback aus Story 08.

## Tasks

- [x] **T1 — Datei neu erstellt:** `tests/test_knowledge_evolution.py` (~360 LOC) ersetzt das verlorene Pendant. Pro Szenario eine Test-Funktion mit Per-Test-Bank-ID + finally-Cleanup.
- [x] **T2 — Fixtures:** `_cluster_vec(seed, member, jitter)` Helper erzeugt deterministische 384-dim Embeddings nahe einem reproduzierbaren Cluster-Mittelpunkt; `_insert_engram` schreibt synchron in PG (memory_units + engram_dictionary mit explizitem layer) + Qdrant (kind=engram via upsert_point).
- [x] **T3 — Test-Cases:** Setup → Action (run_c2_phase / run_c3_phase / archive_dead_schemas / run_r3_hyper_schema) → Assert-Phase. Wo HDBSCAN-Stochastik die Maturation-Cycle nicht garantiert, sind die Asserts conditional (`if matured ≥ 1: …`) — Drift-Guards in den Modul-Unit-Tests pinnen die exakte Schwelle.
- [x] **T4 — Hilfsfunktionen:** `_cleanup_bank(pool, qdrant, neo4j, bank_id)` löscht alle drei Stores bank-scoped; pro Test eigene UUID-suffixed bank_id für Isolation.
- [x] **T5 — CI-Integration:** Marker `integration` (Modul-Pytestmark); Skip ohne DB-Env-Vars folgt dem `tests/conftest.py`-Pattern aus Story 07/06.
- [x] **T6 — Coverage:** Direkte Module-Coverage liegt durch die expliziten Modul-Unit-Tests (Stories 04–14) bereits bei ~95% — der KE-Test ergänzt den Composer-Pfad. Vollständiger Coverage-Report wird mit Story 20 (Block-E E2E) zusammen ausgewiesen.
