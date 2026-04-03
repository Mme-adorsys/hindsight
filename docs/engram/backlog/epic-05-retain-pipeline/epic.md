# Epic 05 — Retain Pipeline Extension

> R1-R5: Erweitert die bestehende Retain Pipeline um Engram-spezifische Verarbeitung.

## Ziel

Die Hindsight Retain Pipeline verarbeitet aktuell flache Facts (text + embedding + fact_type). Epic 05 erweitert jeden Schritt der Pipeline um die neuen Engram-Konzepte: Tags statt fact_type, Thalamus Scores als Metadaten, angereicherte Embeddings, score-aware Deduplication, verbesserte Entity Resolution, und neue Link-Typen (co_activated, temporal_proximity, schema).

## Bestehende Codebasis (Hindsight)

**Retain Pipeline Dateien:**
- `hindsight_api/engine/retain/orchestrator.py` — Pipeline-Koordinator. 11 Schritte: Extract → Embed → Chunk-Store → Dedup → Insert → Entities → Temporal Links → Semantic Links → Entity Links → Causal Links → Observations. Schritte 4-11 in einer DB-Transaktion.
- `hindsight_api/engine/retain/types.py` — `ExtractedFact` (aus LLM), `ProcessedFact` (mit Embedding), `RetainContent`, `CausalRelation`.
- `hindsight_api/engine/retain/fact_extraction.py` — LLM-basierte Fact-Extraktion. `extract_facts_from_contents()`. Fact-Types: world, experience, opinion.
- `hindsight_api/engine/retain/embedding_processing.py` — `augment_texts_with_dates()` (Text + Datum), `generate_embeddings_batch()`.
- `hindsight_api/engine/retain/deduplication.py` — `check_duplicates_batch()`. Gruppiert in 12h-Buckets, semantische Duplikaterkennung.
- `hindsight_api/engine/retain/fact_storage.py` — `insert_facts_batch()`. Schreibt in `memory_units` Tabelle.
- `hindsight_api/engine/retain/entity_processing.py` — `process_entities_batch()`. Entity Extraction + Resolution.
- `hindsight_api/engine/retain/link_creation.py` — `create_temporal_links_batch()`, `create_semantic_links_batch()`, `create_causal_links_batch()`.
- `hindsight_api/engine/retain/link_utils.py` — Batch-Operationen für Links.

**Aus vorherigen Epics:**
- Epic 01: Qdrant Client (`qdrant_client.py`), Neo4j Client (`neo4j_client.py`), EngramDictionary (`engram_repository.py`)
- Epic 02: `ThalamusScores` Dataclass, `ExtractedFact.thalamus_scores` Feld, `FullEngram` Modell, Session Modell
- Epic 04: `ThalamusFilter.score()` liefert Thalamus Scores (bereits in `retain_batch_async` integriert)

## Scope

- R1: ExtractedFact/ProcessedFact um Tags + Thalamus Scores erweitern
- R2: Embedding-Anreicherung mit Session-Kontext + Thalamus-Scores
- R3: Score-aware Deduplication (höher bewerteter Fakt gewinnt)
- R4: Entity Processing mit LLM-Support für ambige Entitäten
- R5: Neue Link-Typen (co_activated, temporal_proximity, schema) + Neo4j Integration

## Nicht in Scope

- Thalamus Filter Integration (→ Epic 04, bereits erledigt)
- Session Layer Automatik (→ Epic 06)
- Consolidation Pipeline (→ Epic 12)

## Abhängigkeiten

- Epic 02 (Engram Data Model) — FullEngram, ThalamusScores
- Epic 04 (Thalamus Filter) — Scores werden vor der Pipeline berechnet

## Referenzen

- `concept.md` → Abschnitt 6 (Retain Pipeline, R1-R5)
- `concept.md` → Link-Typen Tabelle

## Stories

1. [ExtractedFact/ProcessedFact Extension](story-01-fact-extension.md) (R1)
2. [Embedding Enrichment](story-02-embedding-enrichment.md) (R2)
3. [Score-aware Deduplication](story-03-score-aware-dedup.md) (R3)
4. [Entity Processing Extension](story-04-entity-processing.md) (R4)
5. [Link Extension + Neo4j](story-05-link-extension.md) (R5)
