# Story 04 — C2 HDBSCAN Cluster Detection (R1+R2)

## User Story

Als C2-Phase soll ich auf allen aktiven Buffer-Engrams ein HDBSCAN-Clustering im Embedding-Raum durchführen, damit Pattern-Kandidaten (≥3 Mitglieder, paarweise Cosine ≥ 0.75) gefunden werden — die Grundlage für Schema-Erzeugung.

## Kontext

Game-of-Life Regel R1 (Birth) verlangt ≥ 3 Engrams mit hoher paarweiser Ähnlichkeit. R2 (Maturation) verlangt zusätzlich, dass der Cluster ≥ 2 C2-Zyklen überlebt — Persistierung des Cluster-Fingerprints siehe Story 05. Diese Story implementiert die reine Cluster-Detection und R1-Filterung in einem C2-Lauf.

## Bestehende Codebasis

- **NCR Orchestrator:** `engine/consolidation/ncr_orchestrator.py` — Einstiegspunkt für C2.
- **Qdrant Embeddings:** abrufbar via `engine/qdrant_client.py::scroll(filter={kind:"engram", layer:"buffer"})`.
- **HDBSCAN:** Library `hdbscan>=0.8.33` (zu pyproject.toml hinzufügen).

## Akzeptanzkriterien

- [x] `engine/consolidation/c2_pattern_recognition.py` mit `detect_clusters(bank_id, pool, qdrant) -> list[ClusterCandidate]`
- [x] Liest aktive Buffer-Engrams via `engram_dictionary.filter_entries` (PG Pointer Index, concept §3) und holt deren Embeddings via `qdrant.retrieve_many`
- [x] HDBSCAN mit `min_cluster_size=3`, `min_samples=2`, `metric="euclidean"` auf L2-normalisierten Vektoren (monoton zu Cosine — `‖u-v‖² = 2-2·cos(u,v)` für Unit-Vektoren)
- [x] Filter: mean pairwise Cosine ≥ 0.75 (Story-Konstante `COHESION_THRESHOLD`)
- [x] Output: `ClusterCandidate(engram_ids: tuple[str,...], member_embeddings: tuple[tuple[float,...],...], cohesion: float)` als frozen dataclass
- [x] Robustheit: <3 Buffer-Engrams, fehlende Qdrant-Vektoren, HDBSCAN-Exceptions → leere Liste mit Skipped-Reason im Log
- [x] Unit-Tests mit synthetischen Embeddings + DetectionStats-Logging

## Tasks

- [x] **T1 — Dependency:** `hdbscan>=0.8.33` in `hindsight-api/pyproject.toml`, `uv sync` durchgelaufen.
- [x] **T2 — `ClusterCandidate`:** Story-Pfad `models/cluster.py` existiert nicht; frozen dataclass kolokiert in `engine/consolidation/c2_pattern_recognition.py` (analog zur Story-01/02-Naming-Abweichung). Plus `DetectionStats` für T4-Logging.
- [x] **T3 — `detect_clusters`:** PG-First-Pipeline (filter_entries → retrieve_many → L2-Norm → HDBSCAN → mean-pairwise-cosine → R1-Filter). Neuer Helper `qdrant_client.retrieve_many(ids)` für Batch-Vektor-Abfrage.
- [x] **T4 — Logging:** `DetectionStats(bank_id, buffer_engrams, raw_clusters, cohesion_filtered, candidates, skipped_reason, cluster_details)` — INFO-Logline pro Run, DEBUG-Detail pro Cluster (size, cohesion, kept).
- [x] **T5 — Unit-Tests:** 11 Tests in `tests/test_c2_pattern_recognition.py`: 3 Helpers (L2, mean-pairwise-cosine), 6 Pipeline-Tests (3 Cluster → 3 Kandidaten, scattered → empty, low cohesion → filtered, <3 engrams → short-circuit, HDBSCAN-Exception → empty, fehlende Qdrant-Vektoren → empty), DetectionStats-Defaults, plus parametrisierter Drift-Guard auf `COHESION_THRESHOLD == 0.75`.

## Implementation Notes

- **HDBSCAN-Tuning:** `min_samples=2` (statt Default = `min_cluster_size`) hält den Algorithmus permissiv genug, die kleinen R1-Cluster (Größe 3) tatsächlich zu surfen. Default-Setting würde 3-Punkt-Bundles als Noise verwerfen weil mutuell-erreichbare Nachbarschaft fehlt.
- **Metric-Workaround:** HDBSCAN unterstützt `metric="cosine"` nicht direkt (BallTree-Kompatibilität). Wir L2-normalisieren die Vektoren und nutzen `euclidean` — für Unit-Vektoren ist das streng monoton zu Cosine, also clusterstrukturerhaltend.
