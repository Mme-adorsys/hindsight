# Story 02 — Simulated Agent Life Extension (Benchmark B)

## User Story

Als Entwickler brauche ich einen vollständigen Simulated Agent Life Benchmark der über simulierte Wochen operiert und Langzeitverhalten validiert.

## Kontext

Benchmark B Baseline wurde in Epic 14 Story 05 T6 gesetzt (10 simulierte Tage). Hier erweitern wir auf einen vollständigen Benchmark: Mehrere Agents, längerer Zeitraum, diverse Aktivitätsmuster, Metriken über Zeit.

## Akzeptanzkriterien

- [ ] 2 Agents operieren über 30 simulierte Tage
- [ ] Jeder Agent hat ein Aktivitätsprofil (Researcher vs. Developer)
- [ ] Tägliche Aktivitäten: Retain + Recall + Reflect
- [ ] Wöchentliche Aktivitäten: NCR (7 Zyklen)
- [ ] Metriken über Zeit: Engram-Anzahl, Strength-Distribution, Schema-Count, Shared-Bank Population
- [ ] Convergence Detection: Wann stabilisiert sich das System?

## Tasks

- [ ] **T1 — Agent Profile Definition:** Researcher Agent: Viel Retain (explorativ), wenig Recall, breite Themen. Developer Agent: Fokussierter Retain, häufiges Recall (Precision), enge Themen. Profile als JSON-Config.
- [ ] **T2 — Activity Generator:** `engine/benchmark/activity_generator.py`. Generiert tägliche Aktivitäten für einen Agent: `generate_day(agent_profile, day_number) → list[Activity]`. Activity: Retain(content), Recall(query), Reflect. Deterministisch (seeded PRNG).
- [ ] **T3 — Simulation Runner:** `engine/benchmark/simulation_runner.py`. Orchestriert: Für jeden Tag → Activities ausführen → NCR am Ende des Tages (oder alle 7 Tage). Promotion-Checks. Logging aller Metriken.
- [ ] **T4 — Metriken-Sammlung:** Pro Tag und Agent: `{engram_count_by_layer, avg_strength, schema_count, shared_bank_size, recall_latency, recall_precision, construction_quality}`. Als CSV/JSON exportiert.
- [ ] **T5 — Convergence Analysis:** Nach 30 Tagen: Engram-Wachstum abflachen? Schema-Anzahl stabilisiert? Shared Bank wächst linear oder sub-linear? Visualization als Charts.
- [ ] **T6 — Cross-Agent Interaction:** Researcher und Developer haben thematische Überlappung (z.B. beide über "API Design"). Erwartung: Convergence im Shared Bank. Cross-Agent Schemas sollten emergieren.
