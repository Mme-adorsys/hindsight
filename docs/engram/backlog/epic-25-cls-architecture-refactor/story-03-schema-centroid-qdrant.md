# Story 03 — Schema-Centroid in Qdrant

## User Story

Als System sollen Schema-Centroids in derselben Qdrant-Collection wie Engram-Embeddings liegen, unterschieden über `payload.kind ∈ {"engram", "schema"}`, damit Recall mit einer einzigen Vektor-Search beide Räume durchsucht.

## Kontext

Im alten Modell gab es nur Engram-Embeddings in Qdrant. Schemas hatten kein Embedding (sondern nur Engram-Referenzen). Im neuen Modell hat das Schema einen eigenen Centroid (`numpy.mean` über die Evidence-Engram-Embeddings), der in Qdrant abgelegt wird. Beim Recall durchsucht eine einzige Vector-Search die kombinierte Collection — Treffer können Engrams oder Schemas sein.

## Bestehende Codebasis

- **Qdrant Client:** `engine/qdrant_client.py` mit `upsert_point(id, vector, payload)`.
- **Engram Schreibpfad:** `engine/retain/...` schreibt Engram-Embeddings nach Qdrant mit `payload = {kind: "engram", engram_id, ...}` (Erweiterung im Zuge dieser Story).
- **Schema-Knoten:** entsteht in Story 09 — hier wird nur die Qdrant-Seite vorbereitet.

## Akzeptanzkriterien

- [x] Alle Engram-Punkte in Qdrant haben `payload.kind = "engram"` (Migration `scripts/dev/migrate_qdrant_kind_payload.py`)
- [x] Neue Funktion `upsert_schema_centroid(schema_id, centroid_vector, schema_meta)` schreibt mit `payload.kind = "schema"`
- [x] `search_similar` akzeptiert optionalen Filter `kind ∈ {"engram", "schema", None}`
- [x] Helper-Funktion `compute_centroid(embeddings)` mit `numpy.mean` + L2-Normalisierung
- [x] Unit-Tests für Schreibpfad + Filter-Search

## Tasks

- [x] **T1 — Migration Qdrant-Payload:** `scripts/dev/migrate_qdrant_kind_payload.py` (Story-Pfad `scripts/...` → tatsächlich unter `scripts/dev/` neben `seed_bank.py`/`reset_bank.py`). Idempotent: scrollt alle Punkte, setzt `kind="engram"` nur dort wo weder `engram` noch `schema` gesetzt ist; `--dry-run`-Modus, env-Defaults für Qdrant-URL/Key/Collection.
- [x] **T2 — Engram-Schreibpfad:** `qdrant_client.upsert_point` und `batch_upsert` forcieren `kind="engram"` (Caller kann nicht mehr überschreiben — Story-03-Invariant). Damit bleiben bestehende Caller in `engine/engram_storage.py` ohne Diff kompatibel; legacy `'neocortex'`-Schreiber in `consolidation/` sind nach Story 02 ohnehin tot.
- [x] **T3 — Schema-Centroid-Schreibpfad:** Neue `qdrant_client.upsert_schema_centroid(schema_id, centroid, schema_meta)` schreibt `kind="schema"` + `schema_id`; Caller-Override beider Felder wird ignoriert.
- [x] **T4 — Centroid-Compute-Helper:** `engine/schema/centroid.py::compute_centroid(embeddings)` mit `numpy.mean` + L2-Normalisierung. Wirft `ValueError` bei leerem Input oder Zero-Vector (degenerate cancel-out).
- [x] **T5 — Such-Wrapper Erweiterung:** `qdrant_client.search_similar(..., kind=None)` plus private `_build_filter` staticmethod, die einen optionalen Caller-Filter mit einer optionalen `kind`-`FieldCondition` über die `must`-Liste komponiert (kein Replace).
- [x] **T6 — Unit-Tests:** `tests/test_schema_centroid.py` mit 17 Unit-Tests: 5 für `compute_centroid` (orthogonale Vektoren, kollabierte, Single-Vector, Empty-Error, Zero-Vector-Error), 2 für Engram-Upsert-Forcierung, 3 für `upsert_schema_centroid`, 3 für `search_similar`-Filter-Komposition, 4 für Migration-Smoke-Test (skip already tagged, stamp unkinded, dry-run no-write, missing collection short-circuit).
