# Epic 25 — CLS Architecture Refactor

> Strikte Trennung von Hippocampus (Buffer) und Neocortex (Schemas) gemäß CLS-Theorie. C2 wird zur Pattern Recognition, C3 zur Schema-Restrukturierung. Engrams leben nur noch im Buffer, der Neocortex enthält ausschließlich Schemas.

## Ziel

Die bisherige Consolidation-Architektur (Engrams mit `layer='neocortex'`, C2 = Decay+Strengthen, C3 = Schema-Compression-Hook) wird ersetzt durch ein CLS-konformes Modell:

- **Buffer (PostgreSQL + Qdrant)** ist das Hippocampus-Äquivalent — alle individuellen Engrams (working oder buffer) leben dort.
- **Neocortex (Neo4j + Qdrant)** ist der Schema-Layer — ausschließlich `:Schema`- und `:HyperSchema`-Knoten, keine individuellen Engrams.
- **C2** wird zur Pattern Recognition: HDBSCAN-Clustering über Buffer-Engrams, Schema-Erzeugung oder -Reinforcement, plus Decay-Re-Evaluation der Buffer-Engrams.
- **C3** wird zur Schema-Restrukturierung: Game-of-Life R3 (Hyper-Schema-Bildung) und R5 (Schema Death) ausschließlich über Schemas im Cortex.
- **Schemas verweisen auf Engrams indexbasiert** — Top-N `evidence_engram_ids` als UUID-Array-Property, kein Cross-DB-Edge.
- Schema-Description wird via kleinem LLM (`consolidation.schema_description`, Tier SMALL) generiert, mit Template-Fallback.

Bio-Vorbild: McClelland/McNaughton/O'Reilly (1995, CLS-Theorie), Tse et al. (2007, schema-konsistente Konsolidierung), Teyler & DiScenna (1986, Indexing Theory).

## Bestehende Codebasis

- **Consolidation 1:** `engine/consolidation/consolidation1.py` — bleibt konzeptuell (WM → Buffer), Layer-Werte werden konsolidiert.
- **NCR Decay:** `engine/consolidation/ncr_decay.py` — wird in C2 als Decay-Re-Eval der Buffer-Engrams überführt, alte Logik entfällt.
- **NCR Strengthen:** `engine/consolidation/ncr_strengthen.py` — ehemalige Promotion `buffer → neocortex` entfällt vollständig (es gibt keinen Engram-Layer 'neocortex' mehr).
- **Schema Processor:** `engine/consolidation/schema_processor.py` — wird zerlegt: Pattern-Recognition-Logik wandert in C2 (`engine/consolidation/c2_pattern_recognition.py`), Schema-Schema-Logik wandert in C3 (`engine/consolidation/c3_schema_restructure.py`).
- **NCR Orchestrator:** `engine/consolidation/ncr_orchestrator.py` — neue 3-Phasen-Struktur (C1, C2, C3) statt 5-Phasen.
- **Engram Dictionary:** PostgreSQL `engram_dictionary` — `layer`-Wertebereich auf {working, buffer} einschränken, alte `neocortex`-Werte migrieren.
- **Neo4j Schema:** Aktuell halten Schemas keinen eigenen Knoten-Typ — neue Knoten-Typen `:Schema` und `:HyperSchema` einführen, mit `:SPECIALIZES`-Edge.
- **Qdrant Collection:** Bisher nur Engram-Embeddings. Neue Schema-Centroids in derselben Collection, unterschieden durch `payload.kind ∈ {"engram", "schema"}`.
- **Retain Pipeline:** `engine/retain/` — Schema-Fit-Check (R4 incremental) muss Schema-Centroids auflösen können.
- **Recall Orchestrator:** `engine/recall_orchestrator.py` — wird zum HybridRetriever umgebaut, der Engram- und Schema-Treffer in einer Vektor-Search behandelt.

## Scope

**Kern-Refactor (Stories 01–20):**

- Schema als eigenständige Neo4j-Entität (Knoten-Typ, Felder, Edges)
- Engram-Layer eingeschränkt auf {working, buffer}, Migration alter neocortex-Engrams
- Schema-Centroid in Qdrant (`payload.kind="schema"`)
- C2 — Pattern Recognition Pipeline: HDBSCAN, Fingerprint-Match, Property-Aggregation, Centroid, Description, Persistierung, Reinforcement (R4 batch), Decay-Re-Evaluation
- C3 — Schema-Restrukturierung: R3 Hyper-Schema-Bildung, R5 Schema Death
- R4 incremental beim Retain (Schema-Fit-Check)
- HybridRetriever für gemischte Engram/Schema-Treffer beim Recall
- Top-N Evidence-Auflösung beim Schema-Treffer
- Mode-abhängige Schema-vs-Engram-Gewichtung
- Cleanup: alte `ncr_decay`/`ncr_strengthen`/`schema_processor`-Module entfernen
- Knowledge-Evolution-Tests an neue Architektur anpassen
- E2E-Test "Coffee-Meeting" als Akzeptanzgrenze

**Adaption bestehender Epics auf neue Architektur (Stories 21–28):**

- Reconsolidation auf Schema-Hits + Drift-Tracking (Adaption Epic 10)
- Multi-Bank: Schema-Promotion in Shared Bank, Cross-Agent-Konvergenz, Konflikt-Resolution, Engram-Promotion entfernen (Adaption Epic 14)
- Control Plane Schema-Explorer Backend + Frontend auf neue Schema-Knoten umstellen (Adaption Epic 22)

