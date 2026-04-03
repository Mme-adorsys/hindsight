# Story 04 — Shared-Bank Query Integration (B4 + B6)

## User Story

Als System soll der Cross-Bank Query jetzt mit echtem Shared Bank Content funktionieren, mit mode-abhängiger Bank-Gewichtung.

## Kontext

B4 + B6 — In Epic 07 Story 05 wurde das Dual-Bank Query Routing vorbereitet (Agent + Shared parallel). Jetzt hat die Shared Bank Content (durch Epic 12/13/14 Story 03). Diese Story vervollständigt das Zusammenspiel: Ergebnis-Fusion, Source-Marking, Bank-Gewichtung.

## Bestehende Codebasis

- **Dual-Bank Query:** `memory_engine.py` (aus Epic 07) — `merge_bank_results()`. Routing vorbereitet.
- **EngramRetriever:** `search/engram_retrieval.py` (aus Epic 07) — Neo4j + Qdrant Retrieval.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — Bank-Gewichtung pro Mode.

## Akzeptanzkriterien

- [ ] Shared Bank Ergebnisse fließen in recall Ergebnisse ein
- [ ] Bank-Gewichtung: Precision → Agent 0.7 / Shared 0.3, Exploration → Agent 0.3 / Shared 0.7
- [ ] Schema-Ergebnisse aus Shared Bank werden bevorzugt wenn relevant
- [ ] Source-Marking korrekt: Jedes Ergebnis zeigt Herkunfts-Bank
- [ ] Kein direkter Cross-Agent Read — nur über Shared Bank

## Tasks

- [ ] **T1 — End-to-End Dual-Bank Query:** Validieren und vervollständigen dass `recall_async()` korrekt: Agent Bank → MPFP Retriever UND Shared Bank → EngramRetriever parallel aufruft. Ergebnisse über `merge_bank_results()` fusioniert.
- [ ] **T2 — Schema-Boost in Shared Results:** Shared Bank Ergebnisse die Schemas sind (type='schema') bekommen zusätzlichen Score-Boost (+0.2). Schemas sind höherwertig als einzelne Engrams weil sie abstrahiertes Wissen repräsentieren.
- [ ] **T3 — Freshness Penalty für Shared:** Shared Bank Ergebnisse sind tendenziell älter (durchlaufen Consolidation). Leichter Freshness-Penalty: `score *= 0.95` um Agent-eigene frischere Ergebnisse leicht zu bevorzugen.
- [ ] **T4 — Cross-Bank Deduplication:** Wenn Agent Bank und Shared Bank dasselbe Engram liefern (weil Agent-Engram in Shared promoted wurde): Deduplizieren, Agent-Version bevorzugen (kontextspezifischer).
- [ ] **T5 — Unit Tests:** Dual-Bank Query mit Content in beiden Banks. Bank-Gewichtung mode-abhängig. Schema-Boost. Freshness-Penalty. Deduplication.
