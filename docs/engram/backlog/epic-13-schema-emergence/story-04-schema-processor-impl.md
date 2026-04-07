# Story 04 — SchemaProcessor Implementation

## User Story

Als System soll die echte SchemaProcessor Implementation die NoOp-Version ersetzen und alle 5 Regeln (R1-R5) in NCR Phase 3 orchestrieren.

## Kontext

Die SchemaProcessor Interface (Epic 12 Story 04) hat eine NoOp-Implementation. Jetzt ersetzen wir sie durch die echte Logik die R1, R2, R3, R5 als Batch ausführt (R4 läuft inkrementell bei Retain).

## Akzeptanzkriterien

- [x] EngamSchemaProcessor implementiert SchemaProcessor Interface
- [x] Führt R1 → R2+R3 → R5 sequenziell aus
- [x] SchemaResult enthält Details über alle Aktionen
- [x] NCR Orchestrator nutzt EngamSchemaProcessor statt NoOp
- [x] Fault-tolerant: Fehler in einem Schritt stoppt nicht die anderen

## Tasks

- [x] **T1 — EngramSchemaProcessor:** `engine/consolidation/engram_schema_processor.py`. Implementiert `SchemaProcessor`. Constructor: `(neo4j_client, qdrant_client, engram_repo, llm)`. `async process()`: R1 (Clustering) → R2+R3 (Maturation+Abstraction) → R5 (Competition). Jeder Schritt in try/except.
- [x] **T2 — Wiring:** In NCR Orchestrator: `NoOpSchemaProcessor` durch `EngramSchemaProcessor` ersetzen. Dependency Injection über Constructor.
- [x] **T3 — SchemaResult Details:** Erweitere SchemaResult: `clusters_found: int`, `schemas_matured: int`, `schemas_created: int`, `schemas_deleted: int`, `reinforcements: int`. Jedes Detail einzeln geloggt.
- [x] **T4 — Integration Test:** 10 Engrams in bekannten Cluster-Patterns → 3 NCR-Zyklen laufen → Erwartet: Cluster erkannt (Zyklus 1), gereift (Zyklus 3), Schema erstellt. Schwaches Schema stirbt nach 5 Zyklen ohne Reinforcement.
