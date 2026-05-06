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

- [ ] Alle Engram-Punkte in Qdrant haben `payload.kind = "engram"` (Migration für Bestand)
- [ ] Neue Funktion `upsert_schema_centroid(schema_id, centroid_vector, schema_meta)` schreibt mit `payload.kind = "schema"`
- [ ] Such-Wrapper akzeptiert optionalen Filter `kind ∈ {"engram", "schema", None}` für Recall- und C2-Use-Cases
- [ ] Helper-Funktion `compute_centroid(embeddings: List[Vector]) -> Vector` (numpy.mean, normalisiert)
- [ ] Unit-Tests für Schreibpfad + Filter-Search

## Tasks

- [ ] **T1 — Migration Qdrant-Payload:** Skript `scripts/migrate_qdrant_kind_payload.py` setzt `kind="engram"` auf allen bestehenden Punkten (idempotent, batch).
- [ ] **T2 — Engram-Schreibpfad anpassen:** In `engine/retain/...` wird beim Upsert eines Engram-Embeddings explizit `payload.kind = "engram"` mitgeschrieben.
- [ ] **T3 — Schema-Centroid-Schreibpfad:** Neue Funktion `qdrant_client.upsert_schema_centroid(schema_id, vector, schema_meta_dict)` mit `payload = {kind: "schema", schema_id, description_short, ...}`.
- [ ] **T4 — Centroid-Compute-Helper:** `engine/schema/centroid.py::compute_centroid(embeddings)` mit numpy.mean + L2-Normalisierung.
- [ ] **T5 — Such-Wrapper Erweiterung:** `qdrant_client.search(query_vector, kind=None, limit=k, filter=...)` mit optionalem Filter auf `payload.kind`.
- [ ] **T6 — Unit-Tests:** Centroid-Berechnung (gegen Hand-Beispiel). Upsert + Filter-Search (kind="schema" liefert nur Schemas). Migration-Smoke-Test.
