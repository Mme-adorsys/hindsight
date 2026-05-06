# MemoryManager — Epic Overview

> Brain-Inspired Memory Architecture for AI Agents
> Basierend auf Hindsight (Vectorize) als Fundament

---

## Übersicht

Dieses Backlog beschreibt die vollständige Umsetzung der Engram-basierten Memory-Architektur.
Jedes Epic hat einen eigenen Ordner mit `epic.md` und zugehörigen Stories.
Stories enthalten eingebettete Tasks als Checkliste.

**Die Epics sind in Umsetzungsreihenfolge nummeriert.** Claude Code arbeitet sie sequenziell ab, sofern nicht anders angegeben.

**Kontext für Claude Code:**
- Lies zuerst `concept.md` für das Gesamtbild
- Lies dann das jeweilige `epic.md` für Scope und Abhängigkeiten
- Arbeite Stories sequenziell innerhalb eines Epics ab
- Nutze den Hindsight Memory Bank für Entscheidungshintergründe
- Alles baut auf dem Hindsight Fork auf — bestehende Patterns und Code-Strukturen nutzen

**Test-Policy (Benchmark A gestaffelt):**
- **Epic 01-02:** Unit-Tests + Connectivity-Tests (DBs erreichbar, CRUD funktioniert, Models valide)
- **Ab Epic 05 (Retain Pipeline):** Integration-Tests (Daten fließen durch das System, Engrams in allen 3 DBs)
- **Ab Epic 07 (Search & Retrieval):** Retrieval-Tests (Precision/Recall, Mode-Dependency, Graph-Traversal)
- **Ab Epic 12 (Consolidation):** Knowledge-Evolution-Tests + Benchmark B (Simulated Agent Life)
- **Epic 23:** Benchmark C (Golden Dataset) für quantitative Gesamtbewertung

---

## Epic-Liste (in Umsetzungsreihenfolge)

### Phase 1 — Fundament → **Milestone 1: "Foundation Standing"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 01 | **Hybrid Storage Architecture** | Qdrant als Content Store, Neo4j als Graph Store, PostgreSQL als Agent Session Bank. Engram-ID Linking zwischen Neo4j und Qdrant. Migration von monolithischem PostgreSQL+pgvector. | — (Basis für alles) |
| 02 | **Engram Data Model** | Engram als zentrale Wissenseinheit: Embedding, Tags, Strength, Thalamus-Scores, Session-Referenz, Layer-Property. Ablösung des flachen Fact-Modells (memory_units). | Epic 01 |
| 03 | **LLM Routing** | L1-L3: Task-to-Model-Tier Mapping definieren, LLMConfig um Per-Subtask Assignment erweitern, konkrete Provider-Mappings (Haiku/GPT-4o-mini → Sonnet/GPT-4o → Opus). Rule-based, nicht dynamisch. | — (Querschnitt, parallel zu 01+02) |

### Phase 2 — Ingestion + Steuerung → **Milestone 2: "Memory Alive"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 04 | **Thalamus Filter** | Relevance Scoring Gate: Novelty, Surprise, Task-Relevance, Emotional Valence. Entscheidet was gespeichert wird und mit welcher initialen Stärke. | Epic 02 |
| 05 | **Retain Pipeline Extension** | R1-R5: ExtractedFact/ProcessedFact um Tags + Thalamus Scores erweitern, Embedding-Anreicherung (temporal + session + thalamus), Score-aware Deduplication, Entity Processing Erweiterung, Link-Erweiterung (co_activated, temporal_proximity, schema). | Epic 02, Epic 04 |
| 06 | **Session Layer** | Transientes Objekt im Application Layer. Dual Control: explizit (Agent/User) + automatisch (Surprise, Prediction Error). Mode konfiguriert: MPFP Patterns, Thresholds, Scoring Weights, Traversal Depth, Weak Links, Reconsolidation, Construction. 4 Modi: Precision, Exploration, Analogy, Validation. | Epic 02 |

