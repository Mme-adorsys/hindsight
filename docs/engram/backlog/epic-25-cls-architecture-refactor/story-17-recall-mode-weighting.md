# Story 17 — Recall Mode-abhängige Schema/Engram-Gewichtung

## User Story

Als Recall-Pipeline soll ich abhängig vom Session-Mode unterschiedlich zwischen Schema- und Engram-Treffern gewichten — Precision priorisiert Schemas (Allgemeinheit), Exploration priorisiert Engrams (Spezifik) — damit der Mode die Antwort-Charakteristik prägt.

## Kontext

Beide Treffertypen kommen aus derselben Vektor-Search (Story 15) mit Cosine-Score. Die Mode-Gewichtung modifiziert den finalen Recall-Score: Schema-Hits werden mit `w_schema(mode)`, Engram-Hits mit `w_engram(mode)` multipliziert. Im Mode `Precision` boostet das Schemas; in `Exploration` Engrams.

## Bestehende Codebasis

- **Session Mode Config:** `engine/session/mode_config.py` (aus Epic 06) mit Mode-spezifischen Gewichten.
- **Recall Scoring:** Bestehende Formel aus Kapitel 8 + Epic 07.
- **HybridRetriever:** liefert RetrievalHits mit kind und score (aus Story 15).

## Akzeptanzkriterien

- [ ] In `mode_config.py` neue Felder `w_schema: float`, `w_engram: float` pro Mode
- [ ] Default-Werte:
  - Precision: `w_schema=1.2`, `w_engram=0.9`
  - Exploration: `w_schema=0.8`, `w_engram=1.2`
  - Analogy: `w_schema=1.1`, `w_engram=1.0`
  - Validation: `w_schema=1.0`, `w_engram=1.0`
- [ ] Im HybridRetriever wird der finale Score multipliziert: `final_score = base_score × (w_schema if hit.kind=="schema" else w_engram)`
- [ ] Top-K wird nach finalem Score sortiert (Re-Ranking)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Mode-Config-Erweiterung:** In `mode_config.py` neue Felder. Pro Mode-Default-Werte oben.
- [ ] **T2 — Re-Ranking im HybridRetriever:** In `hybrid_retriever.py::retrieve()` nach Qdrant-Search → Score-Modifikation pro Hit, dann Re-Sort.
- [ ] **T3 — Konfigurierbarkeit:** Gewichte überschreibbar pro Bank (analog zu anderen Mode-Settings).
- [ ] **T4 — Unit-Tests:** (a) Im Precision-Mode wird ein gleichscored Schema vor einem gleichscored Engram zurückgegeben. (b) Im Exploration-Mode umgekehrt. (c) Re-Sort funktioniert korrekt nach Score-Modifikation.
