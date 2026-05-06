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

- [ ] Neue Datei `engine/consolidation/c2_pattern_recognition.py` mit Funktion `detect_clusters(bank_id) -> list[ClusterCandidate]`
- [ ] Liest alle aktiven Buffer-Engram-Embeddings (`status=active`, `layer=buffer`) der Bank
- [ ] HDBSCAN mit `min_cluster_size=3`, `metric="cosine"`
- [ ] Filter: paarweise Cosine ≥ 0.75 (Cluster mit niedrigerer Kohäsion verwerfen)
- [ ] Output: `ClusterCandidate { engram_ids: list[UUID], member_embeddings: list[vector], cohesion: float }`
- [ ] Robustheit: bei <3 Buffer-Engrams oder HDBSCAN-Failure → leere Liste
- [ ] Unit-Tests mit synthetischen Embeddings

## Tasks

- [ ] **T1 — Dependency:** `hdbscan>=0.8.33` in `pyproject.toml`. uv-Lockfile aktualisieren.
- [ ] **T2 — `ClusterCandidate` Pydantic-Modell:** `models/cluster.py` mit Feldern engram_ids, member_embeddings, cohesion (gemittelte paarweise Cosine).
- [ ] **T3 — `detect_clusters(bank_id)` implementieren:** Embeddings aus Qdrant scrollen, HDBSCAN ausführen, pro Cluster Cohesion berechnen, R1-Filter anwenden.
- [ ] **T4 — Logging:** Zähler pro C2-Lauf — Anzahl gefundener Cluster, Anzahl R1-gefiltert, Anzahl der Engrams gesamt.
- [ ] **T5 — Unit-Tests:** (a) 9 Embeddings in 3 klaren Clustern → 3 Kandidaten. (b) Verstreute Embeddings → leere Liste. (c) Cohesion < 0.75 → gefiltert.