### Phase 3 — Retrieval → **Milestone 3: "Memory Thinks"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 07 | **Search & Retrieval Erweiterung** | S1-S6: Fact-Type Filter ablösen, Mode-aware MPFP Patterns, Thalamus-Score Pre-Filter + Scoring, Recency-Decay Modulation durch Engram Strength, Session-Mode Steuerung, Retriever-Architektur (EngramRetriever). | Epic 01, Epic 02, Epic 06 |
| 08 | **Working Context** | Transientes PFC-Äquivalent: Goal Stack, Active Engrams (3 Tiers: focus/supporting/peripheral), Episodic Buffer, Inference Layer. Workspace während laufender Tasks. | Epic 06 |
| 09 | **Weak Connections & Synaptic Tagging** | Co-Activation Tracking bei Recall, Association Windows bei Retain, temporal_proximity Links. Neo4j Relationship-Types: co_activated, temporal_proximity. Mode-abhängiges Traversal (Precision ignoriert, Exploration folgt, Analogy bevorzugt). | Epic 01, Epic 02, Epic 06 |

### Phase 4 — Verarbeitung → **Milestone 4: "Memory Learns"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 10 | **Reflect & Reconsolidation** | RF1-RF4: Priority-basierte Reconsolidation (Stärke → Prediction Error → Disposition), Retrieval-Cost Optimierung durch Hybrid-Architektur, Semantic Trigger statt Timer, Disposition-Einfluss auf Reconsolidation. | Epic 02, Epic 07 |
| 11 | **Constructive Memory** | Retrieval als Rekonstruktion statt Lookup. Pipeline: {facts, inferences, gaps}. Mode beeinflusst Construction. Prediction Error Detection wenn Antwort von current_expectation abweicht. Feedback in Session Mode + Reconsolidation. | Epic 07, Epic 06, Epic 08 |

### Phase 5 — Langzeit-Prozesse → **Milestone 5: "Memory Matures"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 12 | **Consolidation Pipeline** | 4-Stufen Modell: Working Memory (PostgreSQL) → Consolidation 1 → Engram Buffer (Dictionary layer='buffer') → Consolidation 2 (NCR) → Neocortex (Dictionary layer='neocortex'). NCR: Phase 1 Decay, Phase 2 Strengthen, Phase 3 Schema Compression. | Epic 01, Epic 02, Epic 05 |
| 13 | **Schema Emergence** | 5 Game-of-Life Regeln: R1 Clustering/Birth (3+ Engrams, M+ common neighbors), R2 Repetition/Maturation (K NCR Zyklen), R3 Abstraction/Specialization (gemeinsame Properties extrahieren), R4 Reinforcement/Growth (neues Engram stärkt Schema), R5 Competition/Death (schwache Schemas sterben). R4 auch inkrementell bei Retain. | Epic 12 |
| 14 | **Multi-Bank Architecture** | B1-B6: 3-Tier Bank Model (Agent Session → Agent Engram Dictionary → Shared Memory), Write Conflict Resolution, Cross-Bank Novelty Scoring, Shared-to-Agent Feedback Loop, Consolidation Triggers, Cross-Bank Query. | Epic 01, Epic 02, Epic 12 |

### Phase 6 — Evolution → **Milestone 6: "System Evolved"**

