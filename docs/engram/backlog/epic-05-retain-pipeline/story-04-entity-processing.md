# Story 04 — Entity Processing Extension (R4)

## User Story

Als System soll die Entity Resolution ambige Entitäten mit LLM-Support auflösen, damit nicht jede Erwähnung von "Berlin" oder "Apple" als separate Entity gespeichert wird.

## Kontext

Hindsight's Entity Processing extrahiert Entity-Namen aus Fakten und resolved sie gegen bekannte Entities in der Bank. Bei ambigen Namen (z.B. "Apple" = Firma vs. Frucht, "Paris" = Stadt vs. Person) kann die rein textbasierte Resolution fehlschlagen. Wir fügen LLM-basierte Disambiguation hinzu (Small-Tier Task via LLM Routing aus Epic 03).

## Bestehende Codebasis

- **Entity Processing:** `hindsight_api/engine/retain/entity_processing.py` — `process_entities_batch(entity_resolver, conn, bank_id, unit_ids, facts, ...)`. Extrahiert Entities aus Fakt-Texten, merged mit User-provided Entities, ruft `link_utils.extract_entities_batch_optimized()` auf.
- **Entity Resolver:** Externer Service, injiziert in den Orchestrator. Resolved Entity-Namen zu canonical Names + Entity IDs.
- **Link Utils:** `hindsight_api/engine/retain/link_utils.py` — `extract_entities_batch_optimized()`. Batch Entity Resolution.
- **LLM Routing:** `hindsight_api/engine/llm_routing.py` (aus Epic 03) — Entity Disambiguation ist Small-Tier Task.
- **Neo4j Client:** `hindsight_api/engine/neo4j_client.py` (aus Epic 01) — Für Entity-Nodes und Entity-Links im Graph.

## Akzeptanzkriterien

- [ ] Ambige Entities werden per LLM disambiguiert bevor sie resolved werden
- [ ] LLM-Call nur bei Ambiguität (wenn Entity-Name mehrere Kandidaten in der Bank hat)
- [ ] Disambiguation nutzt Fakt-Kontext als Input für den LLM
- [ ] Neue Entities erzeugen Neo4j-Nodes (nicht nur PostgreSQL Einträge)
- [ ] Entity-Links werden sowohl in PostgreSQL als auch in Neo4j geschrieben
- [ ] Fallback bei LLM-Fehler: Erste Kandidat wird gewählt (wie bisher)
- [ ] Performance: LLM-Calls nur bei tatsächlicher Ambiguität, nicht für jede Entity

## Tasks

- [x] **T1 — Ambiguity Detection:** In `entity_processing.py` neue Funktion `detect_ambiguous_entities(entity_names: list[str], known_entities: list[Entity]) → list[AmbiguousEntity]`. Prüft ob ein Entity-Name zu mehreren bekannten Entities matcht (z.B. case-insensitive Match auf canonical_name). Gibt nur die ambigen zurück.
- [x] **T2 — LLM Disambiguation:** In `entity_processing.py` neue Funktion `async disambiguate_entities(ambiguous: list[AmbiguousEntity], fact_context: str, llm: LLM) → list[ResolvedEntity]`. LLM-Prompt (Small-Tier): "Given the context: '{fact_context}', which entity does '{name}' refer to? Options: {candidates}". Batch-fähig: Mehrere ambige Entities in einem LLM-Call.
- [x] **T3 — Entity Processing Flow erweitern:** In `process_entities_batch()`: Nach initialer Entity-Extraktion: Ambiguity Check durchführen. Bei ambigen Entities → LLM Disambiguation. Aufgelöste Entities an den bestehenden Resolution-Flow weiterreichen.
- [x] **T4 — Neo4j Entity-Nodes:** In `entity_processing.py` oder neuem Modul: Bei neuen Entities auch Neo4j-Nodes erstellen. Node-Properties: `entity_id`, `canonical_name`, `type` (person/org/location/concept). Bei Entity-Links: Neo4j Relationship `ENTITY` zwischen Engram-Nodes erstellen (parallel zu PostgreSQL `memory_links`).
- [x] **T5 — Dual-Write Entities:** Sicherstellen dass Entity-Links in beiden Systemen (PostgreSQL + Neo4j) atomar geschrieben werden. PostgreSQL: Bestehender `insert_entity_links_batch()` Flow. Neo4j: Neuer `create_entity_relationships_batch()` Aufruf. Fehler in Neo4j → Log Warning, kein Rollback der PostgreSQL-Transaktion (eventual consistency).
- [x] **T6 — Unit Tests:** Ambiguity Detection mit bekannten Entity-Duplikaten. LLM Disambiguation Mock. Neo4j Entity-Node Creation. Dual-Write Konsistenz. Fallback bei LLM-Fehler.
