# Story 03 — Extended Scoring Formula (S3 + S4)

## User Story

Als System soll die Scoring-Formel Engram Strength und Thalamus Scores einbeziehen, und die Recency-Decay durch Strength moduliert werden.

## Kontext

Hindsight's Scoring: `60% CE + 20% RRF + 10% Temporal + 10% Recency`. Wir erweitern auf: `w1×CE + w2×RRF + w3×Temporal + w4×Recency(strength-moduliert) + w5×Engram_Strength + w6×Thalamus_Weighted`. Die Gewichte sind mode-abhängig (ScoringWeights aus Epic 06). Zusätzlich: Strength-Pre-Filter vor teuren Pipeline-Stufen (mode-abhängig).

## Bestehende Codebasis

- **Scoring:** `search/scoring.py` — `calculate_recency_weight(days, half_life=365)`, `calculate_frequency_weight(count)`, `calculate_temporal_proximity()`. Alle logarithmisch.
- **ScoredResult:** `search/types.py` — `combined_score` berechnet aus CE + RRF + Recency + Temporal.
- **ScoringWeights:** `engine/session/mode_config.py` (aus Epic 06) — 6 Gewichte pro Mode.
- **FullEngram:** `engine/engram_repository.py` (aus Epic 01/02) — `strength`, `thalamus_scores`.

## Akzeptanzkriterien

- [ ] Scoring-Formel nutzt 6 Gewichte statt fester Prozentverteilung
- [ ] Gewichte kommen aus ModeConfig.scoring_weights
- [ ] Recency-Decay: Half-Life moduliert durch Engram Strength (stärker → langsamere Decay)
- [ ] Strength Pre-Filter: Engrams unter Strength-Threshold werden vor teuren Stufen gefiltert
- [ ] Thalamus-Score Gewichtung: Mode-spezifische Dimension wird geboostet
- [ ] Ohne ModeConfig/Session: Fallback auf bestehende Hindsight-Gewichte

## Tasks

- [ ] **T1 — Strength-modulierte Recency:** In `scoring.py`: `calculate_recency_weight()` erweitern. Neuer Parameter `strength: float = 0.5`. Formel: `effective_half_life = base_half_life * (1 + strength)`. Strength 0.0 → 365 Tage Half-Life (wie bisher). Strength 1.0 → 730 Tage Half-Life (doppelt so langsam).
- [ ] **T2 — Thalamus Score als Scoring-Dimension:** In `scoring.py`: Neue Funktion `calculate_thalamus_weight(thalamus_scores: ThalamusScores, boost_dimension: str | None) → float`. Wenn boost_dimension gesetzt → Score dieser Dimension × 1.5. Sonst: overall Score. Normalisiert auf 0-1.
- [ ] **T3 — Engram Strength als Scoring-Dimension:** In `scoring.py`: Neue Funktion `calculate_strength_weight(strength: float) → float`. Direkte Nutzung des Strength-Werts (bereits 0-1 normalisiert). Logarithmische Dämpfung um Dominanz sehr starker Engrams zu vermeiden.
- [ ] **T4 — Extended combined_score:** In `search/types.py` oder `scoring.py`: Neue Funktion `calculate_combined_score(ce, rrf, temporal, recency, strength, thalamus, weights: ScoringWeights) → float`. Gewichtete Summe aller 6 Dimensionen. Normalisierung: Jede Dimension 0-1, dann gewichtet.
- [ ] **T5 — Strength Pre-Filter:** In `retrieval.py`: Nach Seed-Phase, vor Reranking: Engrams mit `strength < mode_config.strength_pre_filter` entfernen. Logik: Engram Dictionary abfragen für Strength-Werte der Kandidaten. Batch-Query für Performance.
- [ ] **T6 — Reranking Integration:** In `reranking.py` oder `retrieval.py`: `ScoredResult.combined_score` nutzt jetzt `calculate_combined_score()` mit ModeConfig Weights. Fallback ohne ModeConfig: Bestehende 60/20/10/10 Verteilung (CE, RRF, Temporal, Recency).
- [ ] **T7 — Unit Tests:** Strength-modulierte Recency (verschiedene Strength-Werte). Thalamus-Boost mit verschiedenen Dimensionen. Pre-Filter entfernt schwache Engrams. Scoring-Formel mit allen 6 Dimensionen. Fallback auf Hindsight-Gewichte.
