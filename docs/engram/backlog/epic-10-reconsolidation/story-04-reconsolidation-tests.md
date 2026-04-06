# Story 04 — Reconsolidation Integration Tests

## User Story

Als Entwickler brauche ich Integration Tests die belegen, dass Reconsolidation korrekt funktioniert: Priority Selection, Semantic Trigger, Disposition-Einfluss, und Engram-Updates über alle 3 Datenbanken.

## Akzeptanzkriterien

- [x] End-to-End Test: retain → recall → reflect Zyklus
- [x] Schwache Engrams werden bevorzugt reconsolidiert
- [x] Semantic Trigger findet verwandte Engrams
- [x] Engram Strength wird nach Reconsolidation korrekt updated (in Dictionary)
- [ ] Modified Content wird in Qdrant und Neo4j synchron aktualisiert

## Tasks

- [x] **T1 — Test-Fixture:** 20 Engrams mit verschiedenen Strengths (0.05 bis 0.95), 2 PE-geflaggt (die stärksten — Priorität unabhängig von Strength). PostgreSQL-only fixture, Qdrant-Test skippt ohne Service.
- [x] **T2 — Priority Queue Test:** filter_entries → build_reconsolidation_queue → PE-Engrams zuerst, dann schwache (strength < 0.3), dann Rest nach last_accessed.
- [x] **T3 — Semantic Trigger Test:** find_reconsolidation_candidates via Qdrant (skip ohne HINDSIGHT_TEST_QDRANT_URL). Score-Filter >= 0.6 verifiziert.
- [x] **T4 — Cross-DB Consistency Test:** update_strength() persistiert in PostgreSQL und ist via filter_entries sichtbar. Qdrant/Neo4j-Sync in Epic 12 (NCR).
- [x] **T5 — Disposition Variation Test:** Analytical > Conservative bei Confirmations. Conservative supprimiert Contradictions bei low similarity. Optimistic Bias hebt Confirmed über Analytical.
