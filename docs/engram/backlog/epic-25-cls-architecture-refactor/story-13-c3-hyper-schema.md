# Story 13 — C3 Hyper-Schema-Bildung (R3)

## User Story

Als C3-Phase soll ich semantisch verwandte Schemas (Cosine ≥ 0.7) zu einem Hyper-Schema zusammenfassen können, damit der Cortex auch über Schemas selbst abstrahiert (Game-of-Life R3 auf Schema-Ebene).

## Kontext

R3 lief in der alten Architektur als Property-Extraktion auf Engram-Ebene. In der neuen Architektur ist die Property-Extraktion deterministisch in C2 verlagert (Story 07). R3 wird hier zur **Schema-zu-Hyper-Schema-Subsumption**: zwei verwandte Schemas mit systematisch unterschiedlichen Property-Werten werden zu einem Hyper-Schema verallgemeinert. Beispiel: `coffee_meeting_1on1_morning` + `coffee_meeting_1on1_afternoon` → `coffee_meeting_1on1`.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01) inkl. `link_specialization(schema_id, hyper_id)`.
- **Centroid-Helper:** `engine/schema/centroid.py::compute_centroid(vectors)` (aus Story 03).
- **Property Aggregator:** wiederverwendbar — Hyper-Schema bekommt union/generalisierte Properties.

## Akzeptanzkriterien

- [x] `engine/consolidation/c3_schema_restructure.py` mit R3-API: `find_hyper_schema_candidates`, `create_hyper_schema`, `run_r3_hyper_schema`
- [x] Pairwise Centroid-Cosine ≥ 0.7 + ≥ 1 differierende Property-Keys
- [x] HyperSchema mit Centroid = L2-renormalisierter Mean der beiden, Property-Union per Type, evidence_count = sum
- [x] `:SPECIALIZES`-Edges via `link_specialization` (aus Story 01)
- [x] R3Report-Logging
- [x] 27 neue Unit-Tests; Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — Kandidaten-Finder:** `find_hyper_schema_candidates(bank_id, neo4j, qdrant, ...)` listet aktive `:Schema`-Knoten via `list_active_schemas`, fetcht Centroids in einem Qdrant-Batch via `retrieve_many` (statt N Einzel-Searches), berechnet Cosine inline. Pairwise O(N²); N typisch im Zehner-Bereich pro Bank.
- [x] **T2 — Hyper-Schema-Erzeuger:** `create_hyper_schema(candidate, ...)` baut Centroid via `compute_centroid` (Story 03 — L2-renormalisiert), Property-Union via `_union_properties`/`_merge_property` (kategorial → set, numerisch → range+mean-of-means, temporal → union-interval). Description als deterministisches Template — die LLM-`schema_description` (Story 08) ist Engram-Schemas vorbehalten; Hyper-Schema kann später bei Bedarf nachgeneriert werden.
- [x] **T3 — Edge-Linking:** `link_specialization(neo4j, sub.id, hyper_id)` für beide Subschemas. Per-Edge-Failures werden geloggt, brechen aber den Hyper-Schema-Mint nicht ab (best-effort).
- [x] **T4 — Konstanten:** `HYPER_SCHEMA_COHESION_THRESHOLD = 0.7`, `HYPER_SCHEMA_MIN_PROPERTY_DIFF = 1` mit Drift-Guard.
- [x] **T5 — Pipeline-Integration:** `run_r3_hyper_schema(bank_id, ...) -> R3Report` end-to-end im selben Modul. Verdrahtung in einen separaten C3-Orchestrator (mit R5 aus Story 14) folgt.
- [x] **T6 — Unit-Tests:** 27 Tests in `tests/test_c3_schema_restructure.py`: Drift-Guards, 4 Cosine, 4 differing-keys, 4 merge-property pro Type, 3 union-properties, 4 find_hyper_schema_candidates (above threshold + diff, below cosine, no diff, <2 schemas), 3 create_hyper_schema (happy path, missing centroid raise, link failure logged), 4 run_r3 end-to-end (no pairs, single pair, greedy single-hyper-per-run, create-failure continues).

## Implementation Notes

- **Greedy first-match-wins:** `run_r3_hyper_schema` verwendet `used_ids: set[UUID]` — ein Subschema kann pro Run nur in maximal einem Hyper-Schema landen. Hält R3 stabil und vermeidet Konvergenz-Probleme bei dicht-gruppierten Schemas. Hyper-of-Hyper kommt nicht in dieser Story (concept §13: nur eine Ebene).
- **Centroid-Batch-Fetch:** Statt pro Schema einen Qdrant-search-Aufruf nutzen wir `retrieve_many` einmal mit allen IDs. Spart N-1 Round-Trips.
- **Property-Type-Conflict-Fallback:** Mixed-Type-Properties (z.B. categorical vs. numeric für gleichen Key) werden zu `categorical_set` reduziert — selten, deutet auf Aggregator-Drift hin, sollte sich beim nächsten C2-Lauf normalisieren.
- **Cross-DB-Drift:** Schemas ohne Qdrant-Centroid werden silent gedropped — `_fetch_schema_centroids` filtert sie raus.