> Refactoring und Erweiterung des Gesamtsystems. Reichhaltigere API, objektiver Thalamus, konfigurierbare Modelle, persistentes Working Memory.

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 15 | **API & Retain Enrichment** | Retain-API um Expectation, Outcome, Tags erweitern. Recall-API um Expectation, Tags erweitern. Neuer Pipeline-Step R0 (Sequence Analysis): budget-abhängige Extraktion aus reichhaltigem Content (Konversationen, Narrative). Experience-Engram Typ. Neue Neo4j Links: CAUSAL, PREDICTION_ERROR. Caller schickt lieber mehr Content — System extrahiert Struktur. | Epic 05, Epic 02, Epic 01 |
| 16 | **Objektiver Thalamus-Scoring-Rahmen** | Refactoring aller 4 Thalamus-Dimensionen zu rein embedding-basierten, deterministischen Berechnungen. Surprise aus Expectation↔Outcome. Emotional Valence aus Prediction-Error-Magnitude. LLM-Call entfällt. Alle Scores kostenlos, reproduzierbar, schnell. | Epic 15, Epic 04 |
| 17 | **Konfigurierbare Modell-Zuweisung** | Per-Pipeline-Schritt Modellkonfiguration. 3 Budget-Profile (Low/Mid/High) als Empfehlungen. Per-Step Overrides auf Bank-Ebene. Prioritätsreihenfolge: Request > Bank > Env > Profile Default. Admin-API für Bank-Konfiguration. | Epic 03, Epic 15 |
| 18 | **Working Memory Persistence & Cache Layer** | 2-Schicht-Modell: Session Cache (transient, wird geflushed) + Working Memory (persistent, überlebt Sessions). Priming-Effekt: nächste Session startet mit warmem Kontext. PostgreSQL JSONB Persistenz. Flush-Prozess: Episodic Buffer → Retain, Inferences → WM, Co-Activation → Neo4j. | Epic 08, Epic 06 |

### Phase 7 — Control Plane Extension → **Milestone 7: "System Visible"**

> Das Control Plane zeigt die brain-inspirierten Features: Engram-Metadaten, Session Modes, Lifecycle-Übersicht, NCR Dashboard, Schema Explorer.

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 19 | **CP: Engram Metadata & Session Modes** | Memory-Tabelle um Strength, Layer, Access Count, Thalamus Scores erweitern. Memory Detail Panel mit Thalamus-Score-Visualisierung. Session Mode Selector (Precision/Exploration/Analogy/Validation) in Recall und Reflect Views. | Epic 02, Epic 04, Epic 06 |
| 20 | **CP: Bank Profile & System Configuration** | Bank Profile um System Configuration Section erweitern: LLM Provider/Model/Tier-Routing, DB Connection Status, Embedding/Reranker Info, NCR Config. Neuer Dataplane Endpoint `/config`. | Epic 03, Epic 19 |
| 21 | **CP: Engram Lifecycle & NCR Dashboard** | Neue Views: Engram Lifecycle (Layer-Verteilung, Strength-Distribution, Flow-Visualisierung) + NCR Dashboard (manueller Trigger, Run History, Ergebnis-Übersicht). NCR History Persistence in DB. 2 neue Sidebar-Items. | Epic 12, Epic 02, Epic 19 |
| 22 | **CP: Schema Explorer** | Neues View: Schema-Liste mit Maturity-Badges, Schema-Detail mit Member-Engrams, Mini-Graph (Cytoscape). Neue Dataplane Endpoints für Schema List/Detail. 1 neues Sidebar-Item. | Epic 13, Epic 21 |

### Phase 8 — Lifecycle Scoring Overhaul → **Milestone 8: "Memory Evolves"**

> Neues biologisch inspiriertes Scoring-System: Geburtswert × Decay mit individueller Equilibrium Rate, sessions-basiertem Aging und bidirektionalem Lifecycle.

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 24 | **Lifecycle Scoring Overhaul** | Neuer Composite Score (`thalamus_overall × decay`), Equilibrium Rate r (demand/protection × bank_factor), sessions_alive als Taktgeber, tag-abhängige Promote-Thresholds, bankgrößen-normalisierte Hard Gates, bidirektionaler Lifecycle (Archive-Reactivation, Buffer-Aging). Ersetzt altes Scoring in `scoring.py`, `ncr_decay.py`, `ncr_strengthen.py`. | Epic 04, Epic 06, Epic 12 |

### Phase 9 — Qualitätssicherung → **Milestone 9: "System Validated"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 23 | **Benchmarking & Validation** _(geparkt)_ | 4 Dimensionen: Storage Validation, Retrieval Validation, Knowledge Evolution, Construction Quality. 3 Ansätze: A) Scripted Scenarios, B) Simulated Agent Life, C) Golden Dataset. Konkrete Auswahl noch offen. | Alle vorherigen Epics |

### Phase 10 — CLS Architecture Refactor → **Milestone 10: "Memory Separates"**

