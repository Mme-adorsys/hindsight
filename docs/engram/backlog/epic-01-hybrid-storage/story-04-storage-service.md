# Story 04 — Engram Storage Service

## User Story

Als System brauche ich eine zentrale Service-Schicht im Engine-Layer, die Engram-Operationen über alle drei Datenbanken synchron hält, damit kein inkonsistenter Zustand entsteht.

## Kontext

Ein Engram lebt in drei Systemen: PostgreSQL Dictionary (Metadaten), Qdrant (Content + Embedding), Neo4j (Graph-Knoten + Relationships). Der Engram Storage Service ist die **einzige Schicht die direkt auf alle drei Datenbanken schreibt**. Alle höheren Schichten (Retain Pipeline, Consolidation, Reflect) gehen durch diesen Service. Er wird Teil des Engine-Layers und nutzt die in Stories 01-03 erstellten Client-Module.

## Bestehende Codebasis

- **Engine Pattern:** `hindsight_api/engine/memory_engine.py` → MemoryEngine Klasse. Der Storage Service wird als eigenständiges Modul im Engine-Layer erstellt, NICHT als Erweiterung der MemoryEngine. MemoryEngine nutzt den Service für Engram-Operationen.
- **Interface Pattern:** `hindsight_api/engine/interface.py` → MemoryEngineInterface ABC. Der Storage Service bekommt ein eigenes Interface.
- **DB Access Pattern:** `hindsight_api/engine/db_utils.py` → Pool + `acquire_with_retry()`. Storage Service bekommt alle drei Connection-Objekte bei Initialisierung.
- **Response Models:** `hindsight_api/engine/response_models.py` → Pydantic Modelle. FullEngram Modell hier hinzufügen.
- **Retain Pattern:** `hindsight_api/engine/retain/fact_storage.py` → Batch INSERT Logik für MemoryUnits. Analoges Pattern für Engram Batch-Operationen.

## Akzeptanzkriterien

- [x] `EngramStorageService` Interface und Implementierung im Engine-Layer
- [x] `FullEngram` Datenmodell in `response_models.py`
- [x] Create: Engram wird atomar in allen drei Systemen angelegt
- [x] Read: Daten aus allen drei Systemen zusammengeführt in FullEngram
- [x] Update: Metadaten-Update propagiert korrekt (Dictionary + Neo4j)
- [x] Delete: Kaskadierend über alle drei Systeme
- [x] Compensation bei partiellem Failure (Cleanup bereits geschriebener Einträge)
- [x] CRUD-Lifecycle Test über alle drei Systeme
- [x] Bestehende MemoryUnit-Operationen in MemoryEngine unverändert

## Tasks

- [x] **T1 — FullEngram Modell definieren:** In `hindsight_api/engine/response_models.py` neues Pydantic Modell: `FullEngram { engram_id: UUID, metadata: EngramMetadata, content: EngramContent, relationships: List[EngramRelationship] }`. Untermodelle: `EngramMetadata` (strength, layer, tags, thalamus_scores, timestamps, status — aus Dictionary), `EngramContent` (text, embedding — aus Qdrant), `EngramRelationship` (target_id, rel_type, weight, properties — aus Neo4j).
- [x] **T2 — Service Interface definieren:** Neues Modul `hindsight_api/engine/engram_storage.py`. ABC `EngramStorageInterface` mit Methoden: `create_engram(data) → engram_id`, `read_engram(engram_id) → FullEngram`, `read_metadata(engram_id) → EngramMetadata`, `update_metadata(engram_id, updates)`, `update_content(engram_id, text, embedding)`, `add_relationship(source_id, target_id, rel_type, properties)`, `remove_relationship(source_id, target_id, rel_type)`, `delete_engram(engram_id)`, `exists(engram_id) → bool`, `batch_create(engrams) → List[engram_id]`.
- [x] **T3 — Create-Flow implementieren:** UUID generieren → Dictionary INSERT → Qdrant Upsert → Neo4j Node Create. Bei Fehler: Compensation (reverse-delete in umgekehrter Reihenfolge). Logging für jede Phase.
- [x] **T4 — Read-Flow implementieren:** Parallel-Fetch per engram_id aus allen drei Systemen. Zusammenführen in FullEngram. Optionaler `fields`-Parameter der steuert welche Quellen abgefragt werden (z.B. nur Metadaten → nur Dictionary, schnell).
- [x] **T5 — Update-Flows implementieren:** Routing-Logik: Metadaten → Dictionary + Neo4j Properties. Content → Qdrant. Relationships → nur Neo4j. Strength/Scores → Dictionary + Neo4j (beide führen diese Felder).
- [x] **T6 — Delete-Flow implementieren:** Kaskadierend: Neo4j Relationships → Neo4j Node → Qdrant Point → Dictionary Entry. Reihenfolge so gewählt dass bei Abbruch keine verwaisten Relationships bleiben.
- [x] **T7 — Compensation & Error Handling:** Compensation-Strategie für jede Create/Update-Phase. Retry-Logik für transiente Fehler (analog `db_utils.py` Pattern). Strukturiertes Logging für alle Cross-DB-Operationen.
- [x] **T8 — Integration in MemoryEngine:** In `memory_engine.py` den `EngramStorageService` als Dependency injizieren (neben bestehendem Pool). Initialisierung in `__init__` oder Factory-Methode. MemoryEngine delegiert Engram-Operationen an den Service, behält eigene MemoryUnit-Operationen.
- [x] **T9 — CRUD-Lifecycle Test:** Create → Read (alle 3 DBs prüfen) → Update Metadata → Update Content → Add Relationship → Read (Updates verifizieren) → Delete → Read (alle 3 DBs leer).
