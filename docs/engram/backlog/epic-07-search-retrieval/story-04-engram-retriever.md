# Story 04 — EngramRetriever (S6)

## User Story

Als System brauche ich einen neuen EngramRetriever der Neo4j + Qdrant orchestriert, damit die Hybrid-Architektur für Retrieval genutzt wird.

## Kontext

Hindsight's GraphRetriever (BFS/MPFP) arbeitet ausschließlich gegen PostgreSQL+pgvector. Der neue EngramRetriever implementiert dasselbe `GraphRetriever`-Interface, arbeitet aber fundamental anders: Seeds kommen aus Qdrant (Vector Similarity), Graph-Traversal läuft über Neo4j. Beide Retriever existieren parallel — Session Layer routet zum richtigen je nach Bank (Agent Session → MPFP/BFS, Shared → EngramRetriever).

## Bestehende Codebasis

- **GraphRetriever ABC:** `search/graph_retrieval.py` — `retrieve(pool, query_embedding_str, bank_id, fact_type, budget, query_text, semantic_seeds, temporal_seeds)`. Return: `tuple[list[RetrievalResult], MPFPTimings | None]`.
- **RetrievalResult:** `search/types.py` — Ergebnis-Datentyp mit Text, Scores, Embedding.
- **Qdrant Client:** `engine/qdrant_client.py` (aus Epic 01) — Vector Similarity Search gegen Qdrant Collections.
- **Neo4j Client:** `engine/neo4j_client.py` (aus Epic 01) — Cypher Queries, Relationship Traversal.
- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — FullEngram Lookup.

## Akzeptanzkriterien

- [x] `EngramRetriever` implementiert `GraphRetriever` Interface (name = "engram")
- [x] Seed Phase: Qdrant Vector Similarity (statt pgvector)
- [x] Traversal Phase: Neo4j Cypher (statt SQL-basierte Edge Loading)
- [x] Enrichment Phase: Engram Dictionary für Metadata (Strength, Tags, Thalamus Scores)
- [x] Mode-aware: Pattern-Set und Traversal-Depth aus ModeConfig
- [x] Bestehende MPFP/BFS Retriever bleiben funktional (parallel verfügbar)
- [x] Performance: Qdrant Seeds + Neo4j Traversal ≤ pgvector + SQL Traversal Latenz

## Tasks

- [x] **T1 — EngramRetriever Klasse:** Neues Modul `engine/search/engram_retrieval.py`. Klasse `EngramRetriever(GraphRetriever)` mit `name = "engram"`. Constructor: `(qdrant_client, neo4j_client, engram_repository, mode_config: ModeConfig | None)`.
- [x] **T2 — Qdrant Seed Phase:** `async _get_seeds(query_embedding, tags, limit) → list[SeedResult]`. Qdrant-Query: Vector Similarity mit optionalem Tag-Filter. Return: Top-k Seeds mit Score + Engram-ID. Limit mode-abhängig (Precision: 5, Exploration: 20, Analogy: 10, Validation: 10).
- [x] **T3 — Neo4j Traversal Phase:** `async _traverse(seeds, pattern_set: MPFPPatternSet) → list[TraversalResult]`. Cypher Query: MATCH-Patterns entlang der konfigurierten Edge-Types. Depth mode-abhängig (shallow=1, medium=2, deep=3). Activation Score via Edge-Weights. Deduplication der traversierten Nodes.
- [x] **T4 — Enrichment Phase:** `async _enrich(traversal_results) → list[RetrievalResult]`. Batch-Lookup gegen Engram Dictionary für Metadata (Strength, Tags, Thalamus Scores). Konvertierung zu `RetrievalResult` für Kompatibilität mit der bestehenden Fusion-Pipeline.
- [x] **T5 — retrieve() Implementation:** Zusammenführung der 3 Phasen: Seeds → Traverse → Enrich. `asyncio.gather()` wo möglich (z.B. parallele Pattern-Traversals). Timing-Tracking analog zu MPFPTimings.
- [x] **T6 — Retriever Registry:** In `retrieval.py` oder neuem Modul: `RetrieverRegistry` das Bank-Typ → Retriever mappt. Agent Session Bank → MPFPGraphRetriever (PostgreSQL). Shared Bank → EngramRetriever (Neo4j + Qdrant). Konfigurierbar, nicht hardcoded.
- [x] **T7 — Unit Tests:** Qdrant Seed Phase mit Mock-Client. Neo4j Traversal mit Mock-Client. Enrichment Mapping korrekt. retrieve() End-to-End mit Mocks. RetrieverRegistry Routing.
