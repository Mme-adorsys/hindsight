# Story 05 — Session-Mode Routing (S5)

## User Story

Als System soll der Session Mode bestimmen welcher Retriever verwendet wird und wie die Ergebnisse gewichtet werden, anstatt die Bank Disposition.

## Kontext

Hindsight nutzt die Bank Disposition (Personality) um Retrieval-Verhalten zu beeinflussen. Wir ersetzen das durch den Session Mode: Der Mode steuert Retriever-Auswahl, Bank-Gewichtung bei Cross-Bank Queries, und Scoring-Weights. Die Disposition bleibt erhalten, wird aber nur noch für Reflect/Reconsolidation genutzt.

## Bestehende Codebasis

- **Bank Disposition:** `engine/retain/bank_utils.py` — `BankProfile` mit Disposition Dict. Beeinflusst aktuell Retrieval.
- **recall_async:** `memory_engine.py` — Nutzt `bank_id` für Routing.
- **SessionManager:** `engine/session/session_manager.py` (aus Epic 06) — Session mit Mode.
- **ModeConfig:** `engine/session/mode_config.py` (aus Epic 06) — Alle Parameter pro Mode.
- **RetrieverRegistry:** `engine/search/retrieval.py` (aus Story 04) — Bank → Retriever Mapping.

## Akzeptanzkriterien

- [x] Session Mode bestimmt Retriever-Konfiguration (nicht Disposition)
- [x] Dual-Bank Query: Agent Session Bank + Shared Bank parallel
- [x] Bank-Gewichtung mode-abhängig: Precision → Agent höher, Exploration → Shared höher
- [x] Disposition-Einfluss auf Retrieval entfernt (nur noch in Reflect)
- [x] Ohne Session: Default Precision-Mode (backward compat)

## Tasks

- [x] **T1 — Dual-Bank Query Orchestration:** In `memory_engine.py` oder neuem Modul: `recall_async()` sendet Query parallel an Agent Session Bank (PostgreSQL → MPFP) UND Shared Bank (Qdrant + Neo4j → EngramRetriever). `asyncio.gather()` für Parallelität.
- [x] **T2 — Bank-Weight Merging:** Neue Funktion `merge_bank_results(agent_results, shared_results, mode: RetrievalMode) → list[ScoredResult]`. Gewichtung: Precision → Agent 0.7 / Shared 0.3, Exploration → Agent 0.3 / Shared 0.7, Analogy → Agent 0.3 / Shared 0.7, Validation → Agent 0.5 / Shared 0.5. RRF über gewichtete Scores.
- [x] **T3 — Source-Marking:** Jedes Ergebnis bekommt `source: Literal['agent', 'shared']` als Metadata. Wird bis in die API-Response durchgereicht (damit Agent weiß woher das Wissen kommt).
- [x] **T4 — Disposition aus Retrieval entfernen:** In `recall_async()` und Retrieval-Pipeline: Alle Stellen wo BankProfile.disposition das Retrieval beeinflusst → entfernen. Disposition nur noch in `reflect_async()` nutzen.
- [x] **T5 — Unit Tests:** Dual-Bank Query Parallelität. Bank-Weight Merging pro Mode. Source-Marking korrekt. Ohne Session → Default Precision Routing.
