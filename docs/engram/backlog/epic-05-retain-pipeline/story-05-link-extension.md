# Story 05 — Link Extension + Neo4j (R5)

## User Story

Als System brauche ich die neuen Link-Typen (co_activated, temporal_proximity, schema) und die Dual-Write Pipeline nach PostgreSQL + Neo4j, damit die Graph-Struktur die biologisch inspirierten Verbindungstypen abbildet.

## Kontext

Hindsight erzeugt 4 Link-Typen (semantic, temporal, entity, causal) — alle nur in PostgreSQL (`memory_links` Tabelle). Wir erweitern um 3 neue Typen und schreiben alle Links auch nach Neo4j. Die neuen Typen:
- **co_activated:** Engrams die wiederholt zusammen abgerufen werden (wird bei Recall erzeugt, hier nur die Retain-Seite vorbereiten)
- **temporal_proximity:** Engrams die im selben Zeitfenster einer Session erstellt werden (basierend auf STC — Synaptic Tagging & Capture)
- **schema:** Verbindung zu einem Schema/Meta-Engram (inkrementell bei Retain: Game of Life Regel R4)

## Bestehende Codebasis

- **Link Creation:** `hindsight_api/engine/retain/link_creation.py` — `create_temporal_links_batch()`, `create_semantic_links_batch()`, `create_causal_links_batch()`. Alle schreiben in PostgreSQL `memory_links`.
- **Link Utils:** `hindsight_api/engine/retain/link_utils.py` — Pure Functions für Link-Berechnung + Batch-DB-Operationen.
- **Orchestrator:** `hindsight_api/engine/retain/orchestrator.py` — Ruft Link-Creation in Schritten 7-10 auf (innerhalb Transaktion).
- **Neo4j Client:** `hindsight_api/engine/neo4j_client.py` (aus Epic 01) — Relationships: SEMANTIC, TEMPORAL, ENTITY, CAUSAL, CO_ACTIVATED, TEMPORAL_PROXIMITY, SCHEMA, CONTRADICTION.
- **Engram Dictionary:** `hindsight_api/engine/engram_repository.py` (aus Epic 01) — Für Schema-Kandidaten Lookup.

## Akzeptanzkriterien

- [x] Temporal Proximity Links werden für Engrams in derselben Session + Zeitfenster erzeugt
- [x] Schema-Fit Check bei jedem neuen Engram (Game of Life R4: passt neues Engram zu bestehendem Schema?)
- [x] co_activated Link-Typ ist definiert, wird aber hier nur vorbereitet (Erzeugung bei Recall in Epic 07/09)
- [x] Alle bestehenden Link-Typen (semantic, temporal, entity, causal) werden zusätzlich nach Neo4j geschrieben
- [x] Neo4j-Relationships haben Properties: `weight`, `created_at`, `link_type`
- [x] PostgreSQL bleibt Source of Truth für Links, Neo4j ist denormalisiert für Graph-Traversal
- [x] Performance: Neo4j-Writes parallel zu PostgreSQL (async), nicht sequenziell

## Tasks

- [x] **T1 — Temporal Proximity Links:** In `link_creation.py` neue Funktion `create_temporal_proximity_links_batch(conn, bank_id, unit_ids, session_id, time_window_minutes=30)`. Logik: Alle Engrams mit derselben `session_id` die innerhalb von `time_window_minutes` erstellt wurden bekommen `temporal_proximity` Links. Gewichtung basierend auf zeitlicher Nähe (näher = stärker). Unterschied zu `temporal` Links: Diese sind Session-übergreifend (24h Fenster), `temporal_proximity` ist intra-Session (30min Fenster).
- [x] **T2 — Schema-Fit Check (R4 inkrementell):** In `link_creation.py` oder neuem Modul `schema_links.py`: Funktion `check_schema_fit_batch(conn, neo4j, unit_ids, embeddings) → list[SchemaLink]`. Für jedes neue Engram: Suche bestehende Schemas in Neo4j (Nodes mit `type='schema'`). Vergleiche Embedding-Similarity. Wenn Similarity > Threshold (z.B. 0.7) → Schema-Link erzeugen + Schema-Strength erhöhen. Lightweight: Kein neues Schema erstellen hier (→ Epic 13), nur bestehende Schemas stärken.
- [x] **T3 — Neo4j Link Writer:** Neues Modul `hindsight_api/engine/retain/neo4j_link_writer.py`. Funktion `write_links_to_neo4j(neo4j_client, links: list[LinkRecord])`. Schreibt Links als Neo4j Relationships. Batch-Cypher Query für Performance. Properties: `weight`, `created_at`, `source` ("retain"). Idempotent: MERGE statt CREATE (vermeidet Duplikate bei Retry).
- [x] **T4 — Dual-Write in Orchestrator:** In `orchestrator.py`: Nach jedem PostgreSQL Link-Creation Step den Neo4j Link Writer parallel aufrufen. Sammle alle erstellten Links (temporal, semantic, causal, entity, temporal_proximity, schema) → schreibe gesammelt nach Neo4j am Ende des Link-Blocks. `asyncio.gather()` für parallele Writes. Fehler in Neo4j → Log Warning, kein Rollback.
- [x] **T5 — co_activated Vorbereitung:** In `link_utils.py` oder `link_creation.py`: Nur die Type-Definition und Helper-Funktion `create_co_activation_link(neo4j, from_id, to_id, weight)` vorbereiten. Wird in Epic 07/09 bei Recall aufgerufen. Hier nur das Interface bereitstellen.
- [x] **T6 — Integration Tests:** Temporal Proximity Links innerhalb einer Session. Kein temporal_proximity Link zwischen verschiedenen Sessions. Schema-Fit erkennt bekanntes Schema. Dual-Write: Links in PostgreSQL UND Neo4j vorhanden. Neo4j-Fehler blockiert nicht die Pipeline.
