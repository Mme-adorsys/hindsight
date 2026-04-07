# MemoryManager — Milestones

> Jeder Milestone ist ein prüfbares Quality Gate mit konkreten Akzeptanzkriterien und Validierungstests.

---

## Milestone 1 — "Foundation Standing" (nach Phase 1: Epic 01-03)

**Aussage:** Die 3-Datenbank-Architektur steht, das Engram-Datenmodell ist definiert, LLM Routing ist konfiguriert.

### Akzeptanzkriterien

- [ ] PostgreSQL, Qdrant, Neo4j laufen und sind erreichbar
- [ ] Engram CRUD über alle 3 Datenbanken funktioniert (StorageService)
- [ ] Compensation-Logik bei Partial Failure getestet
- [ ] Engram Dictionary Tabelle mit allen Feldern (Tags, Strength, Thalamus Scores, Layer)
- [ ] Neo4j Graph Schema: Alle 8 Relationship-Types definiert
- [ ] LLM Routing: 3 Tiers konfiguriert, Provider-Mappings aktiv
- [ ] Docker-Compose Setup für alle 3 DBs + Application

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| DB Connectivity | Smoke | Alle 3 DBs erreichbar und antworten | E01 |
| Engram CRUD | Unit | Create/Read/Update/Delete in Dictionary + Qdrant + Neo4j | E01 S04 |
| Compensation | Unit | Partial Failure → Cleanup ohne Inkonsistenz | E01 S04 |
| Model Validation | Unit | FullEngram, ThalamusScores, Session Pydantic Models valide | E02 |
| LLM Tier Routing | Unit | Subtask → korrekter Tier → korrektes Model | E03 |

---

## Milestone 2 — "Memory Alive" (nach Phase 2: Epic 04-06)

**Aussage:** Das System kann filtern, speichern und session-gesteuert arbeiten. Der Retain-Pfad ist komplett.

### Akzeptanzkriterien

- [ ] Thalamus Filter bewertet Input und gate't korrekt (Gate Open/Close)
- [ ] Retain Pipeline schreibt Engrams mit Tags, Scores, erweiterten Embeddings, Links
- [ ] Deduplication: Score-aware (höher bewerteter Fakt gewinnt)
- [ ] Entity Resolution mit LLM-Disambiguation für ambige Entities
- [ ] Neue Link-Typen (temporal_proximity, schema-fit) in Neo4j
- [ ] Dual-Write: Alle Links in PostgreSQL UND Neo4j
- [ ] Session Layer: 4 Modi konfiguriert, Dual Control funktioniert
- [ ] Mode-Parameter fließen durch retain_batch_async

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Thalamus Gate | Integration | Input → Score → Gate Open/Close → korrekte Filterung | E04 |
| Tag Extraction | Integration | LLM extrahiert Tags statt nur fact_type | E05 S01 |
| Embedding Enrichment | Unit | Augmentierter Text enthält Session + Thalamus Kontext | E05 S02 |
| Score-aware Dedup | Integration | Höherer Score ersetzt bestehenden Fakt | E05 S03 |
| Entity Disambiguation | Integration | Ambige Entity korrekt aufgelöst | E05 S04 |
| Dual-Write Links | Integration | Links in PostgreSQL UND Neo4j vorhanden | E05 S05 |
| Session Mode Config | Unit | Alle 4 Modi mit korrekten Parametern | E06 S01 |
| Dual Control | Unit | Explicit + Automatic Mode Shifts | E06 S02 |
| Retain with Session | Integration | Mode beeinflusst Thalamus Threshold bei Retain | E06 S03 |

---

## Milestone 3 — "Memory Thinks" (nach Phase 3: Epic 07-09)

**Aussage:** Das System kann mode-aware suchen, einen Working Context aufbauen, und schwache Verbindungen nutzen.

### Akzeptanzkriterien

- [ ] Tags-basierte Filterung statt fact_type (ein Query statt 12)
- [ ] EngramRetriever: Qdrant Seeds + Neo4j Traversal funktioniert
- [ ] Scoring-Formel mit 6 Dimensionen (CE, RRF, Temporal, Recency, Strength, Thalamus)
- [ ] Mode-aware MPFP Patterns (unterschiedliche Ergebnisse pro Mode)
- [ ] Dual-Bank Query: Agent + Shared parallel
- [ ] Working Context: 3-Tier Active Engrams populated aus Recall
- [ ] Co-Activation Tracking und Link-Erstellung
- [ ] Association Window (STC) erzeugt temporal_proximity Links

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Tag Filter | Integration | Ein Query statt 3 fact_type Queries | E07 S01 |
| Mode MPFP | Integration | Precision vs. Exploration → unterschiedliche Patterns | E07 S02 |
| Extended Scoring | Unit | 6-Dimension Scoring korrekt berechnet | E07 S03 |
| EngramRetriever E2E | Integration | Qdrant Seeds → Neo4j Traversal → Enriched Results | E07 S04 |
| Dual-Bank Query | Integration | Agent + Shared Ergebnisse fusioniert | E07 S05 |
| Precision@5 | Retrieval | ≥ 0.8 gegen Ground Truth (Precision Mode) | E07 S06 |
| Recall@20 | Retrieval | ≥ Ground Truth Coverage (Exploration Mode) | E07 S06 |
| Working Context Population | Integration | Recall → 3-Tier Engrams populated | E08 S02 |
| Co-Activation Links | Integration | Wiederholter Recall → co_activated Link nach Threshold | E09 S01 |
| Weak Link Traversal | Integration | Exploration folgt Weak Links, Precision ignoriert | E09 S02 |

