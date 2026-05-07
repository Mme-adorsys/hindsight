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

- [x] Neue Klasse `HybridRetriever` in `engine/search/hybrid_retriever.py` (Pfad-Abweichung: `search/` statt `retrieval/` — bestehende Recall-Module liegen alle unter `search/`, ein neues `retrieval/` würde shadowen).
- [x] `retrieve(query_embedding, bank_id, k=10, tags=None) -> list[RetrievalHit]`
- [x] Eine Qdrant-Search ohne kind-Filter, Top-K Treffer
- [x] `RetrievalHit` mit Feldern `kind ∈ {"engram", "schema"}`, `id`, `score`, `payload` (Pydantic)
- [x] Schema-Treffer enthalten `description`, `properties`, `evidence_engram_ids`, `evidence_count`, `schema_label`
- [x] Engram-Treffer enthalten `text`, `fact_type`, `context`, `tags` aus PG memory_units ⨝ engram_dictionary
- [x] Factory `build_default_hybrid_retriever(pg_pool)` extrahiert Qdrant+Neo4j aus dem bestehenden Default-Retriever; volle Orchestrator-Routung folgt nach Story 16/17, Cleanup in Story 18.
- [x] 14 Unit-Tests (Search-Wiring, Engram-Enrichment, Schema-Enrichment, Mixed-Order, Lookup-Failure-Best-Effort); Integration-Test verschoben auf Block E (Story 19/20 E2E).

## Tasks

- [x] **T1 — `RetrievalHit` Pydantic-Modell:** im selben Modul `engine/search/hybrid_retriever.py` (statt eigenem `models/retrieval.py` — kompakter und kein neues Top-Level-Package).
- [x] **T2 — `HybridRetriever`:** Qdrant-Search ohne kind-Filter, kind-Detection per `payload.kind`, ID-Extraktion aus `engram_id`/`schema_id`. Unparseable IDs werden geloggt aber brechen den Lauf nicht ab.
- [x] **T3 — Schema-Hit-Anreicherung:** `schema_lookup` als injizierte Awaitable (Pattern aus Stories 06/09 wiederverwendet); Default-Pfad nutzt `engine.schema.schema_repository.get_schema`. Lookup-Fehler werden geloggt, Hit bleibt erhalten (best-effort).
- [x] **T4 — Engram-Hit-Anreicherung:** `engram_lookup` als injizierte Awaitable (Test-Stub) oder direkter PG-Pool (`memory_units` LEFT JOIN `engram_dictionary` für Tags). UUID-Cast über asyncpg `$1::uuid[]`.
- [x] **T5 — Recall-Orchestrator-Brücke:** `build_default_hybrid_retriever(pg_pool)` Factory greift via `get_default_graph_retriever()` auf den bereits gewireten `EngramRetriever` zu und konstruiert `HybridRetriever` mit dessen Clients. Volle Pipeline-Routung (RRF/CE/Score-Replacement) bleibt Story 18, weil sie Top-N-Evidence-Auflösung (S16) und Mode-Gewichtung (S17) voraussetzt.
- [x] **T6 — Unit-Tests:** 14 Tests in `tests/test_hybrid_retriever.py` — alle Akzeptanzpunkte gepinnt; keine Live-DB nötig.
