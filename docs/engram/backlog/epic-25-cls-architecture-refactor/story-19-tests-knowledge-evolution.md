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

- [ ] Test-Datei auf neue Module umgestellt (Imports, Module-Pfade)
- [ ] Test-Fälle bedient die neue Architektur:
  - "Engram promotet nach Buffer" (C1)
  - "Schema entsteht aus 3+ ähnlichen Buffer-Engrams" (C2)
  - "Schema wird verstärkt bei neuen passenden Engrams" (C2 R4 + Retain R4 incremental)
  - "Buffer-Engram altert ohne Recall → archived" (C2 Decay)
  - "Schema mit niedrigem evidence_count + alte last_reinforced → archived" (C3 R5)
  - "Hyper-Schema entsteht aus zwei verwandten Schemas" (C3 R3)
- [ ] Test-Coverage ≥ Vorgängerwert
- [ ] Alle Tests grün

## Tasks

- [ ] **T1 — Datei umbenennen:** `test_knowledge_evolution.py` bleibt, alter Inhalt wird ersetzt.
- [ ] **T2 — Fixtures erweitern:** Synthetic-Engram-Datenströme für die 6 oben aufgelisteten Test-Szenarien.
- [ ] **T3 — Test-Cases schreiben:** Pro Szenario einen `pytest`-Case mit klarer Setup/Action/Assert-Struktur.
- [ ] **T4 — Hilfsfunktionen:** `assert_schema_exists(bank_id, name_pattern)`, `assert_engram_archived(id)`, `assert_evidence_count(schema_id, count)`.
- [ ] **T5 — CI-Integration:** Tests laufen im bestehenden CI-Job mit Marker `integration`. Datenbanken werden im Test-Container hochgefahren.
- [ ] **T6 — Coverage-Report:** Neue Module (c2_pattern_recognition, c2_schema_writer, c2_decay, c3_schema_restructure, etc.) sind ≥ 80% covered.
