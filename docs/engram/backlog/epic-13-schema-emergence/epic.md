# Epic 13 — Schema Emergence

> 5 Game-of-Life Regeln: Clustering, Maturation, Abstraction, Reinforcement, Competition.

## Ziel

Schemas sind keine vordefinierten Kategorien — sie entstehen emergent aus den Daten. 5 lokale Regeln (analog zu Conway's Game of Life) erzeugen, reifen, abstrahieren, stärken und bereinigen Schemas. Alles in einem flachen Graphen — keine vorgegebene Hierarchie.

## Bestehende Codebasis

- **SchemaProcessor Interface:** `consolidation/schema_processor.py` (aus Epic 12) — NoOp Implementation. Wird hier durch echte Logik ersetzt.
- **Neo4j Client:** `engine/neo4j_client.py` (aus Epic 01) — Graph Queries für Clustering.
- **Engram Dictionary:** `engine/engram_repository.py` — FullEngram mit Tags, Strength, Entities.
- **Schema-Fit Check:** `retain/link_creation.py` oder `schema_links.py` (aus Epic 05) — R4 inkrementell bei Retain.

## Scope

- R1: Clustering/Birth (NCR Batch)
- R2: Repetition/Maturation (NCR Batch)
- R3: Abstraction/Specialization (NCR Batch)
- R4: Reinforcement/Growth (Retain inkrementell — bereits vorbereitet in Epic 05)
- R5: Competition/Death (NCR Batch)
- SchemaProcessor Implementation (ersetzt NoOp)

## Abhängigkeiten

- Epic 12 (Consolidation) — NCR Phase 3 Hook, SchemaProcessor Interface
- Epic 01 (Neo4j) — Graph Queries
- Epic 05 (Retain Pipeline) — R4 Schema-Fit bei Retain (Hook bereits da)

## Stories

1. [x] [Clustering & Birth (R1)](story-01-clustering-birth.md)
2. [x] [Maturation & Abstraction (R2 + R3)](story-02-maturation-abstraction.md)
3. [x] [Reinforcement & Competition (R4 + R5)](story-03-reinforcement-competition.md)
4. [x] [SchemaProcessor Implementation](story-04-schema-processor-impl.md)