> Strikte CLS-Trennung: Buffer (Hippocampus) hält ausschließlich Engrams, Neocortex (Neo4j) ausschließlich Schemas. C2 wird zur Pattern Recognition mit HDBSCAN-Clustering und Schema-Erzeugung; C3 zur Schema-Restrukturierung (R3 Hyper-Schemas, R5 Schema Death). Schemas verweisen indexbasiert auf Engrams (Top-N UUID-Array, kein Cross-DB-Edge).

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 25 | **CLS Architecture Refactor** | Neue 3-Phasen-Pipeline (C1, C2, C3), Schema als eigenständige Neo4j-Entität (`:Schema`/`:HyperSchema`-Knoten), Engram-Layer eingeschränkt auf {working, buffer}, Schema-Centroid in Qdrant (`payload.kind="schema"`), HDBSCAN-Cluster-Detection in C2 mit R1+R2-Maturation, Schema-Fingerprint-Match (Cosine ≥ 0.85), statistische Property-Aggregation, `consolidation.schema_description` Pipeline-Step (Tier SMALL + Template-Fallback), R4 batch + incremental, C3 mit Hyper-Schema-Bildung (R3) und Schema Death (R5), HybridRetriever für gemischte Engram/Schema-Treffer, Mode-abhängige Gewichtung. Cleanup der alten ncr_decay/ncr_strengthen/schema_processor-Module. **Plus Adaption** bestehender Epics auf neue Architektur: Reconsolidation auf Schema-Hits + Drift-Tracking (Epic 10), Multi-Bank-Schema-Promotion + Cross-Agent-Konvergenz + Konflikt-Resolution (Epic 14), Control-Plane Schema-Explorer Backend + Frontend (Epic 22). 28 Stories in 8 Blöcken. | Epic 01, Epic 02, Epic 03, Epic 10, Epic 12, Epic 13, Epic 14, Epic 22, Epic 24 |

---

## Abhängigkeitsgraph

```
Phase 1:  01 (Storage) → 02 (Engram) ──┬──→ Phase 2
          03 (LLM Routing) ─────────────┘ (parallel)

Phase 2:  02 → 04 (Thalamus) → 05 (Retain)
          02 → 06 (Session Layer)

Phase 3:  01 + 02 + 06 → 07 (Search)
          06 → 08 (Working Context)
          01 + 02 + 06 → 09 (Weak Connections)

Phase 4:  02 + 07 → 10 (Reconsolidation)
          07 + 06 + 08 → 11 (Constructive Memory)

Phase 5:  01 + 02 + 05 → 12 (Consolidation)
          12 → 13 (Schema)
          01 + 02 + 12 → 14 (Multi-Bank)

Phase 6:  05 + 02 + 01 → 15 (API Enrichment)
          15 + 04 → 16 (Objective Thalamus)
          03 + 15 → 17 (Model Configuration)
          08 + 06 → 18 (WM Persistence)

Phase 7:  02 + 04 + 06 → 19 (CP Metadata & Modes)
          03 + 19 → 20 (CP Bank Config)
          12 + 02 + 19 → 21 (CP Lifecycle & NCR)
          13 + 21 → 22 (CP Schema Explorer)

Phase 8:  04 + 06 + 12 → 24 (Lifecycle Scoring Overhaul)

Phase 9:  * → 23 (Benchmarking) [geparkt]

Phase 10: 01 + 02 + 03 + 10 + 12 + 13 + 14 + 22 + 24 → 25 (CLS Architecture Refactor)
```

---

## Aktueller Status

### Phase 1 — Fundament
- [x] Epic 01 — Hybrid Storage Architecture
- [x] Epic 02 — Engram Data Model
- [x] Epic 03 — LLM Routing

### Phase 2 — Ingestion + Steuerung
- [x] Epic 04 — Thalamus Filter
- [x] Epic 05 — Retain Pipeline Extension
- [x] Epic 06 — Session Layer

### Phase 3 — Retrieval
- [x] Epic 07 — Search & Retrieval Erweiterung
- [x] Epic 08 — Working Context
- [x] Epic 09 — Weak Connections & Synaptic Tagging

