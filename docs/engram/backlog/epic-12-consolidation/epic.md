# Epic 12 — Consolidation Pipeline

> ⚠️ **Superseded by [Epic 25 — CLS Architecture Refactor](../epic-25-cls-architecture-refactor/epic.md)**.
> Die hier dokumentierte 4-Stufen-Architektur (Working → Buffer → Neocortex mit NCR Decay+Strengthen+Schema-Compression) wurde in Epic 25 durch das CLS-konforme 3-Phasen-Modell (C1, C2 Pattern Recognition, C3 Schema-Restrukturierung) ersetzt. Engrams leben nur noch im Buffer; Schemas sind eigene Neo4j-Knoten im Cortex. Diese Datei bleibt als Audit-Trail erhalten.

> 4-Stufen-Modell: Working Memory → Buffer → Neocortex. NCR mit 3 Phasen.

## Ziel

Die Consolidation Pipeline überführt kurzfristiges Wissen in langfristiges. Biologisch inspiriert: SWS (Slow-Wave Sleep) für Decay und Stärkung, REM für Schema-Kompression. Implementiert als Nightly Consolidation Run (NCR) — ein Batch-Prozess der periodisch läuft.

## Bestehende Codebasis

- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — `layer` Property: 'buffer' oder 'neocortex'.
- **FullEngram:** Strength, access_count, last_accessed, thalamus_scores.
- **Neo4j Client:** `engine/neo4j_client.py` (aus Epic 01) — Graph Operations.
- **Qdrant Client:** `engine/qdrant_client.py` (aus Epic 01) — Payload Updates.
- **memory_units Tabelle:** PostgreSQL — Kurzfristiger Storage (Agent Session Bank).

## Scope

- Consolidation 1: memory_units (PostgreSQL) → Engram Buffer (Dictionary layer='buffer')
- Consolidation 2: NCR mit 3 Phasen (Decay, Strengthen, Schema Compression)
- NCR Scheduling (periodisch, konfigurierbar)
- Engram Lifecycle: buffer → neocortex → archived

## Nicht in Scope

- Schema Emergence Regeln R1-R5 (→ Epic 13) — NCR Phase 3 ruft sie auf
- Multi-Bank Promotion (→ Epic 14)

## Abhängigkeiten

- Epic 01 (Hybrid Storage) — Alle 3 Datenbanken
- Epic 02 (Engram Model) — FullEngram mit Strength, Layer
- Epic 05 (Retain Pipeline) — Engrams existieren im System

## Stories

1. [Consolidation 1: Session → Buffer](story-01-session-to-buffer.md)
2. [NCR Phase 1: Decay](story-02-ncr-decay.md)
3. [NCR Phase 2: Strengthen](story-03-ncr-strengthen.md)
4. [NCR Phase 3: Schema Compression Hook](story-04-ncr-schema-hook.md)
5. [NCR Scheduler & Orchestration](story-05-ncr-scheduler.md)
6. [Knowledge Evolution Tests](story-06-evolution-tests.md)
