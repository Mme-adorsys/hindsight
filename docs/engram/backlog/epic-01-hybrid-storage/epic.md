# Epic 01 — Hybrid Storage Architecture

> Basis für alles. Muss als erstes stehen.

## Ziel

Erweiterung von Hindsight's monolithischem PostgreSQL+pgvector um Qdrant (Content Store) und Neo4j (Graph Store). PostgreSQL bleibt Agent Session Bank + bekommt eine neue Dictionary-Tabelle. Engram-ID als Synchronisationspunkt zwischen allen drei Systemen.

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/config.py` — Zentrales Config via Env-Vars (HindsightConfig Dataclass)
- `hindsight-api/hindsight_api/models.py` — SQLAlchemy ORM (MemoryUnit, Entity, MemoryLink, Bank, etc.)
- `hindsight-api/hindsight_api/engine/db_utils.py` — asyncpg Pool mit Retry-Logik, `acquire_with_retry()`
- `hindsight-api/hindsight_api/engine/memory_engine.py` — MemoryEngine (zentrale Klasse, ~3500 Zeilen)
- `hindsight-api/hindsight_api/engine/interface.py` — MemoryEngineInterface ABC
- `hindsight-api/hindsight_api/migrations.py` — Alembic Runner mit Advisory Locks
- `hindsight-api/hindsight_api/alembic/versions/` — bestehende Migrationen
- `hindsight-api/pyproject.toml` — Poetry Dependencies

**Bestehende Patterns die wir nutzen:**
- Config via Env-Vars + HindsightConfig Dataclass
- asyncpg Pool mit Retry-Logik für DB Connections
- Alembic Migrationen mit programmatischer Konfiguration
- SQLAlchemy ORM mit AsyncAttrs
- Extension-System (`extensions/base.py`, `extensions/loader.py`)

## Scope

- Qdrant als neuen Storage-Layer integrieren (neben bestehendem PostgreSQL)
- Neo4j als neuen Storage-Layer integrieren
- PostgreSQL Dictionary-Tabelle via Alembic Migration
- Sync Service als neue Schicht im Engine-Layer

## Nicht in Scope

- Engram-Felder im Detail (→ Epic 02)
- Thalamus Scores (→ Epic 04)
- Retrieval-Logik / EngramRetriever (→ Epic 07)
- Multi-Bank Cross-Bank Query (→ Epic 14)

## Abhängigkeiten

- Keine (dieses Epic ist die Basis)

## Referenzen

- `concept.md` → Abschnitt 3 (Storage-Architektur)
- `engram_architecture_complete.md` → Kapitel 1 (CLS Theory)

## Stories

1. [x] [Qdrant Integration](story-01-qdrant-integration.md)
2. [x] [Neo4j Integration](story-02-neo4j-integration.md)
3. [x] [PostgreSQL Dictionary Table](story-03-dictionary-table.md)
4. [x] [Engram Storage Service](story-04-storage-service.md)

## Status

**DONE** — Alle 4 Stories abgeschlossen (2026-04-03)
