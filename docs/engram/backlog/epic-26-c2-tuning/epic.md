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

## Story 06 — C1-Promotion-Rate auf Dev-Bänken (offen)

**Befund nach Story 02 Live-Smoke:** Selbst mit funktionierender memory_embedding-Lane (sprint_retro paarweise Cosines 0.83-0.90 im memory_embedding-Raum — klar clusterbar) bleibt C1 der Bottleneck: 5 von 21 Engrams in Buffer, sprint_retro 0/8 obwohl es das stärkste Cluster-Signal hat.

**Root-Cause:** Thalamus avg_score fällt nach dem ersten Retain auf 0.30-0.39 weil ähnliche Folge-Memories die `novelty`-Komponente drücken (Konzept §5.2). Plus access_count-Hard-Gate (≥ 8) wird auf kleinen Bänken selten erreicht.

**Mögliche Stoßrichtungen:**
- (a) Thalamus-Novelty soll memory_embedding statt fact_embedding nutzen (Cluster-Cohesion-konform).
- (b) Composite-Score-Decay-Faktor für junge Engrams (< 24h) auf 1.1× erhöhen (Geburts-Bonus).
- (c) access_count-Hard-Gate auf Dev-Bänken (<100 Engrams) auf 5 zurück (war pre-Epic-24).

Aufschlussreich nur via Smoke-Run mit größerer Bank — siehe Story 04 (50-Memory-Variante).

## Story 07 — HDBSCAN durch AgglomerativeClustering ersetzen ✅

**Status:** Implementiert (Commit `b32cccf`). Smoke-Test ging von 0/6 auf 2/6 Hard-Checks.

**Befund:** HDBSCAN failed empirisch auf Dev-Banken (n<50). 8 Buffer-Engrams mit pairwise Cosine 0.85+ wurden über ALLE Configs (eom/leaf × min_samples 1/2) als komplette Noise gelabeled. Concept §13 R1 fragt explizit nach "paarweise Cosine ≥ 0.75" — das ist eine Distanz-Schwellen-Operation, kein Dichte-Problem.

**Fix:** `sklearn.cluster.AgglomerativeClustering(metric="cosine", linkage="complete", distance_threshold=1-COHESION_THRESHOLD)` drückt §13 R1 direkt aus: alle Paare im Cluster müssen über dem Cohesion-Gate liegen. Singletons werden auf -1 remapped (Noise-Konvention beibehalten).

**Live-Verifikation:** dev-cls-smoke produziert jetzt ein echtes `coffee_morning` Schema mit 4 Evidence-IDs und aggregierten Properties (cluster, format, mood, time).

## Status der Stories (Stand 2026-05-20)

| Story | Status | Commit |
|-------|--------|--------|
| 01 — Reset-Bug | ✅ | `a07916b` |
| 02 — Memory-Embedding-Lane | ✅ | `363f57b` + `c0ca7aa` |
| 03 — Backfill | ⏸ Deferred (Dev-Bänke starten frisch) | — |
| 04 — Smoke-Skalierung + 100-Memory | ✅ | `c1fdbe2` |
| 05 — UMAP Dev-Diagnostik | ⏸ Optional | — |
| 06 — Thalamus kind=engram filter | ✅ | `9946171` |
| 07 — HDBSCAN → Agglomerative | ✅ | `b32cccf` |

**Smoke-Test-Endstand (100-Memory):** 4/6 Hard-Checks. Zwei echte fresh schemas im neuen Bank (`coffee_morning` + `sprint_retro`, retro mit evidence=6 cycles=2). C2 R1 + R2 + R4 vollständig validiert.

**Warum nicht 6/6 mit 100 Memories?** R4 Schema-Fingerprint-Match: `coffee_afternoon`-Centroid matchte den existierenden `coffee_morning`-Centroid mit cosine ≥ 0.85 (geteilter boilerplate "coffee/office") → REINFORCED morning statt CREATED afternoon. Das ist **korrektes Konzept-§13-R4-Verhalten** (ähnliche Patterns sollen denselben Schema verstärken). Der "3 schemas"-Test-Check ist daher zu optimistisch für Seeds mit hoher Cluster-Overlap.

**Bekanntes Cross-Bank-Leak im CP Read-Path:** `/v1/cp/banks/{bank_id}/schemas` zeigt auch schemas aus anderen Banks weil `_filter_bank` einen "kein bank_id stamp → zeigen" Fallback hat. Schema-Knoten in Neo4j tragen kein `bank_id` (nur Qdrant-Payload). Story für eine spätere Iteration (kein Datenintegritäts-Issue, nur UI-Display).

**Bonus: Fact-Style Smoke (`--style fact`)** validierte die Tag-basierte Lifecycle-Differenzierung empirisch. 99 world-fact-Memories auf derselben Bank → 119 Engrams → **C1 consolidated=0** (alle in Working geblieben). Thalamus-avg-Score 0.38-0.40 × Decay ≈ 0.40 erreicht den 0.7-Promote-Threshold für `fact`-Tag nie. Konzept-korrektes §5.3-Verhalten: Facts brauchen höhere Bar als Experiences (Bio: episodisch wird gern konsolidiert, deklarative Trivia nicht). 0/6 hard checks, aber das ist der gewünschte Effekt — Pipeline weigert sich völlig korrekt, beliebige Facts zu konsolidieren.

**Stress-Test: Fact mit 100 Recalls/Memory (`--recalls-per-memory 100`)** sollte zeigen, ob Recall-Druck Facts über den 0.7-Threshold pusht. Resultat: **NEIN.** 15 facts × 100 recalls → avg access_count=147 (max 300), aber **strength blieb bei 0.458** (= Thalamus-Birth-Wert). Composite = thalamus × strength × recency stayed at 0.458 < 0.7 → C1 consolidated=0.

**Architektur-Befund (Story 08 Kandidat):** Auf der aktuellen Epic-25-Architektur bumpt der Recall-Pfad NUR `access_count`, NICHT `strength`. Der alte NCR-Phase-2-Strengthen-Pfad wurde in Epic 25 entfernt. Damit gibt es keinen Mechanismus, der hoch-frequentierte Facts mit der Zeit consolidate-fähig macht. Konzept §5.4 ("Composite = Thalamus × Strength × Recency") setzt aber genau diese Persistenz-durch-Nutzung voraus. Story 26.08 sollte einen "recall-driven strengthen"-Hook bauen, der bei jedem Recall die Strength des getroffenen Engrams inkrementell anhebt (entsprechend LTP bei wiederholter synaptischer Aktivierung).

## Out-of-Scope

- Änderung der `COHESION_THRESHOLD=0.75`-Konstante auf Production-Bänken (Konzept-Bindung §13 R1).
- Änderung der Thalamus-Novelty-Komponente direkt (Konzept §5.2). Story 06 (a) ist eine *Verdrahtung*, kein Konzept-Verstoß.
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
