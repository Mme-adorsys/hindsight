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

- [ ] **T1 — Alembic Migration erstellen:** Neue Migration in `hindsight_api/alembic/versions/`. Revisionskette an letzte bestehende Migration anhängen. Tabelle `engram_dictionary`: `engram_id` (UUID, PK), `bank_id` (VARCHAR, FK → banks.bank_id, NOT NULL), `strength` (FLOAT, default 0.0), `layer` (VARCHAR, CHECK IN ('buffer', 'neocortex')), `abstraction_level` (FLOAT, default 0.0), `tags` (TEXT[]), `novelty` (FLOAT), `surprise` (FLOAT), `task_relevance` (FLOAT), `emotional_valence` (FLOAT), `thalamus_overall` (FLOAT), `created_at` (TIMESTAMPTZ, NOT NULL), `last_accessed` (TIMESTAMPTZ), `access_count` (INTEGER, default 0), `status` (VARCHAR, CHECK IN ('active', 'archived', 'decayed'), default 'active'), `confidence_score` (FLOAT), `session_ref` (UUID).
- [ ] **T2 — Indexe in Migration:** B-Tree auf `strength`, `layer`, `status`, `thalamus_overall`. GIN auf `tags`. Composite auf `bank_id` + `layer` + `status`. Composite auf `bank_id` + `strength`.
- [ ] **T3 — ORM Model definieren:** `EngramDictionary` Klasse in `hindsight_api/models.py`, analog zu `MemoryUnit`. SQLAlchemy Mapped[] mit Type Hints. Relationship zu `Bank` Model.
- [ ] **T4 — Repository-Modul erstellen:** Neues Modul `hindsight_api/engine/engram_dictionary.py`. Nutzt bestehenden asyncpg Pool via `db_utils.acquire_with_retry()`. Methoden: `insert_entry(pool, engram_data)`, `update_entry(pool, engram_id, updates)`, `get_by_id(pool, engram_id)`, `delete_by_id(pool, engram_id)`, `filter_entries(pool, bank_id, strength_min, layer, status, tags)`, `batch_insert(pool, entries)`, `update_strength(pool, engram_id, new_strength)`, `update_access(pool, engram_id)` (incrementiert access_count + setzt last_accessed).
- [ ] **T5 — Connectivity-Test:** Test der Migration ausführt, Eintrag anlegt, filtert, aktualisiert und löscht. Prüft FK-Constraint zu banks.
