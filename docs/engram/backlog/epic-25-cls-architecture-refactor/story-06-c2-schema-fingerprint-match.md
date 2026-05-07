# Story 06 — C2 Schema-Fingerprint-Match (Cosine ≥ 0.85)

## User Story

Als C2-Phase soll ich vor jeder Schema-Erzeugung prüfen, ob bereits ein passendes Schema existiert (Cosine ≥ 0.85 zwischen Cluster-Centroid und Schema-Centroid), damit existierende Schemas verstärkt statt Duplikate angelegt werden.

## Kontext

Wenn ein gereifter Cluster (Story 05) als Schema-Kandidat identifiziert ist, muss zwischen "neues Schema erzeugen" (Story 09) und "bestehendes Schema verstärken" (Story 10) entschieden werden. Diese Story liefert den Match-Schritt: Qdrant-Search mit `kind="schema"` gegen den Cluster-Centroid; Treffer ≥ 0.85 → Reinforcement-Pfad, sonst → Erzeugungs-Pfad.

## Bestehende Codebasis

- **Qdrant Client:** `engine/qdrant_client.py::search(query_vector, kind="schema", limit=1)` (aus Story 03).
- **Schema Repository:** `engine/schema/schema_repository.py::get_schema(id)` (aus Story 01).

## Akzeptanzkriterien

- [x] `match_existing_schema(qdrant, schema_lookup, centroid, bank_id) -> tuple[SchemaModel | None, float]`
- [x] Qdrant `search_similar(kind="schema", limit=1, filters={must:[bank_id]})`
- [x] Cosine ≥ 0.85 → `(SchemaModel, score)`
- [x] Cosine < 0.85 oder kein Treffer → `(None, best_score)` (oder `(None, 0.0)` bei leerem Bank)
- [x] `partition_for_consolidation` in `c2_pattern_recognition.py` ruft Match pro `MaturedClusterCandidate` und sortiert in `reinforcement`/`creation`-Buckets
- [x] Unit-Tests inklusive Bank-Isolation und Drift-Guard; Integration-Test über echtes Qdrant verschoben auf Block E (Story 19/20 Knowledge-Evolution-E2E)

## Tasks

- [x] **T1 — Match-Funktion:** `engine/consolidation/c2_schema_match.py::match_existing_schema(qdrant, schema_lookup, centroid, bank_id, threshold=SCHEMA_MATCH_THRESHOLD)`. Akzeptiert injizierte `schema_lookup`-Awaitable statt Neo4j-Client direkt — vermeidet Cross-Module-Deps. Defensive Checks: Qdrant-Hit ohne `schema_id` → Miss; `schema_lookup` returns None (Cross-DB-Drift) → Miss.
- [x] **T2 — Konstanten:** `engine/consolidation/constants.py` mit `SCHEMA_MATCH_THRESHOLD = 0.85` (concept §13 R4).
- [x] **T3 — Pipeline-Integration:** `partition_for_consolidation(matured, bank_id, qdrant, schema_lookup) -> ConsolidationPlan(reinforcement, creation)` in `c2_pattern_recognition.py`. Plus drei frozen dataclasses `MatchedForReinforcement`, `UnmatchedForCreation`, `ConsolidationPlan`. Immature Kandidaten werden gedroppt — sie warten in `c2_cluster_fingerprints` auf den nächsten Zyklus.
- [x] **T4 — Logging:** INFO-Logline pro Run: `bank, reinforcement, creation, skipped_immature` Counter. DEBUG für Best-Score pro Miss.
- [x] **T5 — Unit-Tests:** 9 Tests in `test_c2_schema_match.py` (above-threshold, below-threshold, empty hits, kind+bank-Filter-Shape, explicit-threshold-override, Cross-DB-Drift, missing-schema_id, Bank-Isolation, Drift-Guard) + 3 Integration-Tests in `test_c2_pattern_recognition.py` (mixed-inputs Bucket-Split, immature gedropt, empty-input).

## Implementation Notes

- **Schema-Lookup-Injection:** Statt Neo4j-Client als Argument (würde harte Deps in `c2_schema_match` ziehen), nimmt die Funktion eine Awaitable `(schema_id) -> SchemaModel | None`. Caller wired typischerweise `functools.partial(schema_repository.get_schema, neo4j_client)`.
- **Threshold-Trennung:** `SCHEMA_MATCH_THRESHOLD` (Cluster-vs-Schema) und `MATCH_COSINE_THRESHOLD` (Cluster-vs-Cluster, Story 05) sind bewusst separate Konstanten am gleichen Wert 0.85 — verschiedene Mechanismen, beide aus concept §13 R2/R4.
- **Cross-DB-Drift-Tolerance:** Wenn Qdrant einen `schema_id` zurückgibt, den Neo4j nicht (mehr) kennt, behandeln wir das als Miss + WARNING-Log. Kein Hard-Fail — Cleanup ist Sache von Story 18 (R5 Schema Death + Garbage-Collect).
