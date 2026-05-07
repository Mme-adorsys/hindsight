# Story 10 — C2 Schema-Reinforcement (R4 batch)

## User Story

Als C2-Phase soll ich bei einem Schema-Match (aus Story 06) das bestehende Schema verstärken — `evidence_count` erhöhen, Top-N aktualisieren, Centroid neu mitteln, Properties verfeinern, `last_reinforced_at` setzen — ohne ein neues Schema anzulegen.

## Kontext

R4 ("Reinforcement/Growth") läuft sowohl batch in C2 (für Cluster-Treffer) als auch incremental beim Retain (Story 12). Diese Story implementiert nur den batch-Pfad: ein gereifter Cluster matcht ein bestehendes Schema → das Schema absorbiert die neuen Engrams als Evidence.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py::update_schema(id, props)` (aus Story 01).
- **Qdrant Centroid:** Update via `upsert_schema_centroid()` (Idempotent, aus Story 03).
- **Property Aggregator:** wiederverwendbar — wird mit aktuellem Evidence-Set + neuen Engrams gefüttert.

## Akzeptanzkriterien

- [x] `reinforce_schema(matched, bank_id, *, neo4j, qdrant, pool) -> SchemaModel` (MatchedForReinforcement aus Story 06 statt Argumenten-Salat)
- [x] Schritte 1-5+6: evidence_count += cluster_size, Top-N refresh, weighted_centroid, properties re-aggregation, last_reinforced_at=now, cycles_survived+=1
- [x] Persistierung: Neo4j `update_schema` first, dann Qdrant centroid; Qdrant-Fail wird geloggt aber NICHT rolled back (idempotent — nächster R4-Match korrigiert)
- [x] 11 neue Unit-Tests + Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — `reinforce_schema()`:** In `engine/consolidation/c2_schema_writer.py`. Signatur nimmt komplettes `MatchedForReinforcement` (cluster + schema + cosine).
- [x] **T2 — `weighted_centroid(old, old_w, new, new_w)`:** L2-renormalisierter Mittelwert. Validiert non-negative Weights, total > 0, equal Dimensionen, kein Zero-Vector.
- [x] **T3 — Property-Refresh:** Neuer Helper `_fetch_member_tags(pool, ids)` lädt Tags der neuen Top-N aus PG; `aggregate_properties` läuft drüber. **Aktueller** Top-N-Snapshot, kein historisches Merge.
- [x] **T4 — Atomarität (anders als Story 09):** Update-Saga statt Create-Saga. Neo4j-Update ist idempotent — Qdrant-Centroid-Refresh-Failure wird nur geloggt, kein Archive (das Schema bleibt valid, Centroid wird beim nächsten R4-Match aufholen). Schema-Death (Story 14) ist nicht unsere Sache hier.
- [x] **T5 — Pipeline-Integration:** `reinforce_matched(reinforcement, bank_id, *, neo4j, qdrant, pool)` als sequenzieller best-effort Wrapper.
- [x] **T6 — Unit-Tests:** 11 neue Tests in `tests/test_c2_schema_writer.py`: 5 für `weighted_centroid` (Unit-Blend, gewichtetes Dominanz, Negative-Weight-Error, Dim-Mismatch-Error, Zero-Total-Error), 4 für `reinforce_schema` (Happy-Path inkl. Property-Refresh-Wechsel coffee→tea, Qdrant-Lookup-Failure → Bootstrap, Qdrant-Centroid-Upsert-Failure → log-only, Empty-Cluster → no-op), 2 für Batch-Wrapper (best-effort partial failure, empty batch).

## Implementation Notes

- **Saga-Asymmetrie zu Story 09:** Story 09 macht Neo4j-First + Qdrant-Cleanup-on-Fail (archive). Story 10 macht Neo4j-First + Qdrant-Log-on-Fail (kein rollback). Warum: bei Update ist die Neo4j-Seite idempotent valid (Schema existiert, hat alte oder neue Properties — beides konsistent); bei Create wäre ein Halbzustand (Neo4j-Knoten ohne Qdrant-Centroid) ein Phantom für HybridRetriever. Reinforcement-Centroid-Drift heilt sich beim nächsten R4-Match selbst.
- **Centroid-Lookup:** SchemaModel trägt nur `centroid_qdrant_id`, nicht den Vektor. `_fetch_schema_centroid` macht einen Qdrant-`get_by_id`-Roundtrip. Bei Lookup-Failure (Drift, Outage) wird mit dem neuen Cluster-Centroid bootstrappt — verhindert Crash, akzeptiert kleinen Quality-Loss.
- **Property-Refresh-Granularität:** Nur die NEUEN Top-N werden re-aggregiert. Historische Engrams die aus der Top-N gefallen sind verlieren ihren Property-Beitrag — gewollt, weil die Top-N die "Kanonische Repräsentation" sind (concept §4.2 Indexing Theory).