### Phase 4 — Verarbeitung
- [x] Epic 10 — Reflect & Reconsolidation
- [x] Epic 11 — Constructive Memory

### Phase 5 — Langzeit-Prozesse
- [x] Epic 12 — Consolidation Pipeline
- [x] Epic 13 — Schema Emergence
- [x] Epic 14 — Multi-Bank Architecture

### Phase 6 — Evolution
- [x] Epic 15 — API & Retain Enrichment
- [x] Epic 16 — Objektiver Thalamus-Scoring-Rahmen
- [x] Epic 17 — Konfigurierbare Modell-Zuweisung
- [x] Epic 18 — Working Memory Persistence & Cache Layer

### Phase 7 — Control Plane Extension
- [x] Epic 19 — CP: Engram Metadata & Session Modes
- [x] Epic 20 — CP: Bank Profile & System Configuration
- [x] Epic 21 — CP: Engram Lifecycle & NCR Dashboard
- [x] Epic 22 — CP: Schema Explorer

### Phase 8 — Lifecycle Scoring Overhaul
- [x] Epic 24 — Lifecycle Scoring Overhaul

### Phase 9 — Qualitätssicherung _(geparkt)_
- [ ] Epic 23 — Benchmarking & Validation

### Phase 10 — CLS Architecture Refactor
- [ ] Epic 25 — CLS Architecture Refactor

---

## Milestones

Detailliert in [milestones.md](milestones.md) — hier die Kurzübersicht:

| # | Milestone | Phase | Kernaussage |
|---|-----------|-------|-------------|
| M1 | Foundation Standing | 1 (E01-03) | 3-DB Architektur steht, Engram Model definiert, LLM Routing aktiv |
| M2 | Memory Alive | 2 (E04-06) | Thalamus filtert, Retain-Pfad komplett, Session Layer steuert |
| M3 | Memory Thinks | 3 (E07-09) | Mode-aware Retrieval, Working Context, Weak Connections |
| M4 | Memory Learns | 4 (E10-11) | Reconsolidation auf alle Typen, Constructive Memory mit Inferenz |
| M5 | Memory Matures | 5 (E12-14) | NCR Consolidation, Schema Emergence, Multi-Bank mit Shared Memory |
| M6 | System Evolved | 6 (E15-18) | Reichhaltige API, objektiver Thalamus, konfigurierbares LLM Routing, persistentes Working Memory |
| M7 | System Visible | 7 (E19-22) | Control Plane zeigt Engram-Metadaten, Lifecycle, NCR, Schemas |
| M8 | Memory Evolves | 8 (E24) | Neues Lifecycle Scoring mit Decay, Equilibrium Rate, bidirektionalem Lifecycle |
| M9 | System Validated | 9 (E23) | Golden Dataset Benchmark, alle Dimensionen ≥ Mindest-Score |
| M10 | Memory Separates | 10 (E25) | Strikte CLS-Trennung: Engrams im Buffer, Schemas im Cortex. C2 = Pattern Recognition, C3 = Schema-Restrukturierung. HybridRetriever liefert gemischte Treffer |

```
M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9
                                              ↓
                                              M10 (CLS Refactor)
```

---

## Zusammenfassung der Änderungen (April 2026)

**Roadmap-Umstrukturierung:**
1. **Benchmarking** wurde ans Ende verschoben (jetzt Epic 23, Phase 8) — Benchmarking macht erst Sinn wenn alle Features stehen
2. **Epics 15-18 (Evolution/Refactoring)** sind Phase 6: Reichhaltige API, objektiver Thalamus, konfig. Models, WM Persistence
3. **Epics 19-22 (Control Plane Extension)** sind NEU (Phase 7) — entstanden aus dem Control Plane Extension Plan mit 6 Stories in 4 Phasen
4. **Durchgehende Nummerierung** E01-E23 entspricht der Ausführungsreihenfolge
5. **Milestone-Nummern** wurden angepasst: M6 = System Evolved, M7 = System Visible (NEU), M8 = System Validated

---

## Status-Legende

- [ ] Nicht begonnen
- [~] In Arbeit
- [x] Abgeschlossen
