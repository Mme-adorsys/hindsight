# Story 03 — PostgreSQL Dictionary Table (Hippocampal Pointer Index)

## User Story

Als System brauche ich eine leichtgewichtige Lookup-Tabelle in der bestehenden PostgreSQL-Datenbank, damit Engram-Metadaten schnell gefiltert werden können ohne Qdrant oder Neo4j abfragen zu müssen.

## Kontext

Der Dictionary ist ein hippocampaler Pointer-Index — er weiß WO Information liegt und WIE sie bewertet ist, speichert aber keinen Inhalt. Ermöglicht schnelle Pre-Filter-Queries (z.B. "alle Engrams mit strength ≥ 0.5 und layer='neocortex'") bevor teure Graph- oder Vector-Operationen starten. Nutzt die bestehende Hindsight PostgreSQL-Datenbank und deren Alembic-Migration-System.

## Bestehende Codebasis

- **Migration Pattern:** `hindsight_api/migrations.py` → Alembic Runner mit Advisory Locks, Multi-Tenant Support. Neue Migration in `hindsight_api/alembic/versions/` anlegen.
- **ORM Pattern:** `hindsight_api/models.py` → SQLAlchemy ORM mit AsyncAttrs, Mapped[]. Neue `EngramDictionary` Klasse hier einfügen, analog zu `MemoryUnit`.
- **FK Pattern:** `models.py` → `Bank` Model mit `bank_id` als PK. Dictionary Table bekommt FK auf `banks.bank_id`, analog zu `MemoryUnit.bank_id`.
- **DB Connection:** `hindsight_api/engine/db_utils.py` → asyncpg Pool. Dictionary nutzt den gleichen Pool.
- **Bestehende Tabellen:** memory_units, entities, unit_entities, entity_cooccurrences, memory_links, banks, documents, chunks. Die Dictionary Table kommt als 9. Tabelle dazu.

## Akzeptanzkriterien

- [ ] Alembic Migration erstellt `engram_dictionary` Tabelle idempotent
- [ ] SQLAlchemy ORM Model `EngramDictionary` in `models.py` definiert
- [ ] FK zu `banks` Tabelle funktioniert
- [ ] Indexe auf Metadaten-Felder vorhanden (B-Tree + GIN)
- [ ] Repository-Methoden für CRUD + Filter im Engine-Layer
- [ ] Migration läuft sauber mit bestehenden Migrationen (Alembic Revisionskette)
- [ ] Bestehende Tabellen und Daten sind unverändert

## Tasks

- [x] **T1 — Alembic Migration erstellen:** `alembic/versions/f1b2c3d4e5f6_add_engram_dictionary.py`. Revision `f1b2c3d4e5f6`, down_revision `e0a1b2c3d4e5`. Vollständige `engram_dictionary` Tabelle mit allen 16 Feldern. Multi-tenant via `_get_schema_prefix()`.
- [x] **T2 — Indexe in Migration:** B-Tree auf strength, layer, status, thalamus_overall. GIN auf tags. Composite auf bank_id+layer+status und bank_id+strength. Downgrade löscht alle Indexe + Tabelle.
- [x] **T3 — ORM Model definieren:** `EngramDictionary` in `models.py` mit SQLAlchemy Mapped[] + Type Hints. Relationship zu `Bank` (back_populates). CheckConstraints für layer + status. Alle Indexe analog zur Migration.
- [x] **T4 — Repository-Modul erstellen:** `hindsight_api/engine/engram_dictionary.py`. Methoden: `insert_entry`, `update_entry`, `get_by_id`, `delete_by_id`, `filter_entries`, `batch_insert`, `update_strength`, `update_access`. Nutzt `acquire_with_retry()`.
- [x] **T5 — Connectivity-Test:** `tests/test_dictionary_connectivity.py` mit 8 Tests: insert+get, missing, filter (bank/strength/layer/status), update, update_strength, update_access, delete, FK-Constraint.
