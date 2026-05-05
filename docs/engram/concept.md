# MemoryManager — Architektur- und Implementierungskonzept

> Brain-Inspired Memory Architecture for AI Agents
> **Leitprinzip:** "What would the brain do?"

---

## 1. Ziel und Kontext

### Was wir bauen

Ein gehirn-inspiriertes Memory-System für AI-Agenten, das auf dem Open-Source-Projekt **Hindsight (Vectorize)** als Fundament aufbaut. Das System ersetzt das flache Fact-basierte Speichermodell durch eine **Engram-basierte Architektur** mit selektivem Speichern, gradueller Konsolidierung, aktivem Vergessen und emergenter Abstraktion.

### Warum

Aktuelle AI-Memory-Systeme leiden unter dem gleichen Problem wie einfache neuronale Netze: **Catastrophic Interference**. Neues Wissen überschreibt altes. Es gibt keine Relevanzbewertung, keine Konsolidierung, kein aktives Vergessen. Das Ergebnis sind Systeme, die entweder alles speichern (und in Rauschen ertrinken) oder willkürlich vergessen.

Die Lösung liegt in der **Complementary Learning Systems Theory** (McClelland, McNaughton & O'Reilly, 1995): Zwei getrennte Systeme mit unterschiedlichen Zeitskalen — ein schnelles, spezifisches System (Hippocampus) und ein langsames, generalisierendes System (Neocortex) — arbeiten komplementär zusammen.

### Wissenschaftliche Grundlage

Die vollständige neurowissenschaftliche Basis ist in 13 Kapiteln dokumentiert:
→ `engram_architecture_complete.md`

Dieses Dokument hier ist das **technische Implementierungskonzept** — es referenziert die Neurowissenschaft, wiederholt sie aber nicht.

### Bestehendes System: Hindsight

Hindsight ist ein Open-Source AI-Agent Memory System mit folgender Architektur:

**Monorepo-Struktur:**
- `hindsight` — Thin Wrapper Layer
- `hindsight-api` — Core Processing (das "Gehirn")
- `PostgreSQL + pgvector` — Data Storage

**3-Schichten-Architektur:**
- **Layer 1 (API):** FastAPI HTTP Server + FastMCP Server
- **Layer 2 (Engine):** MemoryEngineInterface → Retain/Write + Search/Read
- **Layer 3 (Storage):** 6 Datenbanktabellen (memory_units, entities, unit_entities, entity_cooccurrences, memory_links, banks)

**Aktuelle Pipelines:**
- **Retain:** 10 Schritte (Fact Extraction → Embedding → ProcessedFact → Deduplication → Storage → Entity Processing → Temporal/Semantic/Entity/Causal Links) + Background Processing (Opinion Reinforcement, Observation Regeneration)
- **Recall:** N×4-Way Parallel Retrieval (Semantic + BM25 + MPFP Graph + Temporal) → RRF Fusion → Cross-Encoder Reranking → Combined Scoring
- **Reflect:** Internal Recall → Bank Profile → LLM Prompt mit Disposition → Answer → Background Opinion Extraction → Opinion Reinforcement

**Was Hindsight NICHT hat (Gap-Analyse):**
- Kein Thalamus Filter (Relevanzbewertung)
- Kein Engram-Modell (nur flache Facts)
- Keine Tags
- Kein Session-Konzept
- Keine Strength/Decay-Mechanik
- Kein Pre-Engram Buffer
- Kein NCR (Nightly Consolidation Run)
- Kein Schema Store
- Keine synaptische Plastizitätsmodulation

---

## 2. Zielarchitektur — Überblick

### Bio→Architektur Mapping

| Biologie | Architektur-Komponente | Datenbank |
|----------|----------------------|-----------|
| Hippocampus | Pre-Engram Buffer + Engram Dictionary | Neo4j + Qdrant |
| Neocortex | Schema Store / Meta-Engrams | Neo4j + Qdrant |
| Thalamus | Thalamus Filter | Application Layer |
| Dentate Gyrus | Pattern Separation | Application Layer |
| CA3 | Pattern Completion | Application Layer |
| CA1 | Mismatch Detection (Novelty/Surprise) | Application Layer |
| SWS/Sharp-Wave Ripples | NCR Phase 1+2 | Background Process |
| REM Sleep | NCR Phase 3 (Schema Compression) | Background Process |
| PFC | Session + Working Context | Application Layer (transient) |
| Dopamin | Positive Prediction Error → Engram weight up | Scoring Layer |
| Noradrenalin | Surprise Score als Plastizitätsmultiplikator | Scoring Layer |
| Cortisol | Stress-Flag drosselt Plastizität | Scoring Layer |
| LTP Early | Pre-Engram Buffer Entry (fragil) | Neo4j (layer='buffer') |
| LTP Late | Konsolidiertes Engram (nach NCR) | Neo4j (layer='neocortex') |
| STC | Association Window im Pre-Engram Buffer | Neo4j Relationships |

### Systemfluss

```
Neue Episode (Action + Context + Outcome)
        │
        ▼
┌─────────────────┐
│  Thalamus Filter │  ← Novelty, Surprise, Task-Relevance, Emotional Valence
│  (Relevance Gate) │
└────────┬────────┘
         │ relevant
         ▼
┌─────────────────┐
│  Retain Pipeline │  ← Fact Extraction, Embedding, Dedup, Links
│  (erweitert)     │
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│  Working Memory      │  ← PostgreSQL (Agent Session Bank)
│  (kurzfristig)       │
└────────┬────────────┘
         │ Consolidation 1
         ▼
┌─────────────────────┐
│  Engram Buffer       │  ← Neo4j (layer='buffer') + Qdrant (Content)
│  (Dictionary)        │
└────────┬────────────┘
         │ Consolidation 2 (NCR)
         ▼
┌─────────────────────┐
│  Neocortex           │  ← Neo4j (layer='neocortex') + Qdrant (Content)
│  (Langzeit)          │
└────────┬────────────┘
         │ Schema Emergence (Game of Life Regeln)
         ▼
┌─────────────────────┐
│  Meta-Engrams        │  ← Neo4j (Schemas) + Qdrant (Content)
│  (Abstraktion)       │
└─────────────────────┘

         ┌──────────────────────────┐
         │  Retrieval Architecture  │
         │  4 Modi: Precision /     │
         │  Exploration / Analogy / │
         │  Validation              │
         └────────┬─────────────────┘
                  │
                  ▼
         ┌──────────────────────────┐
         │  Constructive Memory     │
         │  {facts, inferences,     │
         │   gaps}                  │
         └────────┬─────────────────┘
                  │
                  ▼
         ┌──────────────────────────┐
         │  Working Context         │
         │  (Goal Stack, Active     │
         │   Engrams, Episodic      │
         │   Buffer)                │
         └──────────────────────────┘
```

---

## 3. Storage-Architektur

### Designentscheidung: Hybrid 3-Datenbank-Modell

Jede Datenbank wird für ihre **Kernkompetenz** eingesetzt:

#### PostgreSQL + pgvector — Agent Session Bank (Working Memory)

**Rolle:** Transaktionale Arbeitsspeicher für laufende Agent-Sessions.

Behält die existierende Hindsight-Architektur (memory_units, entities, links) für schnelle, isolierte Agent-Level-Operationen bei. Ist optimiert für den kurzfristigen Lebenszyklus einer Agent-Session.

**Limitation:** Skaliert nicht für langfristige, cross-agent Speicherung — dafür gibt es die Engram-Schicht.

**Erweiterung:** Neue Dictionary-Tabelle als **Hippocampal Pointer Index** — eine leichtgewichtige Lookup-Tabelle mit Engram-Metadaten (ID, Strength, Layer, Tags, Thalamus-Scores, Timestamps, Status), aber ohne schwere Daten (kein Text, keine Embeddings, keine Relationships). Ermöglicht schnelles Filtern ohne Qdrant/Neo4j abzufragen.

#### Qdrant — Content Store

**Rolle:** Skalierbare Vektorsuche für Engram-Inhalte.

Speichert den eigentlichen Content (Text + Embedding) jedes Engrams, indexiert über die Engram-ID. Optimiert für semantische Ähnlichkeitssuche auf Skalierung. Ein einzelner Qdrant-Query liefert Seed-Kandidaten für die Graph-Traversierung.

**Kernkompetenz:** Vector Similarity Search — das kann PostgreSQL+pgvector nicht in der gleichen Skalierung.

#### Neo4j — Graph Store (Engram Dictionary)

**Rolle:** Retrieval-Orchestrator und Beziehungsspeicher.

Speichert Engrams als Knoten mit allen Beziehungstypen: semantic, temporal, entity, causal, schema, co_activated, temporal_proximity. Führt die Graph-Traversierung durch (MPFP, Spreading Activation, Schema-Link Traversal, Causal Chain Navigation).

**Kernkompetenz:** Index-free Adjacency für O(1)-per-Hop-Performance bei tiefen Traversierungen — das kann keine Vektor-Datenbank.

**Orchestrierungs-Rolle:** Neo4j bestimmt welche Retrieval-Patterns ausgeführt werden, delegiert Vector-Search an Qdrant (Semantic Seeds mit Filtern), traversiert den Graph von den Seed-Knoten aus, und kompiliert die Ergebnisliste mit Activation-Scores.

### Engram-ID Linking

Die Engram-ID ist der Synchronisationspunkt zwischen allen drei Systemen:
- **PostgreSQL Dictionary:** Engram-ID → Metadaten (Strength, Layer, Tags, Scores)
- **Neo4j:** Engram-ID → Knoten mit Relationships
- **Qdrant:** Engram-ID → Point mit Text + Embedding

Struktur trennt Inhalt von Beziehungen. Jede Datenbank kann unabhängig optimiert werden.

### Retrieval-Fluss (End-to-End)

1. Query-Embedding generieren
2. Neo4j wählt Retrieval-Pattern (basierend auf Session Mode)
3. Qdrant liefert top-k ähnliche Vektoren als Seed-Knoten (ein Query, schnell)
4. Neo4j traversiert den Graph von den Seeds aus
5. Neo4j liefert Engram-Liste mit Activation-Scores
6. Qdrant liefert Full Content für die Ergebnis-IDs

---

## 4. Engram Data Model

### Definition

Ein Engram ist die zentrale Wissenseinheit. Es ist kein einfacher "Fakt" — es ist ein **bedeutungsvolles Muster** mit Stärke, Kontext und Aktivierungsgeschichte.

### Engram-Felder

```
Engram {
    id: UUID                          # Eindeutige Identifikation
    embedding: Vector[384]            # Semantische Repräsentation (BAAI/bge-small-en-v1.5)
    text: String                      # Inhalt (in Qdrant)
    tags: List[String]                # Kategorisierung (ersetzt fact_type)
    strength: Float                   # Konsolidierungsstärke (0.0 → 1.0)
    layer: Enum[buffer, neocortex]    # Konsolidierungsstufe
    abstraction_level: Float          # 0.0 (spezifisch) → 1.0 (abstrakt/Schema)

    # Thalamus Scores (bei Ersterfassung berechnet)
    novelty: Float
    surprise: Float
    task_relevance: Float
    emotional_valence: Float
    thalamus_overall: Float           # Gewichtete Kombination

    # Temporale Metadaten
    created_at: Timestamp
    last_accessed: Timestamp
    access_count: Integer
    session_ref: UUID                 # Referenz zur Ursprungssession

    # Status
    status: Enum[active, archived, decayed]
    confidence_score: Float
}
```

### Episode als Input

```
Episode {
    action: String                    # Was der Agent getan hat
    context: String                   # In welchem Kontext
    outcome: String                   # Was passiert ist
}
```

### Session als Steuerungskontext

```
Session {
    current_mode: Enum[Exploration, Precision, Analogy, Validation]
    current_expectation: String       # Für Prediction Error Detection
    task_context: String              # Aktueller Task-Kontext
}
```

---

## 5. Thalamus Filter

### Funktion

Relevance Scoring Gate — entscheidet was gespeichert wird und mit welcher initialen Stärke. Jede eingehende Episode wird auf 4 Dimensionen bewertet:

| Score | Beschreibung | Biologisches Vorbild |
|-------|-------------|---------------------|
| **Novelty** | Wie neu/unbekannt ist die Information? | CA1 Mismatch Detection |
| **Surprise** | Wie unerwartet relativ zur current_expectation? | Noradrenalin-Ausschüttung |
| **Task-Relevance** | Wie relevant für den aktuellen Task? | PFC Top-Down Attention |
| **Emotional Valence** | Wie emotional bedeutsam? | Amygdala Modulation |

### Overall Score

Gewichtete Kombination der 4 Scores. Die Gewichtung wird durch den **Session Mode** moduliert:
- **Exploration:** Novelty-Boost
- **Precision:** Task-Relevance-Boost
- **Validation:** Surprise-Boost

### Schwellenwert

Liegt der Overall Score unter dem Threshold, wird die Episode verworfen. Der Threshold ist mode-abhängig (Exploration hat niedrigeren Threshold als Precision).

---

## 6. Retain Pipeline (erweitert)

### Erweiterungen gegenüber Hindsight (R1-R5)

**R1 — ExtractedFact/ProcessedFact erweitern:**
- `tags: List[String]` hinzufügen (ersetzt rigides `fact_type`)
- `thalamus_scores` hinzufügen (novelty, surprise, relevance, emotion, overall)

**R2 — Embedding-Anreicherung:**
- Aktuell: Nur Fact-Text + Datum
- Neu: Text + temporaler Kontext + Session-Kontext + Thalamus-Scores
- Ziel: Reichere Embeddings für bessere semantische Ähnlichkeit

**R3 — Score-aware Deduplication:**
- Aktuell: Semantic + Temporal (24h Fenster)
- Neu: Bei Duplikat-Erkennung gewinnt der höher bewertete Fakt (Thalamus Score + Strength)

**R4 — Entity Processing Erweiterung:**
- Entity-Resolution mit LLM-Support für ambige Entitäten

**R5 — Link-Erweiterung:**
- Neue Link-Typen: `co_activated`, `temporal_proximity`, `schema`
- Co-Activation Links bei Retain UND Recall erzeugen
- Schema-Links inkrementell bei Retain (Game of Life Regel R4)

### Link-Typen (vollständig)

| Link-Typ | Beschreibung | Erzeugung |
|-----------|-------------|-----------|
| semantic | Cosine Similarity | Retain |
| temporal | 24h Fenster Proximity | Retain |
| entity | Geteilte Entity-IDs | Retain |
| causal | LLM-extrahierte Kausalität (causes, caused_by, enables, prevents) | Retain |
| co_activated | Wiederholte gleichzeitige Retrieval-Aktivierung | Retain + Recall |
| temporal_proximity | Zeitfenster-basierte Co-Aktivierung | Retain |
| schema | Verbindung zu Schema/Meta-Engram | NCR + Retain (inkrementell) |

---

## 7. Session Layer

### Position in der Architektur

Transientes Objekt im Application Layer. Lebt **über** der MemoryEngine, wird nicht in der Datenbank persistiert. Steuert das gesamte Verhalten des Memory-Systems während einer Agent-Session.

### Dual Control

**Explizit (bewusst):** Agent oder User setzt den Mode manuell.

**Automatisch (unbewusst):** System erkennt Signale und passt den Mode an:
- Hoher Surprise Score → Shift zu Validation
- Prediction Error → Shift zu Validation
- Schwache Matches → Shift zu Exploration
- Widersprüchliche Evidenz → Shift zu Validation

### 4 Modi und ihre Konfiguration

| Aspekt | Precision | Exploration | Analogy | Validation |
|--------|-----------|-------------|---------|------------|
| **MPFP Patterns** | Kurz, hohe Thresholds | Lang, niedrige Thresholds | Schema-Links | Causal + Contradiction |
| **Strength Pre-Filter** | ≥ 0.05 | ≥ 0.0 | ≥ 0.05 | ≥ 0.1 |
| **CE-Threshold (sigmoid)** | ≥ 0.05 | ≥ 0.01 | ≥ 0.02 | ≥ 0.03 |
| **Tag-Overlap Gewicht** | 0.15 (höchstes) | 0.05 | 0.05 | 0.10 |
| **Thalamus Boost** | Task-Relevance | Novelty | — | Surprise |
| **Weak Links** | Ignoriert | Folgt | Bevorzugt | Ignoriert |
| **Traversal Depth** | Flach (1 Hop) | Tief (3+) | Mittel (2) | Mittel (2) |
| **Max Results** | 3 | 10 | 5 | 5 |
| **Construction** | Konservativ | Kreativ | Cross-Domain | Evidenz-basiert |
| **Reconsolidation** | Minimal | Moderat | Schema-Update | Aggressiv |

> **Zur Strength-Pre-Filter-Kalibrierung (2026-04-09):** Die Werte wurden gegenüber älteren Design-Dokumenten (Precision 0.5, Validation 0.3) deutlich gesenkt. Frische Buffer-Engrams initialisieren mit `strength ≈ 0.1` (siehe Consolidation 1); die alten Schwellen hätten praktisch jedes nicht-konsolidierte Engram aus dem Recall geworfen. Der Scoring-Stage (`w5 × strength_weight` mit log-Dampening) holt die Stärke-Information später mit weniger zerstörerischer Wirkung wieder rein.

> **Zur CE-Threshold-Kalibrierung (2026-04-23):** CE-Werte sind seit dem Threshold-Filter-Fix sigmoid-normalisiert (`reranking.py:94-97`), nicht mehr Roh-Logits. Sigmoid(−3.5) ≈ 0.029 — Werte in dieser Größenordnung sind für Single-Token-Queries gegen lange Engrams die Norm. Daher sind die Schwellen ≤ 0.05; höhere Werte würden auch exakte Matches wegfiltern. Siehe `score-formulas.md` Kalibrierungs-Abschnitt für die Modell-Bindung.

### Mode als Transient Signal

Der Mode wird nicht gespeichert — er fließt als Parameter durch alle Storage-Tiers:
- **PostgreSQL:** Mode → SQL-Thresholds
- **Neo4j + Qdrant:** Mode → EngramRetriever-Patterns + Filter

---

## 8. Search & Retrieval (erweitert)

### Erweiterungen gegenüber Hindsight (S1-S6)

**S1 — Fact-Type Filter ablösen:**
- Aktuell: `AND fact_type = $3` → separate Queries pro Type (12 Queries)
- Neu: Tags-basierte Filterung, ein Query über alle Types
- Hybrid-Architektur löst das Problem inhärent: Qdrant liefert Seeds type-agnostisch, Neo4j traversiert type-agnostisch

**S2 — Mode-aware MPFP Patterns:**
- Aktuell: 7 hardcoded Patterns (5 semantic + 2 temporal), identisch für jeden Query
- Neu: Pattern-Sets konfigurierbar pro Mode via erweitertem MPFPConfig
- API: `recall_async(query, mode='precision')` — Mode als Parameter, Pattern-Selection intern

**S3 — Thalamus-Score Pre-Filter + Scoring:**
- **Level 1 (Pre-Filter):** Strength-Threshold vor teuren Pipeline-Stufen (mode-abhängig)
- **Level 2 (Scoring):** Gewichteter Engram Strength + Thalamus Scores + Confidence in der Ranking-Formel
- Mode-spezifische Thalamus-Score-Gewichtung (Exploration → Novelty-Boost, Precision → Relevance-Boost)

**S4 — Recency-Decay Modulation:**
- Aktuell: Fixer 365-Tage Half-Life (hardcoded)
- Neu: Decay-Rate moduliert durch Engram Strength aus NCR
- Gut konsolidierte Engrams decayen langsamer als schwach konsolidierte

**S5 — Session-Mode steuert Disposition:**
- Aktuell: Disposition (Personality) beeinflusst Retrieval
- Neu: Session Mode übernimmt die Steuerung; Disposition bleibt für Reflect/Reconsolidation

**S6 — Retriever-Architektur:**
- Hindsight hat sauberes GraphRetriever-Interface mit pluggbarer `retrieve()`-Methode
- **Entscheidung: Neuer EngramRetriever, gleiches Interface, keine Subclass.** Arbeitet fundamental anders als BFS/MPFP — orchestriert Neo4j + Qdrant statt PostgreSQL+pgvector zu traversieren
- Beide Retriever existieren parallel: Agent Session Bank → bestehender MPFP/BFS-Retriever (PostgreSQL), Shared Bank → EngramRetriever (Neo4j + Qdrant)
- Session Layer routet zum richtigen Retriever je nach Bank

### Scoring-Formel (erweitert)

Aktuell Hindsight (Fallback ohne Session): `60% CE + 20% RRF + 10% Temporal + 10% Recency`

Mit ModeConfig: `w1×CE + w2×RRF + w3×Temporal + w4×Recency(strength-moduliert) + w5×Engram_Strength + w6×Thalamus_Weighted + w7×Tag_Overlap`

Gewichte mode-abhängig konfiguriert (`mode_config.py:_WEIGHTS_PRECISION/EXPLORATION/ANALOGY/VALIDATION`).
`tag_overlap` ist ein Jaccard-Score zwischen den implizit aus der Query extrahierten Tokens und den auf dem Engram gespeicherten Tags
(Pure Functions in `engine/search/tag_overlap.py`, Kill-Switch via Env-Var `HINDSIGHT_API_TAG_OVERLAP_ENABLED`).

> **Konzeptlücken (zwei Phase-2-Kriterien aus `11_retrieval_architecture.md` §3.3 sind im Code noch nicht verdrahtet):**
> - **Schema Prediction Match** — vorgesehen als "Medium"-Kriterium, hängt an Epic 13 (Schema Emergence). Heute wird der Match nicht ins Scoring eingespeist.
> - **Outcome Weight** — `expectation`/`outcome` werden bei Recall aus `engram_dictionary` geladen, fließen aber nur in die Provenance, nicht ins Ranking. Ein Outcome-getriggertes Strength-Update existiert über die Reconsolidation, aber kein direkter Score-Beitrag.
> Beide werden bewusst aufgeschoben bis Schema-System und Outcome-Tracking belastbare Signale liefern.

### S7 — BM25 Safety-Rescue (2026-04-23)

Sigmoid-normalisierte CE-Scores für Single-Token-Queries gegen lange Engrams können bei `~0.03` landen — unter jeder Mode-Schwelle. Bei
exakten Keyword-Matches ist das ein False-Negative.

**Mechanismus** (`recall_orchestrator.py` Step 5.5): Wenn der CE-Filter **alle** Kandidaten verwirft (`top_scored == [] AND ce_filtered > 0`),
wird die Top-N nach `bm25_score > 0` aus den vor-Filter-Kandidaten wiederhergestellt (N = `max_results` aus Mode-Config).

**Mode-Wirkung:** Greift in allen Modi, aber spürbar primär in Precision (höchste CE-Schwelle 0.05). Der Rescue erfüllt die in `11_retrieval_architecture.md` §4 Mode 1 spezifizierte Promise "exact Context Tag Overlap" auch dann, wenn der Cross-Encoder die Semantik einer rein lexikalischen Übereinstimmung niedrig bewertet — ohne den Threshold global zu lockern und damit Cross-Topic-Noise einzulassen.

**Trade-off:** Precision wird minimal weniger "strict" (CE kann komplett verworfen werden, BM25 rettet), bekommt dafür aber zwei orthogonale "exact match"-Signale (CE-Semantik + Tag-Overlap-Term + BM25-Rescue als Safety-Net).

---

## 9. Working Context

### Funktion

Transientes PFC-Äquivalent — der Workspace während laufender Tasks. Hält den aktiven Kontext zusammen, ohne ihn zu persistieren.

### Struktur

```
WorkingContext {
    goal_stack: List[Goal]                # Aktive Ziele (Stack)
    active_engrams: {
        focus: List[Engram]               # Direkt relevant (3-5)
        supporting: List[Engram]          # Kontext-gebend (5-10)
        peripheral: List[Engram]          # Schwach aktiviert (10-20)
    }
    episodic_buffer: List[Episode]        # Aktuelle Episoden dieser Session
    inference_layer: List[Inference]      # Laufende Schlussfolgerungen
}
```

### Lebenszyklus

Wird bei Session-Start erzeugt, bei Session-Ende verworfen. Relevante Inhalte fließen über die Retain Pipeline in das Engram-System.

---

## 10. Reflect & Reconsolidation (erweitert)

### Erweiterungen gegenüber Hindsight (RF1-RF4)

**RF1 — Priority-basierte Reconsolidation:**
Reconsolidation gilt für ALLE Engram-Typen (nicht nur Opinions wie aktuell).
Prioritäts-Reihenfolge:
1. **Strength-basiert:** Schwache Engrams zuerst (sind fragiler)
2. **Prediction Error:** Engrams die bei Recall einen Prediction Error verursacht haben
3. **Disposition-Einfluss:** Agent-Persönlichkeit moduliert die Reconsolidation

**RF2 — Retrieval-Cost Optimierung:**
- Aktuell: 12 parallele DB-Queries (4 Methoden × 3 fact_types)
- Neu: Hybrid-Architektur löst das inhärent — ein Qdrant-Query für Seeds, Neo4j-Traversal type-agnostisch

**RF3 — Semantic Trigger statt Timer:**
- Aktuell: Opinion Reinforcement bei exaktem Entity-Match
- Neu: Cosine Similarity ≥ 0.6 als zusätzlicher Trigger (schon implementiert in Hindsight)
- Qdrant macht den Similarity-Check computationally cheap

**RF4 — Disposition in Reconsolidation:**
- Agent-Persönlichkeit (Disposition) beeinflusst WIE Engrams bei Reconsolidation modifiziert werden
- Optimistischer Agent: Stärkt positive Engrams mehr
- Analytischer Agent: Gewichtet Evidenz höher

---

## 11. Constructive Memory

### Kernidee

Retrieval ist **Rekonstruktion**, nicht Lookup. Das System gibt nicht einfach gespeicherte Fakten zurück — es konstruiert eine Antwort aus Fragmenten, ergänzt Lücken durch Inferenz, und markiert Unsicherheiten.

### Retrieval Payload

```
ConstructedAnswer {
    facts: List[Fact]                    # Direkt aus Engrams abgerufene Fakten
    inferences: List[Inference]          # Abgeleitete Schlussfolgerungen
    gaps: List[Gap]                      # Identifizierte Wissenslücken
    confidence: Float                    # Gesamtvertrauen
    mode_influence: String               # Wie der Mode die Konstruktion beeinflusst hat
}
```

### Mode-Einfluss auf Construction

- **Precision:** Konservativ — wenig Inferenz, strenge Fakten-Basis
- **Exploration:** Kreativ — mehr Inferenz, weichere Verbindungen zugelassen
- **Analogy:** Cross-Domain — Inferenz über Schema-Grenzen hinweg
- **Validation:** Evidenz-basiert — Gegenargumente und Widersprüche hervorheben

### Prediction Error Detection

Wenn die konstruierte Antwort von `Session.current_expectation` abweicht, entsteht ein **Prediction Error**. Dieser:
1. Füttert zurück in die **Reconsolidation** (RF1) → beteiligte Engrams werden modifiziert
2. Kann den **Session Mode** shiften (z.B. → Validation wenn Widerspruch erkannt)

---

## 12. Consolidation Pipeline

### 4-Stufen-Modell

```
Working Memory (PostgreSQL)
    │
    │ Consolidation 1: Thalamus-gefilterte Facts → Engrams
    ▼
Engram Buffer (Neo4j layer='buffer' + Qdrant)
    │
    │ Consolidation 2: NCR (Nightly Consolidation Run)
    ▼
Neocortex (Neo4j layer='neocortex' + Qdrant)
```

**Consolidation 1** transformiert kurzfristige Facts aus dem Working Memory in Engrams. Thalamus-Score bestimmt initiale Strength.

**Consolidation 2** ist ein **Dictionary Property Update** (layer='buffer' → layer='neocortex'), keine physische Datenkopie. Wird durch den NCR ausgelöst.

### Nightly Consolidation Run (NCR)

Drei Phasen, biologisch inspiriert:

**Phase 1 — Decay (SWS/Sharp-Wave Ripples Äquivalent):**
- Schwache Engrams (niedrige Strength, wenig Access) werden weiter geschwächt
- Unter Decay-Threshold: Status → archived/decayed

**Phase 2 — Strengthen (SWS Äquivalent):**
- Häufig aktivierte Engrams werden verstärkt
- Consolidation 2: layer='buffer' → layer='neocortex'

**Phase 3 — Schema Compression (REM Sleep Äquivalent):**
- Game of Life Regeln identifizieren Muster über Engrams
- Meta-Engrams / Schemas werden erzeugt oder gestärkt
- Abstraktions-Level steigt

---

## 13. Schema Emergence

### Design-Philosophie: Flat Graph, Emergente Hierarchie

Alle Memory-Knoten (Engrams, Schemas, Meta-Engrams) existieren in einem **einzigen flachen Graphen**. Es gibt keine vorgegebenen Schichten oder Hierarchien. Abstraktionslevel variiert über Knoten hinweg, aber die physische Struktur bleibt flach — genau wie im Neocortex.

Schemas sind **keine vordefinierten Kategorien**. Sie entstehen emergent aus den Daten durch einfache lokale Regeln — analog zu Conway's Game of Life.

### 5 Game-of-Life Regeln

**R1 — Clustering/Birth:**
Wenn 3+ Engrams M+ gemeinsame Nachbarn teilen (über shared Entities und semantische Ähnlichkeit), bilden sie einen Cluster-Kandidaten.

**R2 — Repetition/Maturation:**
Ein Cluster wird erst zum Schema-Kandidaten, nachdem er K NCR-Zyklen überlebt hat, ohne zu decayen. Einmalige Cluster werden ignoriert — nur wiederkehrende Muster werden zu Schemas.

**R3 — Abstraction/Specialization:**
Gemeinsame Properties der Cluster-Engrams werden extrahiert und als Schema-Properties codiert. Das Schema abstrahiert das Gemeinsame.

**R4 — Reinforcement/Growth:**
Neues Engram das zu einem bestehenden Schema-Pattern passt, stärkt das Schema und erzeugt eine neue Verbindung. **Diese Regel läuft auch inkrementell bei Retain** (nicht nur im NCR).

**R5 — Competition/Death:**
Schwache Schemas, die über mehrere NCR-Zyklen nicht verstärkt werden, sterben. Verhindert Schema-Inflation.

### Ausführungskontexte

- **NCR Phase 3 (Batch):** R1, R2, R3, R5 — Whole-Graph Pattern Detection
- **Retain (Inkrementell):** R4 — Sofortiger Schema-Fit-Check für neue Engrams

---

## 14. Weak Connections & Synaptic Tagging

### Wert von Weak Connections

Schwache Verbindungen sind nicht "fast gelöschte" Verbindungen — sie sind ein eigener Informationsträger:
- **Exploration Mode:** Kreatives, assoziatives Denken über schwache Links
- **Schema Formation (NCR Phase 3):** Mustererkennung über schwache Cluster
- **Serendipität:** Unerwartete Einsichten über Verbindungen die starke Links nicht zeigen

### Neo4j Relationship-Typen

Weak Connections als **Relationships mit spezialisierten Eigenschaften:**
- `co_activated` — bei wiederholter gleichzeitiger Retrieval-Aktivierung
- `temporal_proximity` — bei zeitlicher Co-Occurrence

### Mode-abhängiges Traversal

| Mode | Weak Link Verhalten |
|------|-------------------|
| Precision | Ignoriert |
| Exploration | Folgt |
| Analogy | Bevorzugt (findet Cross-Domain-Muster) |
| Validation | Ignoriert |

### MPFP Threshold Problem (gelöst)

Hindsight's MPFP hat einen Threshold von 0.1 der Weak Links herausfiltert. Die Neo4j-basierte Lösung umgeht das: der EngramRetriever hat eigene Traversal-Logik die den MPFP-Threshold nicht erbt.

---

## 15. Multi-Bank Architecture

### 3-Tier Bank Model

```
Tier 1: Agent Session Bank (PostgreSQL)
    │   Isoliert pro Agent. Kurzfristiger Working Memory.
    │
    │ Consolidation 1
    ▼
Tier 2: Agent Engram Dictionary (Neo4j + Qdrant)
    │   Agent-spezifische Engrams. Gefiltert und bewertet.
    │
    │ Consolidation 2 (NCR)
    ▼
Tier 3: Shared Memory Bank (Neo4j + Qdrant)
        Cross-Agent Wissen. Schemas und Meta-Engrams.
```

### Design-Entscheidungen (B1-B6)

**B1 — 3-Tier Bank Model:**
Grundarchitektur oben definiert. Agent Session Bank (PostgreSQL) → Agent Engram Dictionary (Neo4j + Qdrant) → Shared Memory Bank (Neo4j + Qdrant).

**B2 — Write Conflict Resolution:**
Wenn zwei Engrams zum Shared Bank konsolidiert werden und Semantic Similarity ≥ 0.85:
- Kein inhaltlicher Widerspruch → **Merge**: stärkeres Engram wird Basis, schwächeres liefert Kontext
- Inhaltlicher Widerspruch → **Höherer Score gewinnt**, schwächeres Engram wird über **contradiction-Link** verbunden (keine Information geht verloren, Widerspruch bleibt im Graph sichtbar)
- Bei gleichem Score → **Neueres Engram gewinnt** (Recency)

**B3 — Cross-Bank Novelty Scoring:**
Zwei-Stufen Novelty Check bei Consolidation Agent → Shared:
1. Qdrant Similarity Query gegen Shared Bank (Similarity ≥ 0.85?)
2. Wenn Match: Bestehendes Shared-Engram **reinforcen** (Strength erhöhen, Access Count hoch, ggf. B2-Logik bei Abweichung)
3. Wenn kein Match: Neues Engram im Shared Bank anlegen

**B4 — Shared-to-Agent Feedback Loop:**
Schema-Recall bei Query-Zeit — kein aktiver Sync nötig:
- Jeder `recall_async` geht automatisch parallel an Agent Session Bank UND Shared Bank
- Ergebnisse werden fusioniert (RRF oder gewichtetes Merging)
- Shared-Bank-Ergebnisse werden als `source: shared` markiert
- Shared-Ergebnisse initial leicht niedriger gewichtet (weniger kontextspezifisch), aber starke Schemas können Agent-eigene Ergebnisse überwiegen

**B5 — Consolidation Triggers:**
Drei Trigger-Typen, alle im NCR-Kontext (kein Echtzeit-Push):
1. **NCR-basiert (Hauptkanal):** Engrams mit `layer='neocortex'` und `strength ≥ Threshold` + B3 Novelty Check bestanden → Shared Bank Promotion
2. **Cross-Agent Convergence:** Mehrere Agents bilden unabhängig ähnliche Engrams (Similarity ≥ 0.85 cross-agent) → erhöhte Priorität für Shared-Bank-Promotion
3. **Schema-Kandidat:** Engram wird Teil eines Schema-Clusters (Game of Life R1+R2) → automatischer Shared-Bank-Kandidat

**B6 — Cross-Bank Query:**
Query-Routing durch Session Layer:
- Default Dual-Query: Jeder `recall_async` geht parallel an Agent Session Bank (PostgreSQL) + Shared Memory Bank (Qdrant + Neo4j)
- Mode-abhängige Bank-Gewichtung: Precision → Agent Bank höher | Exploration/Analogy → Shared Bank höher | Validation → beide gleichwertig
- Kein direktes Cross-Agent-Read — Cross-Agent-Wissen fließt ausschließlich über Shared Bank (Agent-Isolation gewahrt)
- Technisch identisch mit B4 (Feedback Loop = Cross-Bank Query)

---

## 16. LLM Routing

### Entscheidung: Rule-based, nicht dynamisch

Feste Task-zu-Model-Tier-Zuordnung. Kein dynamisches Routing zur Laufzeit.

### 3 Tiers

| Tier | Aufgaben | Modelle |
|------|----------|---------|
| Small/Fast | Einfache Extraktion, Deduplication-Checks | Claude Haiku / GPT-4o-mini |
| Medium | Entity Resolution, Scoring | Claude Sonnet / GPT-4o |
| Large/Reasoning | Komplexe Fact Extraction, kausale Relationen, Conflict Resolution | Claude Opus |

### Implementation (L1-L3)

**L1:** Vollständiges Task-to-Model-Tier Mapping für alle Operationen definieren.

**L2:** LLMConfig um Per-Subtask Model Assignment erweitern (aktuell: nur per-Operation, z.B. retain_llm vs reflect_llm).

**L3:** Konkrete Model-Mappings pro Provider.

---

## 17. Benchmarking & Validation

### 4 Dimensionen

**Storage Validation:** Fact Extraction Accuracy, Embedding Quality, Link Creation, Thalamus Scores, Entity Resolution.

**Retrieval Validation:** Precision/Recall, Ranking-Qualität, Mode-Dependency, Graph Traversal, Temporal Queries.

**Knowledge Evolution:** Engram Strength Tracking, Reconsolidation, Weak Links, Schema Formation, Decay Patterns.

**Construction Quality:** Inference-Generierung, Gap-Identifikation, Mode Shaping, Prediction Error.

### 3 Test-Ansätze (alle drei, gestaffelt)

**A — Scripted Scenarios (begleitend ab Phase 1):**
Vordefinierte Input/Output-Paare. Deterministisch. Jedes Epic bekommt Scripted Tests die belegen dass die Komponente funktioniert. Schnelle Feedback-Loops während der Entwicklung. Basis für Regression.

**B — Simulated Agent Life (ab Phase 5 — Langzeit-Prozesse):**
Agent operiert über mehrere simulierte Tage. Testet Memory-Evolution über Zeit: Consolidation, Schema Emergence, Decay, Weak Connection Formation. Erst sinnvoll wenn die Langzeit-Komponenten (Epic 12-14) stehen.

**C — Golden Dataset (am Ende — Qualitätsmessung):**
Kuratiertes Dataset mit Ground Truth. Inspiriert von BEIR/MS MARCO, adaptiert für Engram-Modell. Quantitativer Benchmark für das Gesamtsystem. Vergleichbar mit externen Standards.

---

## 18. Referenzen

### Wissenschaftliche Grundlage
→ `engram_architecture_complete.md` (13 Kapitel)

### Architekturentscheidungen
→ Hindsight Memory Bank (m2-consulting) — alle Entscheidungen mit Kontext und Begründung

### Backlog
→ `backlog/epic-overview.md` — 15 Epics in Umsetzungsreihenfolge

### Bestehender Code
→ `hindsight/` — Hindsight Monorepo (Basis-System)

### Diagramme
→ `hindsight_search_sequence.mermaid` — Search Pipeline Sequenzdiagramm
→ `hindsight_reflect_sequence.mermaid` — Reflect Pipeline Sequenzdiagramm
→ `thalamus_component.mermaid` — Thalamus Komponentendiagramm
→ `thalamus_sequence.mermaid` — Thalamus Sequenzdiagramm
