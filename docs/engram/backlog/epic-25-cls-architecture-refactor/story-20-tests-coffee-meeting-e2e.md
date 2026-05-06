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

- [ ] Neue Datei `tests/e2e/test_coffee_meeting_lifecycle.py`
- [ ] Synthetic-Daten: 30 Engrams mit Tags `{activity: "coffee", participants: 1, time: <range 14-17>, duration: <range 30-60>, mood: "productive"}`
- [ ] Phase 1: Retain — alle 30 Engrams werden persistiert mit `layer="working"`
- [ ] Phase 2: C1 trigger — alle 30 wandern nach `layer="buffer"` (oder eine Teilmenge — Composite-abhängig)
- [ ] Phase 3: C2 trigger (2× damit R2 Maturation greift) — 1 Schema entsteht mit evidence_count ≥ 5
- [ ] Phase 4: Recall mit Query "tell me about coffee meetings" — Treffer enthält Schema + Top-N Evidence-Engrams
- [ ] Phase 5: Reflect-Antwort enthält im Output sowohl Schema-Description als auch ≥ 1 konkretes Beispiel
- [ ] Test läuft in < 60s (incl. LLM-Calls — mit Mock-LLM oder local-stub)
- [ ] Test ist deterministisch (kein flaky)

## Tasks

- [ ] **T1 — Test-File anlegen:** `tests/e2e/test_coffee_meeting_lifecycle.py`.
- [ ] **T2 — Synthetic-Daten-Generator:** `tests/fixtures/coffee_meeting_factory.py` produziert 30 Engrams mit Variationen in den Tags.
- [ ] **T3 — Assertion-Helper:** `assert_schema_with_evidence(name_pattern, min_evidence_count)`, `assert_recall_contains_schema_and_engrams(response, min_engrams=1)`.
- [ ] **T4 — Mock-LLM:** Für Schema-Description + Reflect-Pipeline LLM-Mock einsetzen, der deterministisch antwortet.
- [ ] **T5 — Phase-Trigger:** Helper `trigger_c1(bank_id)`, `trigger_c2(bank_id)`, `trigger_c3(bank_id)` auf interne API.
- [ ] **T6 — Doku:** Test-File-Header dokumentiert die fünf Phasen klar als Lebenszyklus.
- [ ] **T7 — CI-Integration:** Marker `e2e` und `slow`, läuft im Nightly-CI-Job.
