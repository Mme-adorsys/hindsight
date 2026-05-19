# Epic 26 — C2 Pattern Recognition Tuning

> Auflösung des architektonischen Cohesion-vs-Novelty Tradeoffs, der im Live-Smoke-Test nach Epic 25 sichtbar wurde, plus Reset-Bug-Fix und Dev-Diagnostik.

## Kontext

Der End-to-End-Smoke-Test (`scripts/dev/test_cls_pipeline.py`, 15 Memories in 3 Clustern) hat Epic 25 architektonisch verifiziert — die Pipeline läuft sauber durch, ein echtes Schema (`sprint_retro`) entsteht in Neo4j mit Properties und Evidence-IDs. **Aber**: Cluster A (Coffee morning) und Cluster B (Coffee afternoon) reifen nicht zu Schemas, obwohl konzeptuell vorgesehen.

Die Ursache ist nicht ein einzelner Bug, sondern ein **echter Architektur-Tradeoff** zwischen drei gleichzeitigen Mechanismen:

1. **HDBSCAN-Cohesion (C2 R1):** Innerhalb-Cluster-Cosine muss ≥ 0.75 sein, damit der Cluster den R1-Filter passiert.
2. **Thalamus-Novelty (Retain):** Die `novelty`-Komponente fällt, wenn neue Memories früheren ähnlich sehen — das ist konzept-korrekt, drückt aber den Composite-Score unter den C1-Promote-Threshold.
3. **LLM-Fact-Extraction:** Retain extrahiert ~2 Fakten pro Memory. Das Embedding für C2 sitzt auf der extrahierten Fakt-Ebene, nicht auf der Original-Memory-Ebene → cluster-distinktive Sprache geht verloren, bevor HDBSCAN sie sehen würde.

Auf einer 15-Memory-Dev-Bank manifestiert sich das als:
- Viel Shared Boilerplate → C1 dropped, Cluster wird zu einem Blob (HDBSCAN: `raw_clusters=0`).
- Wenig Shared Boilerplate → C1 promotet, aber Cohesion unter 0.75 (HDBSCAN: `cohesion_filtered=N`).
- Orthogonal kontrastierte Cluster → schlimmster Fall: C1 dropped UND HDBSCAN sieht nichts.

Auf größeren Banken (100+ Memories) sollte das Problem statistisch verschwinden, aber für Dev-Banken und für die Dev-Erfahrung ist es ein echtes Hindernis.

Bestätigt durch drei iterierte Smoke-Runs nach Epic 25 (`3d14054`, `a67e8a7`, `bad4241` / `ebe4b39` revertet `90a1524`).

## Ziel

Cluster-Erkennung auf kleinen Banken zuverlässig machen, ohne den Composite-Score oder die §13-R1-Schwelle zu kompromittieren.

## Stoßrichtungen (zu evaluieren als Story-Set)

### Story 01 — Reset-Bug: Schemas überleben `reset_neo4j`

Status-Quo: `scripts/dev/reset_bank.py::reset_neo4j` löscht Engrams, aber `:Schema`/`:HyperSchema`-Knoten bleiben. Konsequenz: Smoke-Runs sehen den Schema aus dem vorigen Run (`f959e1dd…`), Evidence-IDs zeigen auf nicht mehr existierende Engrams.

Fix: Schema-Knoten + Qdrant-Schema-Centroids (`payload.kind="schema"`, `bank_id`-Filter) mit löschen.

### Story 02 — C2 nutzt Original-Memory-Text + Tags für HDBSCAN-Embedding

Heute: HDBSCAN konsumiert das Engram-Embedding aus Qdrant, das aus der extrahierten Fakt-Ebene stammt.

Vorschlag: Zweite Embedding-Spur, die beim Retain *zusätzlich* das Original-Memory + Tag-Strings einbettet (`payload.kind="memory_embedding"` o.ä.) und in C2 für Cluster-Detection verwendet wird. Die Fakt-Embeddings bleiben für Recall.

Trade-Off: 2× Embedding-Kosten beim Retain, +1 Qdrant-Vektor pro Memory.

### Story 03 — Tag-gewichteter Cosine

Heute: `_mean_pairwise_cosine` rechnet nur auf Vektoren.

Vorschlag: Cosine-Skalar mit einem Tag-Overlap-Bonus kombinieren (z.B. `+0.1` wenn `cluster:*` Tags identisch sind). Macht HDBSCAN-R1-Filter tag-aware ohne Konzept-Verletzung (Bonus, keine Schwellen-Senkung).

### Story 04 — Adaptive `MIN_CLUSTER_SIZE` / `COHESION_THRESHOLD`

Heute: Beide Werte sind Konstanten in `c2_pattern_recognition.py`.

Vorschlag: Skalierung als Funktion der Bank-Größe (analog zum `bank_factor` aus Epic 24). Auf Dev-Bänken `MIN_CLUSTER_SIZE=2`, `COHESION_THRESHOLD=0.65`; auf Production-Bänken Default. Konzept-Update in §13 nötig.

### Story 05 — Dev-Diagnostik: UMAP-Snapshot der Buffer-Engrams

Heute: Wenn HDBSCAN `raw_clusters=0` returniert, ist nicht ersichtlich *warum* — sieht alles wie ein Blob aus? Sind die Cluster zu dispers? Liegt es an einem Outlier?

Vorschlag: Dev-Endpoint `/v1/cp/banks/{bank_id}/c2-snapshot` der die Buffer-Engram-Vektoren via UMAP auf 2D projiziert + Cluster-Labels einfärbt. Wird vom Schema-Explorer-Frontend (Epic 22) konsumiert.

## Out-of-Scope

- Änderung der `COHESION_THRESHOLD=0.75`-Konstante auf Production-Bänken (Konzept-Bindung §13 R1).
- Änderung der Thalamus-Novelty-Komponente (Konzept §5.2).
- Composite-Score-Formel allgemein.

## Akzeptanzkriterien (Milestone 11)

- Smoke-Test mit den existierenden 15 Memories erreicht ≥ 4/6 Hard-Checks (mind. 2 Cluster reifen, 2 Schemas in Cortex).
- Smoke-Test auf 50-Memory-Variante erreicht 6/6 Hard-Checks.
- Reset-Bug behoben (Schema-Count nach Reset == 0, in PG + Qdrant + Neo4j verifiziert).
- Konzept §13 reflektiert ggf. adaptive Schwellen mit Begründung.

## Abhängigkeiten

- **Epic 25** (CLS Architecture Refactor) — abgeschlossen.

## Bezugsstellen im Code

- `hindsight-api/hindsight_api/engine/consolidation/c2_pattern_recognition.py` — HDBSCAN + R1
- `hindsight-api/hindsight_api/engine/consolidation/scoring.py` — `TAG_PROMOTE_THRESHOLDS`, `compute_bank_factor`, `SMALL_BANK_FACTOR_CAP`
- `hindsight-api/hindsight_api/engine/retain/orchestrator.py` — wäre Aufrufpunkt für Original-Text-Embedding
- `scripts/dev/test_cls_pipeline.py` — Smoke-Test
- `scripts/dev/reset_bank.py` — Reset-Bug-Fix
- `docs/engram/concept.md` §5.3 + §13 — Konzept-Synchronisierung bei Schwellen-Änderungen
