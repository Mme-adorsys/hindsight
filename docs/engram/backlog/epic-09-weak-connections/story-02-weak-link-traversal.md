# Story 02 — Mode-aware Weak-Link Traversal

## User Story

Als System soll der EngramRetriever Weak Links (co_activated, temporal_proximity) mode-abhängig traversieren oder ignorieren.

## Kontext

Precision-Retrieval braucht starke, zuverlässige Verbindungen — Weak Links sind Rauschen. Exploration-Retrieval profitiert von Weak Links — sie führen zu unerwarteten, kreativen Ergebnissen. Analogy-Retrieval bevorzugt Weak Links — Cross-Domain Muster laufen über schwache Brücken.

## Bestehende Codebasis

- **EngramRetriever:** `search/engram_retrieval.py` (aus Epic 07) — Neo4j Traversal.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `weak_link_policy: ignore/follow/prefer`.
- **MPFPPatternSet:** `search/mpfp_retrieval.py` (aus Epic 07) — Mode-spezifische Patterns.
- **Neo4j Relationships:** CO_ACTIVATED, TEMPORAL_PROXIMITY im Graph.

## Akzeptanzkriterien

- [x] Precision + Validation: Weak Links werden in Cypher-Queries ausgeschlossen
- [x] Exploration: Weak Links werden traversiert (gleiche Priorität wie starke Links)
- [x] Analogy: Weak Links werden bevorzugt (höheres Weight in der Traversal-Score)
- [x] Weight-Threshold: Nur Weak Links mit Weight ≥ 0.1 werden traversiert (zu schwache ignoriert)
- [x] Traversal-Ergebnisse aus Weak Links werden als `source: 'weak_link'` markiert

## Tasks

- [x] **T1 — Weak Link Filter in Cypher:** In `engram_retrieval.py`: Traversal Cypher-Queries um WHERE-Clause erweitern. `ignore` → `WHERE NOT type(r) IN ['CO_ACTIVATED', 'TEMPORAL_PROXIMITY']`. `follow` → Keine Einschränkung. `prefer` → Separate Query für Weak Links mit Weight-Boost.
- [x] **T2 — Prefer-Mode Boost:** Für Analogy Mode: Separate Cypher-Query die gezielt co_activated und temporal_proximity Relationships traversiert. Ergebnis-Scores × 1.5 (Boost). Diese Ergebnisse werden mit regulären Ergebnissen fusioniert.
- [x] **T3 — Weight Threshold:** Minimum Weight 0.1 für Weak Link Traversal. In Cypher: `WHERE r.weight >= 0.1`. Konfigurierbar über ModeConfig Extension.
- [x] **T4 — Source Marking:** Ergebnisse die über Weak Links gefunden wurden bekommen `source='weak_link'` Marker. In `RetrievalResult`: Neues optionales Feld `traversal_source: str | None`. Hilft bei Debugging und Constructive Memory (Epic 11: Inferenzen aus Weak Links sind weniger sicher).
- [x] **T5 — Unit Tests:** Precision ignoriert Weak Links (Cypher Query Validierung). Exploration traversiert Weak Links. Analogy boosted Weak Link Ergebnisse. Weight Threshold filtert zu schwache Links. Source Marking korrekt.