## Nicht in Scope

- Constructive Memory Anpassung (Epic 11 — bleibt unverändert, weil es engram-agnostisch arbeitet)
- LLM-Pipeline-Step-Konfiguration auf Bank-Ebene (Mechanismus aus Epic 17 wird durch Story 08 wiederverwendet — keine Änderungen am Mechanismus selbst)

## Abhängigkeiten

- Epic 01 (Hybrid Storage) — alle drei Datenbanken
- Epic 02 (Engram Data Model) — Engram-Felder, Tags
- Epic 03 (LLM Routing) — Pipeline-Step-Mechanismus für `consolidation.schema_description`
- Epic 10 (Reflect & Reconsolidation) — Reconsolidation-Pipeline wird in Stories 21–22 erweitert
- Epic 12 (Consolidation Pipeline) — alte Architektur, wird ersetzt
- Epic 13 (Schema Emergence) — alte GoL-Auslegung, wird neu verteilt
- Epic 14 (Multi-Bank) — Engram-basierte Promotion wird in Stories 23–26 durch Schema-basierte ersetzt
- Epic 22 (Schema Explorer Frontend/Backend) — wird in Stories 27–28 auf neue Schema-Knoten umgestellt
- Epic 24 (Lifecycle Scoring) — Composite-Score-Formel bleibt, wird übernommen

## Stories

### Block A — Datenmodell

1. [Schema als eigenständige Neo4j-Entität](story-01-schema-neo4j-entity.md)
2. [Engram-Layer einschränken {working, buffer} + Migration](story-02-engram-layer-constraint.md)
3. [Schema-Centroid in Qdrant](story-03-schema-centroid-qdrant.md)

### Block B — C2 Pattern Recognition

4. [C2 — HDBSCAN Cluster Detection (R1+R2)](story-04-c2-cluster-detection.md)
5. [C2 — Cluster-Fingerprint-Persistierung](story-05-c2-cluster-fingerprint.md)
6. [C2 — Schema-Fingerprint-Match (Cosine ≥ 0.85)](story-06-c2-schema-fingerprint-match.md)
7. [C2 — Property-Aggregation aus getaggten Engrams](story-07-c2-property-aggregation.md)
8. [C2 — Schema-Description-Generation (LLM SMALL + Template-Fallback)](story-08-c2-schema-description.md)
9. [C2 — Schema-Persistierung Neo4j + Qdrant](story-09-c2-schema-persistence.md)
10. [C2 — Schema-Reinforcement R4 (batch)](story-10-c2-schema-reinforcement.md)
11. [C2 — Decay-Re-Evaluation Buffer-Engrams](story-11-c2-decay-reevaluation.md)
12. [Retain — R4 incremental Schema-Fit-Check](story-12-retain-r4-incremental.md)

### Block C — C3 Schema-Restrukturierung

13. [C3 — Hyper-Schema-Bildung (R3)](story-13-c3-hyper-schema.md)
14. [C3 — Schema Death (R5)](story-14-c3-schema-death.md)

### Block D — Recall

15. [HybridRetriever — Engram + Schema Mischtreffer](story-15-hybrid-retriever.md)
16. [Recall — Top-N Evidence-Auflösung](story-16-recall-evidence-resolution.md)
17. [Recall — Mode-abhängige Schema/Engram-Gewichtung](story-17-recall-mode-weighting.md)

### Block E — Cleanup + Tests (Kern)

18. [Cleanup — alte ncr_decay/ncr_strengthen/schema_processor entfernen](story-18-cleanup-old-modules.md)
19. [Tests — Knowledge-Evolution-Tests an neue Architektur anpassen](story-19-tests-knowledge-evolution.md)
20. [Tests — E2E "Coffee-Meeting" Schema-Lifecycle](story-20-tests-coffee-meeting-e2e.md)

### Block F — Adaption Reconsolidation (Epic 10)

21. [Reconsolidation auf Schema-Hits](story-21-reconsolidation-on-schema-hits.md)
22. [Schema Reconsolidation Window & Drift-Tracking](story-22-schema-reconsolidation-window.md)

### Block G — Adaption Multi-Bank (Epic 14)

23. [Multi-Bank: Schema-Promotion in Shared Bank](story-23-multibank-schema-promotion.md)
24. [Multi-Bank: Cross-Agent Schema-Konvergenz](story-24-multibank-cross-agent-convergence.md)
25. [Multi-Bank: Schema-Konflikt-Resolution](story-25-multibank-schema-conflict-resolution.md)
26. [Multi-Bank: Engram-Promotion entfernen + Konzept-Cleanup](story-26-multibank-engram-promotion-removal.md)

### Block H — Adaption Control Plane (Epic 22)

27. [CP: Schema-Explorer Backend-Endpoints](story-27-cp-schema-explorer-backend.md)
28. [CP: Schema-Explorer Frontend-Adaption](story-28-cp-schema-explorer-frontend.md)

**Empfohlene Bearbeitungsreihenfolge:** A → B → C → D → E (Kern abgeschlossen) → F → G → H. Adaptionen (F–H) bauen auf den Kern-Stories auf (insbesondere S15 HybridRetriever für F, S09 Schema-Persistierung für G, alle Datenmodell-Stories A für H).
