# Story 15 — HybridRetriever (Engram + Schema Mischtreffer)

## User Story

Als Recall-Pipeline soll ich mit einer einzigen Vektor-Search beide Räume (Engram-Embeddings + Schema-Centroids) durchsuchen und gemischte Treffer zurückgeben, damit der Reflect/Constructive-Memory-Pfad sowohl konkrete Episoden als auch Schemas bekommt.

## Kontext

In der alten Architektur durchsuchte der Recall nur Engram-Embeddings (Schemas hatten kein eigenes Embedding). Mit Story 03 ist der Schema-Centroid in Qdrant — und beide Räume teilen eine Collection. Eine einzelne Vektor-Search liefert ein gemischtes Ergebnis (Engrams + Schemas), unterschieden über `payload.kind`. Der HybridRetriever ersetzt den bisherigen `EngramRetriever`.

## Bestehende Codebasis

- **Recall Orchestrator:** `engine/recall_orchestrator.py` mit aktuellem EngramRetriever.
- **Qdrant Search:** `qdrant_client.search(query_vector, kind=None, ...)` mit optionalem kind-Filter (aus Story 03).
- **Scoring:** Bestehende Scoring-Formel aus Epic 24.

## Akzeptanzkriterien

- [ ] Neue Klasse `HybridRetriever` in `engine/retrieval/hybrid_retriever.py`
- [ ] `retrieve(query, mode, bank_id, k=10) -> list[RetrievalHit]`
- [ ] Eine Qdrant-Search ohne kind-Filter, Top-K Treffer
- [ ] `RetrievalHit` mit Feldern `kind ∈ {"engram", "schema"}`, `id`, `score`, `payload`
- [ ] Schema-Treffer enthalten `description`, `properties`, `evidence_engram_ids` (für Story 16 nachgelagerte Auflösung)
- [ ] Engram-Treffer enthalten den vollen Engram-Content
- [ ] Recall-Orchestrator routet alle Bank-Typen auf den HybridRetriever (alter EngramRetriever wird in Story 18 entfernt)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `RetrievalHit` Pydantic-Modell:** `models/retrieval.py` mit Feldern oben.
- [ ] **T2 — `HybridRetriever`:** In `engine/retrieval/hybrid_retriever.py`. Query-Embedding generieren, Qdrant-Search ohne kind-Filter, Treffer in `RetrievalHit`-Liste mappen.
- [ ] **T3 — Schema-Hit-Anreicherung:** Bei `kind="schema"` Schema-Knoten aus Neo4j nachladen (description, properties, evidence_engram_ids).
- [ ] **T4 — Engram-Hit-Anreicherung:** Bei `kind="engram"` Engram-Content aus PostgreSQL nachladen.
- [ ] **T5 — Recall-Orchestrator umstellen:** `recall_orchestrator.py` nutzt nur noch `HybridRetriever`. Alter EngramRetriever wird nicht mehr aufgerufen (Cleanup in Story 18).
- [ ] **T6 — Unit-Tests:** (a) Query-Embedding mit Schema-Treffer → RetrievalHit kind="schema" mit description. (b) Query mit Engram-Treffer → kind="engram" mit content. (c) Mischtreffer → beide Typen, korrekt sortiert nach score.