---

## Milestone 4 — "Memory Learns" (nach Phase 4: Epic 10-11)

**Aussage:** Das System kann Wissen überarbeiten (Reconsolidation) und Antworten konstruieren (nicht nur nachschlagen).

### Akzeptanzkriterien

- [ ] Reconsolidation auf alle Engram-Typen (nicht nur Opinions)
- [ ] Priority Queue: Schwache + Prediction-Error Engrams zuerst
- [ ] Semantic Trigger: Qdrant Similarity ≥ 0.6 statt nur Entity-Match
- [ ] Disposition beeinflusst Reconsolidation-Ergebnis
- [ ] ConstructedAnswer: Facts + Inferences + Gaps
- [ ] Mode-abhängige Construction (konservativ vs. kreativ)
- [ ] Prediction Error Detection → Session Mode Shift + Reconsolidation Flag

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Priority Queue | Unit | Schwache Engrams vor starken, PE-Engrams zuerst | E10 S01 |
| Semantic Trigger | Integration | Similarity ≥ 0.6 triggert Reconsolidation | E10 S02 |
| Disposition Effect | Integration | Analytical vs. Conservative → unterschiedliche Updates | E10 S03 |
| Cross-DB Consistency | Integration | Reconsolidation-Update in PG + Qdrant + Neo4j | E10 S04 |
| Construction Pipeline | Integration | Query → Facts + Inferences + Gaps | E11 S02 |
| Mode Shaping | Integration | Precision → wenig Inferenz, Exploration → mehr | E11 S02 |
| Prediction Error | Integration | Abweichung → Mode Shift + Engram Flag | E11 S03 |

---

## Milestone 5 — "Memory Matures" (nach Phase 5: Epic 12-14)

**Aussage:** Das System hat Langzeitprozesse: Consolidation, Schema Emergence, Multi-Bank mit Cross-Agent Wissen.

### Akzeptanzkriterien

- [ ] NCR läuft periodisch: Decay + Strengthen + Schema Compression
- [ ] Engram Lifecycle: buffer → neocortex → archived
- [ ] Schemas emergieren aus wiederholten Mustern (5 Game-of-Life Regeln)
- [ ] Multi-Bank: Agent-Isolation + Shared Bank Promotion
- [ ] Conflict Resolution: Merge oder Contradiction-Link
- [ ] Cross-Agent Convergence erhöht Promotion-Priorität
- [ ] Cross-Bank Query mit echtem Shared Bank Content

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| NCR Lifecycle | Integration | 5 Zyklen → Decay + Promotion korrekt | E12 S06 |
| Strength Trajectory | Evolution | Berechnete vs. beobachtete Strength über Zyklen | E12 S06 |
| Schema Birth | Evolution | 3+ Engrams mit shared Neighbors → Cluster | E13 S01 |
| Schema Maturation | Evolution | Cluster überlebt K Zyklen → Schema-Node | E13 S02 |
| Schema Death | Evolution | Schwaches Schema stirbt nach 5 Zyklen ohne Reinforcement | E13 S03 |
| Agent Isolation | Integration | Agent A kann Agent B's Data nicht lesen | E14 S05 |
| Promotion Lifecycle | Integration | Agent → Buffer → Neocortex → Shared | E14 S05 |
| Conflict Resolution | Integration | Merge + Contradiction bei Shared Write | E14 S05 |
| Benchmark B | Evolution | 30-Tage Simulated Agent Life → Metriken stabil | E14 S05 |

---

## Milestone 6 — "System Evolved" (nach Phase 6: Epic 15-18)

**Aussage:** Das System hat reichhaltigere Inputs, objektive Scores, konfigurierbare Models und persistentes Working Memory.

### Akzeptanzkriterien

