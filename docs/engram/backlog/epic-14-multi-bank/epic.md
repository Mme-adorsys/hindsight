# Epic 14 — Multi-Bank Architecture

> B1-B6: 3-Tier Bank Model, Write Conflict Resolution, Cross-Bank Novelty, Consolidation Triggers.

## Ziel

Das 3-Tier Bank Model isoliert Agent-spezifisches Wissen und ermöglicht Cross-Agent Shared Memory. Tier 1 (Agent Session Bank / PostgreSQL) → Tier 2 (Agent Engram Dictionary / Neo4j+Qdrant) → Tier 3 (Shared Memory Bank / Neo4j+Qdrant). Consolidation Triggers promoten Wissen zwischen Tiers. Cross-Bank Queries (bereits in Epic 07 vorbereitet) werden hier vervollständigt.

## Bestehende Codebasis

- **Bank Model:** Hindsight hat bereits `banks` Tabelle in PostgreSQL. Bank-ID als Isolations-Key.
- **Consolidation Pipeline:** `consolidation/ncr_orchestrator.py` (aus Epic 12) — NCR mit 3 Phasen.
- **Dual-Bank Query:** `memory_engine.py` (aus Epic 07 Story 05) — Routing vorbereitet aber Shared Bank war leer.
- **Schema Emergence:** `consolidation/engram_schema_processor.py` (aus Epic 13) — Schemas als Promotion-Kandidaten.

## Scope

- B1: 3-Tier Bank Model Definition (Tier-Properties, Isolation Rules)
- B2: Write Conflict Resolution (Merge vs. Contradiction-Link)
- B3: Cross-Bank Novelty Scoring (Agent → Shared Promotion)
- B4: Shared-to-Agent Feedback Loop (vervollständigen)
- B5: Consolidation Triggers (NCR-based, Cross-Agent, Schema-Kandidat)
- B6: Cross-Bank Query (vervollständigen mit echtem Shared Bank Content)

## Abhängigkeiten

- Epic 01 (Hybrid Storage) — Alle Datenbanken
- Epic 02 (Engram Model) — FullEngram
- Epic 12 (Consolidation) — NCR als Promotion-Vehikel
- Epic 13 (Schema Emergence) — Schema-Kandidaten als Trigger

## Stories

1. [3-Tier Bank Model](story-01-tier-model.md) (B1)
2. [Write Conflict Resolution](story-02-conflict-resolution.md) (B2)
3. [Cross-Bank Novelty & Promotion](story-03-cross-bank-novelty.md) (B3 + B5)
4. [Shared-Bank Query Integration](story-04-shared-bank-query.md) (B4 + B6)
5. [Multi-Bank Integration Tests](story-05-multi-bank-tests.md)
