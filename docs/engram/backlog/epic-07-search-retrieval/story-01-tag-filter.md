# Story 01 — Tags-basierte Filterung (S1)

## User Story

Als System soll die Retrieval Pipeline Tags statt fact_type für Filterung verwenden, damit die 12 parallelen Type-Queries durch einen einzigen type-agnostischen Query ersetzt werden.

## Kontext

Hindsight iteriert über `fact_type in ['world', 'experience', 'opinion']` und ruft `retrieve_parallel()` separat pro Type auf — das sind 12 DB-Queries (4 Methoden × 3 Types). Mit Tags und der Hybrid-Architektur ist das obsolet: Qdrant liefert Seeds type-agnostisch, Neo4j traversiert type-agnostisch. Optional können Tags als Filter mitgegeben werden.

## Bestehende Codebasis

- **recall_async:** `memory_engine.py` — Iteriert über `fact_type` Liste, ruft `retrieve_parallel()` pro Type.
- **retrieve_parallel:** `search/retrieval.py` — Nimmt `fact_type: str` als Parameter, filtert SQL-Queries mit `AND fact_type = $3`.
- **retrieve_semantic:** `search/retrieval.py` — pgvector Query mit fact_type Filter.
- **retrieve_bm25:** `search/retrieval.py` — tsquery mit fact_type Filter.
- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — Tags als JSONB Feld.

## Akzeptanzkriterien

- [ ] `recall_async()` ruft `retrieve_parallel()` einmal statt pro fact_type
- [ ] `retrieve_parallel()` akzeptiert optionalen `tags: list[str] | None` statt `fact_type: str`
- [ ] Ohne Tags: Kein Filter (alle Engrams, type-agnostisch)
- [ ] Mit Tags: JSONB contains Query (`tags @> $1`)
- [ ] Bestehende `fact_type` API bleibt als Convenience (wird intern zu Tag konvertiert)
- [ ] Performance: Ein Query statt 12 → messbare Latenz-Reduktion

## Tasks

- [ ] **T1 — retrieve_parallel Signature:** In `retrieval.py`: Parameter `fact_type: str` ersetzen durch `tags: list[str] | None = None`. Alle internen Aufrufe anpassen.
- [ ] **T2 — SQL-Queries umstellen:** In `retrieve_semantic()`, `retrieve_bm25()`, `retrieve_temporal()`: `AND fact_type = $3` ersetzen durch optionalen `AND tags @> $1::jsonb` (nur wenn Tags übergeben). Ohne Tags: WHERE-Clause entfällt.
- [ ] **T3 — recall_async Loop entfernen:** In `memory_engine.py`: Die `for fact_type in fact_types` Schleife entfernen. Stattdessen einmal `retrieve_parallel()` aufrufen mit optionalen Tags. fact_type API-Parameter → Tags Konvertierung: `fact_type='opinion'` → `tags=['opinion']`.
- [ ] **T4 — Qdrant Tag-Filter:** Für den EngramRetriever (Story 04): Qdrant-Query mit optionalem Payload-Filter auf Tags. Qdrant unterstützt JSON-basierte Filter nativ.
- [ ] **T5 — Unit Tests:** Retrieval ohne Tags (alle Ergebnisse). Retrieval mit Tags (gefiltert). fact_type → Tags Konvertierung. Performance-Test: 1 Query vs. 3 Queries Timing.
