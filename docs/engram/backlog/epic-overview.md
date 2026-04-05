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
- **Epic 15:** Benchmark C (Golden Dataset) für quantitative Gesamtbewertung

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

### Phase 6 — Qualitätssicherung → **Milestone 6: "System Validated"**

| # | Epic | Beschreibung | Abhängigkeiten |
|---|------|-------------|----------------|
| 15 | **Benchmarking & Validation** | 4 Dimensionen: Storage Validation, Retrieval Validation, Knowledge Evolution, Construction Quality. 3 Ansätze: A) Scripted Scenarios, B) Simulated Agent Life, C) Golden Dataset. Konkrete Auswahl noch offen. | Alle vorherigen Epics |

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

Phase 6:  * → 15 (Benchmarking)
```

---

## Aktueller Status

- [x] Epic 01 — Hybrid Storage Architecture
- [x] Epic 02 — Engram Data Model
- [x] Epic 03 — LLM Routing
- [x] Epic 04 — Thalamus Filter
- [x] Epic 05 — Retain Pipeline Extension
- [x] Epic 06 — Session Layer
- [x] Epic 07 — Search & Retrieval Erweiterung
- [x] Epic 08 — Working Context
- [x] Epic 09 — Weak Connections & Synaptic Tagging
- [ ] Epic 10 — Reflect & Reconsolidation
- [ ] Epic 11 — Constructive Memory
- [ ] Epic 12 — Consolidation Pipeline
- [ ] Epic 13 — Schema Emergence
- [ ] Epic 14 — Multi-Bank Architecture
- [ ] Epic 15 — Benchmarking & Validation

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
| M6 | System Validated | 6 (E15) | Golden Dataset Benchmark, alle Dimensionen ≥ Mindest-Score |

```
M1 → M2 → M3 → M4 → M5 → M6
```

---

## Status-Legende

- [ ] Nicht begonnen
- [~] In Arbeit
- [x] Abgeschlossen
