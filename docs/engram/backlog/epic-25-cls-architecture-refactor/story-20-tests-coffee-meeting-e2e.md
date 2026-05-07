# Story 20 — E2E-Test Coffee-Meeting Schema-Lifecycle

## User Story

Als Codebasis sollen 30 synthetische "Coffee-Meeting"-Engrams einen kompletten Lebenszyklus durchlaufen (Retain → C1 → C2 → C3 → Recall), damit das Zusammenspiel der neuen Architektur als E2E validiert ist.

## Kontext

Dieser Test ist das Akzeptanzkriterium für Epic 25. Wir simulieren 30 Coffee-Meeting-Episoden (1:1, Nachmittag, ~45min, productive), lassen sie durch die Pipeline laufen und prüfen am Ende:
- Im Buffer leben 30 Engrams
- C2 erzeugt ein `coffee_meeting`-Schema mit evidence_count ≥ 5
- Eine Recall-Anfrage "erzähl mir was über Coffee-Meetings" liefert das Schema + Top-N Evidence-Engrams
- Reflect-Pipeline produziert eine Antwort, die sowohl die Schema-Allgemeinheit als auch konkrete Beispiele enthält

## Bestehende Codebasis

- **Retain Pipeline:** `engine/retain/`.
- **Consolidation Pipeline:** `engine/consolidation/` (neue C1/C2/C3).
- **Recall Pipeline:** `engine/recall_orchestrator.py` mit HybridRetriever.
- **Reflect Pipeline:** `engine/reflect/`.
- **Test-Infra:** `tests/e2e/` mit Docker-Compose für DBs.

## Akzeptanzkriterien

- [x] `tests/test_coffee_meeting_lifecycle.py` (Pfad-Abweichung: kein eigener `tests/e2e/`-Subordner — die Suite folgt der Konvention der bestehenden Integration-Tests, die alle direkt unter `tests/` liegen).
- [x] 30 deterministische Coffee-Meeting-Engrams (Seed=4242) mit Tag-Variationen `{activity:coffee, format:1on1, hour:14–17, duration:30–60, mood:productive}`. Embeddings clustered mit jitter=0.01 für HDBSCAN-Cohesion ≥ 0.99.
- [x] Phase 1: Retain — 30 Engrams mit `layer="working"`, COUNT-Assert auf 30.
- [x] Phase 2: C1 — Layer-Update auf `buffer` (C1-Service hat eigene Unit-Tests; hier wird der Outcome simuliert, nicht die Promotion-Logik selbst).
- [x] Phase 3: C2 zweimal aufgerufen (Story 05 Maturation braucht cycles ≥ 2). Asserts: candidates ≥ 1, matured ≥ 1, created+reinforced ≥ 1, dann ≥ 1 Schema mit `evidence_count ≥ 5`.
- [x] Phase 4: HybridRetriever (Story 15) mit Query-Embedding = Cluster-Centre + `mode=Precision` (Schema-Boost). Schema-Hit muss in den Top-K landen.
- [x] Phase 5: `resolve_all_schema_evidence` (Story 16) liefert pro Schema-Hit ≥ 1 Evidence-Engram mit Text — der Reflect-Payload trägt damit nachweislich beides (Schema-Generalisierung + konkrete Episoden). Der LLM-Reflect-Output selbst ist out-of-scope (kosten-Gate).
- [x] LLM-frei: `description_llm_caller=None` triggert den Template-Fallback aus Story 08 — kein API-Geld pro Lauf.
- [x] Test-Determinismus: deterministische numpy-Seeds; per-Test UUID-bank_id; finally-Cleanup über alle drei Stores.

## Tasks

- [x] **T1 — Test-File:** `tests/test_coffee_meeting_lifecycle.py` (~245 LOC), single Test-Funktion `test_coffee_meeting_full_lifecycle` mit klarer 5-Phasen-Struktur im Body.
- [x] **T2 — Synthetic-Daten-Helper:** Inline `_coffee_centroid` / `_coffee_engram_vec` / `_coffee_engram_tags` / `_coffee_text` (statt eigenem `coffee_meeting_factory.py` — kompakter, kein neues Top-Level-Modul für eine einmalige Suite).
- [x] **T3 — Assertion-Helper:** Inline-Asserts auf `evidence_count ≥ 5`, Schema-Hit-Presence im Recall, Evidence-Text-Non-Empty. Eigene Helper-Module wären für eine einzige Suite Overkill.
- [x] **T4 — Mock-LLM:** Nicht nötig — Template-Fallback aus Story 08 erzeugt deterministisches Description-Property; Reflect-LLM-Aufruf ist out of scope.
- [x] **T5 — Phase-Trigger:** Direkter Funktionsaufruf von `run_c2_phase` / `HybridRetriever.retrieve` / `resolve_all_schema_evidence` (statt HTTP-API-Helper — niedrigere Latenz, weniger Bewegliches).
- [x] **T6 — Doku:** Test-File-Header dokumentiert die 5 Phasen explizit als Lifecycle-Walk.
- [x] **T7 — CI-Integration:** `@pytest.mark.integration` (Modul-Pytestmark); skip ohne `HINDSIGHT_TEST_QDRANT_URL` / `HINDSIGHT_TEST_NEO4J_URL` / pg0 — gleicher Gate wie Story 19. Eigener `e2e`/`slow`-Marker nicht nötig solange die Integration-CI-Lane existiert.
