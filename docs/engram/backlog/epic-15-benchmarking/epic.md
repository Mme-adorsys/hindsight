# Epic 15 — Benchmarking & Validation

> 4 Dimensionen × 3 Ansätze: Quantitative Gesamtbewertung des Systems.

## Ziel

Benchmark C (Golden Dataset) als quantitativer Gesamtbenchmark. Ergänzt die bereits stufenweise eingeführten Tests: Benchmark A (Scripted Scenarios, seit Phase 1) und Benchmark B (Simulated Agent Life, seit Phase 5). Epic 15 konsolidiert alle Benchmark-Ergebnisse und erstellt ein Dashboard für die 4 Validierungsdimensionen.

## Bestehende Test-Infrastruktur (aus vorherigen Epics)

- **Unit Tests:** Ab Epic 01 — Pro-Komponente Tests.
- **Connectivity Tests:** Epic 01 — DB-Erreichbarkeit.
- **Integration Tests:** Ab Epic 05 — Daten fließen durch das System.
- **Retrieval Tests:** Epic 07 Story 06 — Precision/Recall, Mode-Dependency, Graph-Traversal.
- **Reconsolidation Tests:** Epic 10 Story 04 — Cross-DB Consistency nach Reconsolidation.
- **Knowledge Evolution Tests:** Epic 12 Story 06 — Multi-Cycle Engram-Entwicklung.
- **Multi-Bank Tests:** Epic 14 Story 05 — Agent-Isolation, Promotion, Conflict Resolution.
- **Benchmark B Seed:** Epic 14 Story 05 T6 — Simulated Agent Life Baseline.

## 4 Validierungsdimensionen

1. **Storage Validation:** Fact Extraction Accuracy, Embedding Quality, Link Creation, Thalamus Scores, Entity Resolution
2. **Retrieval Validation:** Precision/Recall, Ranking-Qualität, Mode-Dependency, Graph Traversal, Temporal Queries
3. **Knowledge Evolution:** Engram Strength Tracking, Reconsolidation, Weak Links, Schema Formation, Decay Patterns
4. **Construction Quality:** Inference-Generierung, Gap-Identifikation, Mode Shaping, Prediction Error

## 3 Benchmark-Ansätze

- **A — Scripted Scenarios** (begleitend seit Phase 1): Bereits implementiert als Unit + Integration Tests
- **B — Simulated Agent Life** (seit Phase 5): Baseline aus E14 Story 05 T6
- **C — Golden Dataset** (dieses Epic): Kuratiertes Dataset mit Ground Truth

## Abhängigkeiten

- Alle vorherigen Epics (System muss vollständig sein)

## Stories

1. [Golden Dataset Design & Creation](story-01-golden-dataset.md) (Benchmark C)
2. [Simulated Agent Life Extension](story-02-simulated-agent-life.md) (Benchmark B vollständig)
3. [Benchmark Dashboard & Metrics](story-03-benchmark-dashboard.md)
4. [Final System Validation](story-04-final-validation.md)
