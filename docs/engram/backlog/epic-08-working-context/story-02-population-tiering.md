# Story 02 — Population & Tiering

## User Story

Als System soll der Working Context automatisch aus Retrieval-Ergebnissen befüllt werden, wobei die Engrams in die richtigen Tiers sortiert werden.

## Kontext

Nach jedem `recall_async()` werden die zurückgegebenen Engrams in den Working Context eingespeist. Top-Ergebnisse → Focus, mittlere → Supporting, schwächere → Peripheral. Bereits aktive Engrams die erneut abgerufen werden steigen im Tier auf (Reinforcement). Engrams die nicht mehr abgerufen werden sinken ab (Decay → Story 03).

## Bestehende Codebasis

- **recall_async:** `memory_engine.py` — Liefert `RecallResultModel` mit `ScoredResult` Liste.
- **ScoredResult:** `search/types.py` — `combined_score`, `cross_encoder_score`.
- **WorkingContext:** `engine/session/working_context.py` (aus Story 01) — ActiveEngrams mit 3 Tiers.

## Akzeptanzkriterien

- [x] Nach jedem recall: Ergebnisse werden in Working Context eingespeist
- [x] Tiering basierend auf combined_score: Top 3-5 → Focus, nächste 5-10 → Supporting, Rest → Peripheral
- [x] Bereits aktive Engrams: Score wird aufaddiert (Reinforcement), bei Threshold → Tier-Aufstieg
- [x] Neue Engrams verdrängen schwächere wenn Capacity-Limit erreicht
- [x] Goal-Relevanz beeinflusst Tiering: Engrams relevant zum aktiven Goal werden bevorzugt

## Tasks

- [x] **T1 — populate_from_recall():** In `working_context.py`: Methode `WorkingContext.populate_from_recall(results: list[ScoredResult])`. Sortiert Ergebnisse nach Score, verteilt auf Tiers. Prüft ob Engram bereits in einem Tier → Reinforcement statt Duplikat.
- [x] **T2 — Reinforcement Logic:** Wenn ein Engram bereits im Working Context ist und erneut abgerufen wird: `relevance_score += new_score * 0.5`. Wenn Score Threshold überschreitet → Tier-Aufstieg (Peripheral → Supporting, Supporting → Focus). Aufstieg kann Verdrängung im Ziel-Tier auslösen.
- [x] **T3 — Displacement Logic:** Wenn ein Tier voll ist und ein neues Engram hinzugefügt werden soll: Schwächstes Engram im Tier wird in den nächst-niedrigeren Tier verschoben. Bei Peripheral-Overflow: Schwächstes wird komplett entfernt.
- [x] **T4 — Goal-Relevanz Bonus:** Wenn aktiver Goal im Goal-Stack: Engrams die zum Goal relevant sind (Keyword-Match oder Tag-Match) bekommen Score-Bonus (+0.2). Einfache Heuristik, kein LLM-Call.
- [x] **T5 — Integration in recall_async():** In `memory_engine.py`: Nach Retrieval-Ergebnis → `working_context.populate_from_recall()` aufrufen wenn Session aktiv. Working Context wird über SessionManager zugänglich.
- [x] **T6 — Unit Tests:** Population aus Recall-Ergebnissen. Tiering korrekt nach Score. Reinforcement bei wiederholtem Recall. Displacement bei vollem Tier. Goal-Relevanz Bonus.
