# Story 04 — Reconsolidation Integration Tests

## User Story

Als Entwickler brauche ich Integration Tests die belegen, dass Reconsolidation korrekt funktioniert: Priority Selection, Semantic Trigger, Disposition-Einfluss, und Engram-Updates über alle 3 Datenbanken.

## Akzeptanzkriterien

- [ ] End-to-End Test: retain → recall → reflect Zyklus
- [ ] Schwache Engrams werden bevorzugt reconsolidiert
- [ ] Semantic Trigger findet verwandte Engrams
- [ ] Engram Strength wird nach Reconsolidation korrekt updated (in Dictionary)
- [ ] Modified Content wird in Qdrant und Neo4j synchron aktualisiert

## Tasks

- [ ] **T1 — Test-Fixture:** 20 Engrams mit verschiedenen Strengths (0.1 bis 1.0), verschiedenen Tags, bekannten Embeddings. In allen 3 DBs. 2 Engrams als Prediction-Error geflaggt.
- [ ] **T2 — Priority Queue Test:** Reflect aufrufen → Prüfen dass Prediction-Error Engrams zuerst kommen, dann schwache.
- [ ] **T3 — Semantic Trigger Test:** Neuen Content retainen der semantisch ähnlich zu bestehendem Engram ist → Reflect erkennt Kandidat → Reconsolidation läuft.
- [ ] **T4 — Cross-DB Consistency Test:** Nach Reconsolidation: Engram Content in PostgreSQL (Dictionary), Qdrant (Embedding), und Neo4j (Node Properties) prüfen. Alles synchron.
- [ ] **T5 — Disposition Variation Test:** Gleicher Input mit Analytical vs. Conservative Disposition → Unterschiedliche Strength-Updates und Content-Modifications.
