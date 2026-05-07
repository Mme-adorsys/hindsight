# Story 09 — C2 Schema-Persistierung (Neo4j + Qdrant)

## User Story

Als C2-Phase soll ich neue Schemas atomar in Neo4j (Knoten) und Qdrant (Centroid) persistieren, damit die drei Repräsentationen (Centroid + Description + Properties) konsistent zur selben Schema-ID liegen.

## Kontext

Wenn ein Cluster nicht gematcht hat (Story 06 Creation-Pfad) und Properties + Description berechnet sind (Stories 07, 08), wird ein neuer `:Schema`-Knoten in Neo4j angelegt und sein Centroid in Qdrant geschrieben. Die `evidence_engram_ids` werden auf die Top-N stärksten Cluster-Mitglieder gesetzt (Default N=5, siehe Konstante).

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::create_schema(...)` (aus Story 01).
- **Qdrant Centroid:** `engine/qdrant_client.py::upsert_schema_centroid(schema_id, centroid, payload)` (aus Story 03).
- **Cluster-Kandidaten:** `MaturedClusterCandidate` mit `engram_ids` (alle Member-IDs).

## Akzeptanzkriterien

- [x] `persist_new_schema(payload, bank_id, *, neo4j, qdrant, pool) -> SchemaModel` (CreationPayload aus Story 07/08 statt Argumenten-Salat)
- [x] Top-N=5 Auswahl per `select_top_n_evidence(pool, ids)` — sortiert nach `engram_dictionary.strength` (= Composite-Score seit Epic 24 Story 03)
- [x] Schema-Knoten in Neo4j komplett befüllt; `created_at = last_reinforced_at = now`, `cycles_survived=1`, `status="active"`, `centroid_qdrant_id = schema_id`
- [x] Centroid in Qdrant via `upsert_schema_centroid` (kind="schema" + schema_id forced; bank_id + description_short im meta)
- [x] Atomarität: Neo4j-First, bei Qdrant-Failure → `archive_schema(neo4j, id)` Saga-Cleanup (kein hard delete — R5 Schema Death Story 14 ist die kanonische Removal-Stelle)
- [x] 11 Unit-Tests; Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — Top-N Selector:** `select_top_n_evidence(pool, engram_ids, n=SCHEMA_TOP_N_EVIDENCE)` — async, fragt PG via `engram_dictionary.strength` ab, ORDER BY DESC + LIMIT n. UUIDs missing in PG werden silent gedropped (können zwischen R1 und persist archived worden sein).
- [x] **T2 — `persist_new_schema`:** `engine/consolidation/c2_schema_writer.py`. Workflow: `uuid4` → Top-N → SchemaModel bauen → `create_schema(neo4j, model, label="Schema")` → `qdrant.upsert_schema_centroid(...)` → return.
- [x] **T3 — Atomarität:** Try/Except um den Qdrant-Aufruf; bei Failure logging + `archive_schema(neo4j, schema_id, label="Schema")` als Fallback. Archive-Failure wird separat geloggt aber nicht reraised — primärer Exception fließt durch.
- [x] **T4 — Konstante:** `SCHEMA_TOP_N_EVIDENCE = 5` in `engine/consolidation/constants.py` (concept §4.2 Top-N=5 Indexing-Theory-Pointer-Set).
- [x] **T5 — Pipeline-Integration:** `persist_creation_payloads(payloads, bank_id, *, neo4j, qdrant, pool)` als sequenzieller Wrapper über `persist_new_schema`. Per-Payload-Failures werden geloggt aber brechen den Batch nicht ab (best-effort, konsistent mit anderen C2-Stages).
- [x] **T6 — Unit-Tests:** `tests/test_c2_schema_writer.py` mit 11 Tests: 4 für `select_top_n_evidence` (sort-order, empty input, n=0, limit param), 4 für `persist_new_schema` (happy path, Qdrant-Failure → archive, Neo4j-Failure → propagate ohne Qdrant, evidence_count aus properties geerbt), 2 für Batch-Wrapper (best-effort partial failure, empty batch), 1 Drift-Guard auf `SCHEMA_TOP_N_EVIDENCE == 5`.

## Implementation Notes

- **Sequential persistence:** `persist_creation_payloads` läuft sequenziell statt `asyncio.gather`. Pro Bank ist das Schema-ID-Volumen klein und Neo4j+Qdrant Write-Contention ist real — Parallelism würde nur einen Thundering Herd auf den Cortex auslösen.
- **Saga statt 2PC:** Wir machen kein echtes Distributed-Transaction-Protocol. Neo4j-First, Qdrant-Second; bei Qdrant-Fail wird der Neo4j-Knoten archiviert (`status="archived"`). HybridRetriever (Story 15) ignoriert archived-Schemas. R5 Schema Death (Story 14) macht die kanonische Hard-Removal später.
- **Pfad-Abweichung:** Story T1 erwähnt "Cluster-Engrams" als Input zum Top-N-Selector — wir nehmen den Umweg über PG (engram_dictionary), weil dort die aktuellen Composite-Scores liegen. Im MaturedClusterCandidate steckt nur der Centroid + dominant_tags + member_tags.
