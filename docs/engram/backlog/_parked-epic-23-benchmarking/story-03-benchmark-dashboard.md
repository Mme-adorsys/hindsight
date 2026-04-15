# Story 03 — Benchmark Dashboard & Metrics

## User Story

Als Entwickler brauche ich ein Dashboard das alle Benchmark-Ergebnisse über die 4 Validierungsdimensionen konsolidiert.

## Kontext

Drei Benchmark-Ansätze (A, B, C) × 4 Dimensionen = 12 Messpunkte. Das Dashboard gibt einen Gesamtüberblick über die Systemqualität und zeigt Trends über Benchmark-Runs.

## Akzeptanzkriterien

- [ ] Dashboard zeigt alle 4 Dimensionen mit Scores
- [ ] Scores pro Dimension: 0-1 Scale, Aggregat aus Sub-Metriken
- [ ] Historischer Vergleich: Aktuelle Werte vs. vorherige Benchmark-Runs
- [ ] Drill-Down: Von Dimension → Sub-Metrik → einzelne Test-Ergebnisse
- [ ] Export: JSON Report für CI/CD Integration

## Tasks

- [ ] **T1 — Metrics Registry:** `engine/benchmark/metrics.py`. Dataclass `BenchmarkMetrics` mit 4 Dimensionen. Jede Dimension hat Sub-Metriken: Storage (extraction_accuracy, embedding_quality, link_accuracy, entity_accuracy, thalamus_accuracy), Retrieval (precision_at_k, recall_at_k, ranking_quality, mode_sensitivity), Evolution (decay_accuracy, promotion_accuracy, schema_quality, convergence_speed), Construction (inference_quality, gap_detection, mode_shaping, prediction_error_accuracy).
- [ ] **T2 — Benchmark Runner:** `engine/benchmark/runner.py`. Orchestriert: Golden Dataset (C) laden → System alimentieren → Metriken berechnen → Report erstellen. Auch: Simulated Life (B) Ergebnisse einsammeln → Metriken berechnen.
- [ ] **T3 — Score Calculation:** Pro Sub-Metrik: Vergleich System-Output vs. Ground Truth. Metriken: Precision@K, Recall@K, NDCG, Cosine Similarity, F1. Aggregation: Gewichteter Durchschnitt pro Dimension.
- [ ] **T4 — JSON Report:** `BenchmarkReport` als JSON exportieren. Felder: `timestamp, system_version, dimensions: {storage: {score, sub_metrics}, ...}, benchmark_a_summary, benchmark_b_summary, benchmark_c_summary`.
- [ ] **T5 — Historical Comparison:** Reports in `benchmarks/` Ordner speichern (timestamped). Vergleichsfunktion: Aktuell vs. Vorherig → Delta pro Sub-Metrik. Regressions-Erkennung: Score-Drop > 5% → Warning.
