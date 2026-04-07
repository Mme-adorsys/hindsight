# Story 04 — Final System Validation

## User Story

Als Entwickler brauche ich einen finalen Validierungslauf der alle Benchmarks zusammenführt und das System als "ready" oder "not ready" klassifiziert.

## Kontext

Die finale Validierung ist das Quality Gate für das Gesamtsystem. Sie führt alle 3 Benchmark-Ansätze aus und prüft ob Mindest-Scores erreicht werden.

## Akzeptanzkriterien

- [ ] Alle 3 Benchmarks (A, B, C) laufen in einem Run
- [ ] Mindest-Scores definiert pro Dimension
- [ ] Pass/Fail Report mit detaillierter Begründung bei Fail
- [ ] Reproduzierbar: Gleicher Run → gleiches Ergebnis (deterministische Seeds)

## Tasks

- [ ] **T1 — Quality Gate Definition:** Mindest-Scores pro Dimension: Storage ≥ 0.7, Retrieval ≥ 0.6, Evolution ≥ 0.5, Construction ≥ 0.5. Konfigurierbar. Alle 4 müssen bestanden werden.
- [ ] **T2 — Full Validation Pipeline:** Script: Clean DB → Run Golden Dataset → Run Simulated Life → Collect Scripted Scenario Results → Calculate Scores → Quality Gate Check → Report.
- [ ] **T3 — Pass/Fail Report:** Bei Pass: "System validates. Scores: ..." Bei Fail: "System fails on dimension X. Sub-metric Y scored Z (required: W). Top failing tests: ..."
- [ ] **T4 — CI/CD Integration:** Validation Pipeline als Command aufrufbar: `python -m benchmark.validate`. Exit Code: 0 (pass), 1 (fail). JSON Report nach stdout. Geeignet für CI/CD Pipeline.
- [ ] **T5 — Regression Guard:** Nach jedem Benchmark-Run: Vergleich mit vorherigem. Wenn Score > 5% gesunken in irgendeiner Dimension → Warning (auch wenn noch über Mindest-Score). Hilft Regressionen früh zu erkennen.