- [ ] Retain-API akzeptiert Expectation, Outcome, Tags
- [ ] R0 Sequence Analysis extrahiert Struktur aus reichhaltigem Content
- [ ] Experience-Engrams werden aus Expectation→Outcome-Paaren erzeugt
- [ ] Neue Neo4j Links: CAUSAL, PREDICTION_ERROR
- [ ] Alle 4 Thalamus-Dimensionen embedding-basiert, deterministisch, kein LLM-Call
- [ ] Budget-Profile (Low/Mid/High) konfigurieren Models pro Pipeline-Schritt
- [ ] Per-Step Override auf Bank-Ebene möglich
- [ ] Working Memory überlebt Session-Ende (Priming-Effekt)
- [ ] Session Cache wird bei Session-Ende geflushed

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Retain Enrichment | Integration | Content mit Expectation+Outcome → Experience-Engram | E15 S01 |
| R0 Sequence Analysis | Integration | Konversation → extrahierte Fakten + Action-Effect Chains | E15 S03 |
| Objective Thalamus | Unit | Gleicher Input → gleicher Score (deterministisch) | E16 S01 |
| Surprise from PE | Unit | Expectation↔Outcome → korrekter Surprise-Score | E16 S01 |
| Budget Profile | Integration | Low → Haiku überall, High → Opus für Reasoning | E17 S01 |
| Per-Step Override | Integration | Bank-Config überschreibt Budget-Profil für Schritt X | E17 S02 |
| WM Persistence | Integration | Session-Ende → WM gespeichert → nächste Session findet WM | E18 S02 |
| Session Cache Flush | Integration | Episodic Buffer → Retain, Inferences → WM | E18 S03 |

---

## Milestone 7 — "System Visible" (nach Phase 7: Epic 19-22)

**Aussage:** Das Control Plane zeigt alle brain-inspirierten Features: Engram-Metadaten, Lifecycle-Übersicht, NCR Dashboard, Schema Explorer. Der Operator hat volle Sichtbarkeit.

### Akzeptanzkriterien

- [ ] Memory-Tabelle zeigt Strength, Layer, Access Count pro Engram
- [ ] Memory Detail zeigt Thalamus Scores (4 Dimensionen + Overall)
- [ ] Session Mode Selector in Recall und Reflect Views funktioniert
- [ ] Bank Profile zeigt System Configuration (LLM, DBs, NCR)
- [ ] Engram Lifecycle View zeigt Layer-Verteilung und Strength-Distribution
- [ ] NCR Dashboard: Manueller Trigger, Last Run Summary, Run History
- [ ] NCR Runs werden persistent gespeichert (History)
- [ ] Schema Explorer zeigt Schema-Liste mit Maturity-Badges
- [ ] Schema Detail zeigt Member-Engrams + Mini-Graph (Cytoscape)
- [ ] Sidebar hat 9 Items: Memories, Recall, Reflect, Documents, Entities, Engrams, Consolidation, Schemas, Memory Bank

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Memory Metadata | UI | Strength-Bar, Layer-Badge, Access Count sichtbar | E19 S01 |
| Thalamus Scores | UI | 4+1 Bars im Detail Panel, korrekte Werte | E19 S01 |
| Mode Selector Recall | UI | Mode wählen → Ergebnisse ändern sich | E19 S02 |
| Mode Selector Reflect | UI | Mode wählen → Antwort-Stil ändert sich | E19 S02 |
| System Config | UI | LLM Provider, Tier-Routing, DB Status sichtbar | E20 S02 |
| Engram Stats API | Integration | Layer-Counts und Strength-Distribution korrekt | E21 S01 |
| NCR Trigger | UI | Button → NCR läuft → Report angezeigt | E21 S04 |
| NCR History | Integration | Runs persistiert, History korrekt sortiert | E21 S03 |
| Schema List | UI | Schemas mit Maturity-Badge, Member Count sichtbar | E22 S02 |
| Schema Graph | UI | Cytoscape-Graph zeigt Schema + Members | E22 S02 |

---

## Milestone 8 — "System Validated" (nach Phase 8: Epic 23)

**Aussage:** Das Gesamtsystem ist quantitativ bewertet und erfüllt Mindest-Qualitätsstandards.

### Akzeptanzkriterien

- [ ] Golden Dataset: 100+ Episoden mit Ground Truth
- [ ] Benchmark C Score: Storage ≥ 0.7, Retrieval ≥ 0.6, Evolution ≥ 0.5, Construction ≥ 0.5
- [ ] Simulated Agent Life (30 Tage): System konvergiert, keine Regressionen
- [ ] Benchmark Dashboard: Alle 4 Dimensionen bewertet
- [ ] CI/CD-fähig: `python -m benchmark.validate` → Exit Code 0

### Validierungstests

| Test | Typ | Beschreibung | Epic |
|------|-----|-------------|------|
| Golden Dataset Quality | Validation | Dataset validiert (Schema, Referenzielle Integrität) | E23 S01 |
| Benchmark C Scores | Validation | Alle 4 Dimensionen ≥ Mindest-Score | E23 S04 |
| Convergence | Validation | Simulated Life → System stabilisiert nach N Tagen | E23 S02 |
| Regression Guard | Validation | Kein Score-Drop > 5% vs. vorheriger Run | E23 S04 |
| Full Pipeline | Smoke | Clean DB → Feed → Consolidate → Query → Construct → Validate | E23 S04 |

---

## Milestone-Abhängigkeiten

```
M1 (Foundation) → M2 (Alive) → M3 (Thinks) → M4 (Learns) → M5 (Matures) → M6 (Evolved) → M7 (Visible) → M8 (Validated)
```

Jeder Milestone baut auf dem vorherigen auf. Kein Milestone kann übersprungen werden.
