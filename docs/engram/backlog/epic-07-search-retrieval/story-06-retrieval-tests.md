# Story 06 — Retrieval Integration Tests (Milestone Validation)

## User Story

Als Entwickler brauche ich Retrieval-spezifische Integration Tests die belegen, dass die erweiterte Search-Pipeline korrekt funktioniert — über alle 3 Datenbanken und Modi hinweg.

## Kontext

Ab Epic 07 beginnt laut Test-Policy die Retrieval-Test-Phase. Diese Story definiert die Integration Tests die als Milestone-Gate für Phase 3 dienen. Die Tests validieren nicht einzelne Komponenten (→ Unit Tests in Stories 01-05), sondern das Zusammenspiel: Query fließt durch Session Layer → Dual-Bank Routing → EngramRetriever/MPFP → Scoring → Fusion → Ergebnis.

## Akzeptanzkriterien

- [x] Alle Tests nutzen reale DB-Instanzen (Docker-Compose Test-Setup)
- [x] Tests decken alle 4 Modi ab (Precision, Exploration, Analogy, Validation)
- [x] Tests decken Dual-Bank Routing ab (Agent + Shared)
- [x] Tests messen Precision/Recall gegen bekannte Ground Truth
- [x] Tests sind reproduzierbar (deterministische Test-Daten, feste Seeds)

## Tasks

- [x] **T1 — Test-Fixture Setup:** Docker-Compose Konfiguration für Tests: PostgreSQL + Qdrant + Neo4j. Fixture-Daten: 50 Engrams mit bekannten Tags, Strength, Thalamus Scores, Links. Engrams in Agent Bank UND Shared Bank verteilt. Deterministische Embeddings (nicht aus LLM, sondern vordefinierte Vektoren).
- [x] **T2 — Precision Mode Test:** Query mit hoher Relevanz zu bekannten Engrams. Erwartung: Nur starke Engrams (strength ≥ 0.5), kurze Traversal-Pfade, Task-Relevance geboostet. Precision@5 ≥ 0.8 gegen Ground Truth.
- [x] **T3 — Exploration Mode Test:** Gleicher Query, Exploration Mode. Erwartung: Mehr Ergebnisse (niedrigere Thresholds), schwache Engrams eingeschlossen, Novelty-Boost sichtbar in Ranking. Recall@20 ≥ Ground Truth.
- [x] **T4 — Analogy Mode Test:** Query mit Schema-Link Traversal. Erwartung: Cross-Domain Engrams via Schema-Links gefunden. Weak Links traversiert.
- [x] **T5 — Dual-Bank Test:** Query geht an Agent Bank + Shared Bank. Erwartung: Ergebnisse aus beiden Banks, Source-Marking korrekt, Bank-Gewichtung mode-abhängig.
- [x] **T6 — Tag-Filter Test:** Query mit Tag-Filter. Erwartung: Nur Engrams mit matchenden Tags. Ohne Tags: Alle Engrams.
- [x] **T7 — Scoring Validation:** Manuell berechnete Scores gegen System-Scores vergleichen. Strength-modulierte Recency. Thalamus-Boost. Mode-abhängige Gewichte.
- [x] **T8 — Performance Baseline:** Timing-Tests für Retrieval-Latenz. Baseline für 50 Engrams. Vergleich: MPFP (PostgreSQL) vs. EngramRetriever (Qdrant + Neo4j). Kein harter Threshold, aber Baseline dokumentieren.
