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

### Designentscheidung: Hybrid 3-Datenbank-Modell mit klarer DB-zu-Speicher-Zuordnung

Die drei Datenbanken haben jeweils eine **scharf definierte Rolle**, abgeleitet aus der CLS-Trennung Hippocampus/Cortex:

| Speicher | Was lebt dort | Datenbanken |
|---|---|---|
| **Working Memory** | frische Engrams (vor C1) | PostgreSQL |
| **Buffer (Hippocampus)** | wichtige Engrams (nach C1) | PostgreSQL + Qdrant |
| **Neocortex** | nur Schemas, keine individuellen Engrams | Neo4j + Qdrant |

#### PostgreSQL + pgvector — Engram-Heimat (Working Memory + Buffer)

**Rolle:** Transaktionaler Speicher für **alle individuellen Engrams** (Working Memory und Buffer). Hält Content, Metadaten, Tags, Strength, Thalamus-Scores, Timestamps und Status pro Engram.

Behält die existierende Hindsight-Tabellenstruktur (memory_units, entities, links) als technische Grundlage. Ergänzt um eine **Hippocampal Pointer Index**-Tabelle: leichtgewichtige Lookup-Tabelle mit Engram-Metadaten (ID, Strength, Layer ∈ {working, buffer}, Tags, Thalamus-Scores, Timestamps, Status) — ohne schwere Daten. Ermöglicht schnelles Filtern ohne Qdrant abzufragen.

**Wichtig:** Das `layer`-Feld kennt nur noch `working` und `buffer`. Es gibt **kein** `layer='neocortex'`-Engram — der Neocortex enthält keine individuellen Engrams mehr.

#### Qdrant — Vektor-Store für Engrams **und** Schemas

**Rolle:** Skalierbare Vektorsuche für zwei verschiedene Inhaltstypen, die in derselben Collection koexistieren:

- **Engram-Embeddings** (`payload.kind = "engram"`) — Vektor pro Engram aus Buffer/Working
- **Schema-Centroids** (`payload.kind = "schema"`) — Mittelwert-Vektor pro Schema, von C2 berechnet

Beim Recall durchsucht eine **einzige Vektor-Search beide Räume gleichzeitig**. Treffer können Engrams oder Schemas sein, je nach Cosine-Distanz zur Query.

**Kernkompetenz:** Vector Similarity Search auf Skalierung — das kann PostgreSQL+pgvector nicht in der gleichen Größenordnung.

#### Neo4j — Schema-Graph (Neocortex)

**Rolle:** Speichert ausschließlich **Schemas und Hyper-Schemas** — die abstrakten Wissensstrukturen. Keine individuellen Engrams.

**Knoten-Typen:**
- `(:Schema)` — von C2 erzeugt, mit Description, Properties, evidence_engram_ids, evidence_count
- `(:HyperSchema)` — von C3 R3 erzeugt, generalisiert über mehrere Schemas

**Edge-Typen:**
- `(:Schema)-[:SPECIALIZES]->(:HyperSchema)` — Subsummation-Beziehung
- weitere Schema-Schema-Beziehungen (entity_overlap, causal, temporal_succession) optional, je nach Game-of-Life-Erweiterungen

**Wichtig:** Es gibt **keine `:Engram`-Knoten** in Neo4j. Schemas verweisen auf Engrams ausschließlich via `evidence_engram_ids`-Array (Property), nicht über Edges. Das spart einen ganzen Knoten-Typ und entspricht dem Indexing-Modell von Teyler & DiScenna (1986).

**Kernkompetenz:** Schema-Schema-Traversierung mit O(1)-per-Hop für Schema-Hierarchie-Queries und Hyper-Schema-Auflösung.

### ID-Linking zwischen den DBs

| Identifier | PostgreSQL | Qdrant | Neo4j |
|---|---|---|---|
| `engram_id` (UUID) | row in `memory_units` | point mit `payload.kind="engram"` | — (existiert nicht in Neo4j) |
| `schema_id` (UUID) | — | point mit `payload.kind="schema"` (Centroid) | `(:Schema {id})` Knoten |

**Cross-DB-Auflösung Schema → Engram:** Schema-Knoten in Neo4j hält ein UUID-Array `evidence_engram_ids: [UUID; N]`. Beim Recall wird dieses Array per `WHERE id IN (...)` an PostgreSQL übergeben → Top-N Evidence-Engrams werden geladen. Kein Cross-DB-Edge, kein Stub-Knoten.

### Retrieval-Fluss (End-to-End)

1. Query-Embedding generieren
2. **Qdrant Vector-Search** über die gemischte Collection → Top-K Treffer (Schemas und/oder Engrams)
3. Pro Treffer:
   - **Engram-Treffer:** Metadaten aus PostgreSQL laden (Content, Strength, Tags)
   - **Schema-Treffer:** Schema-Knoten aus Neo4j laden (Description, Properties, evidence_engram_ids); optional Top-N Evidence-Engrams aus PostgreSQL nachholen
4. Reflect-Pipeline (Kapitel 10) synthetisiert die Antwort aus dem Mischresultat

Damit gibt es keine zentrale "Orchestrator"-DB mehr — Qdrant ist der Einstiegspunkt, PostgreSQL und Neo4j sind nachgelagerte Detail-Provider. Das ist konzeptuell sauberer als das vorherige Neo4j-zentrische Modell.

---

## 4. Engram Data Model

### Definition

Ein **Engram** ist die individuelle Wissenseinheit — eine einzelne Erinnerung mit Stärke, Kontext und Aktivierungsgeschichte. Engrams leben im **Working Memory** (frisch eingegangen) oder im **Buffer** (Hippocampus-Äquivalent — wichtige Engrams, die durch C1 promotet wurden).

Ein **Schema** ist die abstrakte Wissenseinheit — ein aus mehreren Engrams emergent abgeleitetes Muster. Schemas leben **ausschließlich im Neocortex** (Neo4j). Sie haben kein eigenes 1:1-Pendant zu individuellen Engrams; stattdessen verweisen sie über einen Top-N Evidence-Index auf ihre stärksten Belege im Buffer.

Diese saubere Trennung folgt der CLS-Theorie (McClelland/McNaughton/O'Reilly 1995): Hippocampus speichert Episoden, Cortex speichert statistische Regelmäßigkeiten.

### 4.1 Engram-Felder

```
Engram {
    id: UUID                          # Eindeutige Identifikation
    embedding: Vector[384]            # Semantische Repräsentation (BAAI/bge-small-en-v1.5)
    text: String                      # Inhalt (in Qdrant payload + PostgreSQL)
    tags: List[String]                # Strukturierte Kategorisierung (z.B. activity, mood, participants)
    strength: Float                   # Konsolidierungsstärke (0.0 → 1.0)
    layer: Enum[working, buffer]      # Konsolidierungsstufe — kein neocortex (dort leben nur Schemas)

    # Thalamus Scores (bei Ersterfassung berechnet)
    novelty: Float
    surprise: Float
    task_relevance: Float
    emotional_valence: Float
    thalamus_overall: Float           # Gewichtete Kombination

    # Temporale Metadaten
    created_at: Timestamp             # Wallclock (Debug, Humans, Audit)
    created_at_session: Integer       # Snapshot von bank.session_counter bei Creation
                                      # (immutable — Basis für sessions_alive-Derivation)
    last_accessed: Timestamp
    access_count: Integer
    session_ref: UUID                 # Referenz zur Ursprungssession

    # Status
    status: Enum[active, archived, decayed]
    confidence_score: Float
}
```

**Hinweis zu `abstraction_level`:** In früheren Konzeptversionen trug das Engram selbst ein Abstraktionslevel-Feld. Mit der CLS-konformen Trennung Buffer/Neocortex entfällt das — Engrams sind immer "spezifisch" (Episoden), Abstraktion liegt in den Schemas.

### 4.2 Schema-Felder (Neocortex-Knoten)

Schemas sind eigenständige Knoten im Neo4j-Graphen mit drei parallelen Repräsentationen ihres Inhalts. Centroid (Vektor) ist die "Adresse" im Embedding-Raum, Description (Klartext) und Properties (Key-Value) sind der menschenlesbare bzw. strukturierte Inhalt.

```
Schema {
    id: UUID
    description: String               # Klartext, vom kleinen LLM oder Template generiert
                                      # z.B. "1:1 Coffee-Meetings am Nachmittag, ~45min, produktiv"

    properties: Map[String, Any]      # Statistisch aggregiert aus Evidence-Engrams
                                      # z.B. {participant_count: 1, time_window: "14:00-17:00",
                                      #       duration_avg: 45, mood: "productive"}

    centroid_qdrant_id: UUID          # Verweis auf Centroid-Vektor in Qdrant
                                      # (= numpy.mean über Evidence-Engram-Embeddings)

    evidence_engram_ids: [UUID; N]    # Top-N stärkste Belege als UUID-Array, Default N=5
                                      # → kein Cross-DB-Edge, nur Property
    evidence_count: Integer           # Gesamtzahl Evidence (auch jenseits Top-N)

    created_at: Timestamp
    last_reinforced_at: Timestamp     # für R5 (Death) — gemessen gegen NCR-Zyklen
    cycles_survived: Integer
    status: Enum[active, archived]
}
```

**Drei Repräsentationen, eine UUID als Klammer:**

| Repräsentation | Speicher | Zweck |
|---|---|---|
| Centroid | Qdrant (`payload.kind = "schema"`) | Pattern-Match in C2, Vektor-Search beim Recall |
| Description | Neo4j-Property | menschenlesbar, LLM-Input bei Recall-Synthese |
| Properties | Neo4j-Properties | strukturierte Abfragen, klare Pattern-Definition |

**Verbindung zu Engrams (Indexing Theory):**

Das Schema hat keinen Edge zu seinen Evidence-Engrams. Die Verbindung ist ein **UUID-Array als Property** (`evidence_engram_ids`), das beim Recall via SQL-Lookup auf die Buffer-Engrams aufgelöst wird. Das spart einen ganzen Knoten-Typ in Neo4j (keine `:EngramRef`-Stubs) und entspricht biologisch dem Indexing-Modell von Teyler & DiScenna (1986): Cortex zeigt nur auf einige starke Hippocampus-Engrams, restliche Verbindung läuft über content-addressable Aktivierung.

**Hyper-Schemas (durch C3 R3 entstehend):**

```
HyperSchema { ... }                   // Gleiche Felder wie Schema
(:Schema)-[:SPECIALIZES]->(:HyperSchema)
```

Hyper-Schemas verallgemeinern verwandte Schemas. Modellierung über Edge-Beziehung im Neo4j-Graphen.

### Bank als temporaler Taktgeber

Jeder Bank (Agent Session Bank, Agent Engram Dictionary, Shared Memory Bank — siehe Kapitel 15) hält einen monoton steigenden **Session-Counter**, der pro abgeschlossene Session um genau 1 erhöht wird. Dieser Counter ist der **temporale Taktgeber** für alle Engrams der Bank.

```
Bank {
    id: UUID
    name: String
    tier: Enum[agent_session, agent_dictionary, shared]
    session_counter: Integer          # Monoton steigend, ++1 pro abgeschlossene Session
    bank_size: Integer                # Anzahl aktiver Engrams (für bank_factor)
    created_at: Timestamp
}
```

**Warum dieses Design?**

Das Alter eines Engrams in Sessions (`sessions_alive`) wird **nicht pro Engram persistiert**, sondern zur Laufzeit abgeleitet:

```
sessions_alive = bank.session_counter - engram.created_at_session
```

Vorteile:
- **O(1) statt O(n) pro Session-Abschluss:** Am Ende jeder Session wird genau ein Wert (`bank.session_counter`) inkrementiert — kein Batch-Update über Millionen von Engrams
- **Keine Drift:** Der Counter ist die einzige Quelle der Wahrheit; jedes Engram erbt sein Alter implizit
- **Konsistent mit der Session-Semantik:** Sessions sind die Arbeitseinheit (nicht Wallclock-Zeit) — ein System, das eine Woche nicht genutzt wurde, altert seine Engrams nicht fälschlich
- **Timestamp bleibt für Audits:** `created_at` (Wallclock) existiert weiter für Debugging und Humans

Der Wallclock-Timestamp (`created_at`) und der Session-Snapshot (`created_at_session`) ergänzen sich: Timestamp für Menschen und Protokolle, Session-Snapshot für die Decay-Berechnung.

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

## 5. Thalamus Filter & Engram Lifecycle Scoring

### Funktion

Der Thalamus Filter ist das **initiale Bewertungssystem** für eingehende Episoden. Er bestimmt nicht ob eine Information gespeichert wird — **alles wird gespeichert**. Er bestimmt den **Geburtswert** (initiale Wichtigkeit) jedes Engrams. Dieser Geburtswert ist der Ausgangspunkt für den gesamten Lebenszyklus des Engrams.

### 5.1 Initiale Bewertung (Thalamus Score)

Jede eingehende Episode wird auf 4 Dimensionen bewertet. Alle Scores sind embedding-basiert, deterministisch, ohne LLM-Kosten.

**Novelty** — "Wie neu ist diese Information?"

```
novelty = 1.0 - max_similarity(content_embedding, existing_engrams)
```

Biologisches Vorbild: CA1 Mismatch Detection. Vergleicht den Content gegen alle existierenden Engrams in Qdrant (top-5 Similarity Search). Keine Treffer → 1.0 (alles ist neu). Hohe Ähnlichkeit zu bestehendem Wissen → nahe 0.0.

**Surprise** — "Wie unerwartet ist das Ergebnis?"

```
surprise = 1.0 - cosine(expectation_embedding, outcome_embedding)
```

Biologisches Vorbild: Noradrenalin-Ausschüttung. Misst die Abweichung zwischen Erwartung und tatsächlichem Outcome. Fallback: 0.5 (neutral) wenn Expectation oder Outcome fehlen.

**Task-Relevance** — "Wie relevant für den aktuellen Task?"

```
task_relevance = cosine(content_embedding, context_embedding)
```

Biologisches Vorbild: PFC Top-Down Attention. Misst die semantische Nähe zum aktuellen Arbeitskontext. Kontext-Hierarchie: Item-Level > Session-Level > None. Fallback: 0.5 wenn kein Kontext vorhanden.

**Emotional Valence** — "Wie emotional bedeutsam?"

```
emotional_valence = min(1.0, prediction_error × VALENCE_AMPLIFICATION)
```

Biologisches Vorbild: Amygdala-Modulation. Amplifiziert den Prediction Error (gleicher Input wie Surprise) um Faktor 1.5 (konfigurierbar). Macht kleine Überraschungen emotional unbedeutend, große Überraschungen emotional aufgeladen. Fallback: 0.3 (niedrig, errs on inclusion side).

**Anmerkung zur Korrelation von Surprise und Emotional Valence:** Beide basieren auf demselben Input (expectation vs outcome). Die Valence ist eine amplifizierte Version der Surprise — bei kleinen Prediction Errors (z.B. 0.4) ist Valence = 0.6, bei großen (0.8) ist Valence = 1.0 (gedeckelt). Die Amplification sorgt dafür, dass ab einem gewissen Überraschungsgrad die emotionale Bedeutsamkeit am Maximum ist.

### 5.2 Thalamus Overall Score

Gewichtete Kombination der 4 Dimensionen. Die Gewichtung ist **mode-abhängig** — der Session Mode bestimmt welche Dimension betont wird:

| Dimension | Exploration | Precision | Validation | Analogy |
|-----------|:-----------:|:---------:|:----------:|:-------:|
| Novelty | **0.40** | 0.15 | 0.20 | **0.30** |
| Surprise | 0.20 | 0.20 | **0.40** | 0.20 |
| Task-Relevance | 0.20 | **0.45** | 0.20 | **0.30** |
| Emotional Valence | 0.20 | 0.20 | 0.20 | 0.20 |

```
thalamus_overall = w_novelty × novelty + w_surprise × surprise
                 + w_relevance × task_relevance + w_valence × emotional_valence
```

Wertebereich: [0.0, 1.0]. Der Thalamus Overall Score wird als **Geburtswert** am Engram persistiert.

### 5.3 Natürlicher Zerfall (Decay)

Über die Lebenszeit eines Engrams verändert sich sein Wert basierend auf dem Verhältnis von **tatsächlichen Abrufen** zu **erwarteten Abrufen**:

```
decay = log(1 + access_count) / log(1 + sessions_alive × r)
```

Dabei ist:
- `access_count` — wie oft das Engram tatsächlich abgerufen wurde
- `sessions_alive` — Alter des Engrams in Sessions, **abgeleitet bei Bedarf** als `bank.session_counter - engram.created_at_session` (nicht pro Engram persistiert, siehe Kapitel 4)
- `r` — die individuelle Equilibrium Rate (erwartete Abrufhäufigkeit pro Zyklus)

**Verhalten:**
- `access_count > expected` → decay > 1.0 → **Verstärkung** (Information wird häufiger gebraucht als erwartet)
- `access_count = expected` → decay = 1.0 → **Stabil** (Information wird genau so oft gebraucht wie erwartet)
- `access_count < expected` → decay < 1.0 → **Zerfall** (Information wird seltener gebraucht als erwartet)

Das Alter der Information ist im Decay bereits eingebaut: mit jeder abgeschlossenen Session steigt der `session_counter` der Bank um 1, der Nenner wächst implizit für alle Engrams dieser Bank, und wenn `access_count` eines Engrams nicht mithält, sinkt sein Decay automatisch. Eine alte Information die nie abgerufen wird zerfällt schneller als eine junge — ohne dass ein separater Altersfaktor nötig wäre. Sessions sind der natürliche Taktgeber: jede Session ist eine Arbeitseinheit, und ein Engram beweist seinen Wert dadurch dass es über mehrere Arbeitssessions hinweg abgerufen wird.

#### Derivation statt Persistence (O(1) Session-Abschluss)

`sessions_alive` existiert nirgends als gespeichertes Feld auf dem Engram. Es wird immer dann berechnet, wenn es gebraucht wird — bei Recall-Scoring, bei C1-Neuberechnung, bei C2a-Checks:

```
sessions_alive = bank.session_counter - engram.created_at_session
```

Wenn eine Session abgeschlossen wird, passiert genau ein Schreibvorgang: `bank.session_counter += 1`. Alle Engrams der Bank "altern" dadurch implizit um 1 Session — ohne dass eine einzige Engram-Zeile angefasst werden muss. Bei einer Bank mit 50.000 Engrams ersetzt das eine UPDATE auf einer Tabelle die UPDATE-Flut auf allen Engrams.

Biologisches Vorbild: Im Gehirn altert keine einzelne Synapse aktiv — die Zeit vergeht einfach, und der Zerfall ergibt sich aus dem Verhältnis von Zeit zu Reaktivierung.

#### Equilibrium Rate r — individuell pro Engram

Die Equilibrium Rate `r` bestimmt wie viele Abrufe pro Zyklus erwartet werden, damit ein Engram stabil bleibt. Sie setzt sich zusammen aus einer **mode-abhängigen Baseline** und einer **individuellen Modulation durch die Thalamus-Dimensionen**:

```
r = r_base(mode) × demand / protection × bank_factor
```

Wobei:

```
demand      = 1 + α × task_relevance
protection  = 1 + β × (novelty + surprise + emotional_valence) / 3
bank_factor = log(1 + reference_size) / log(1 + bank_size)
```

**Stellräder:**
- `α` und `β` steuern die Stärke des Dimensionseinflusses (Default: α = 0.5, β = 0.5)
- `reference_size` ist die Baseline-Bankgröße auf die das System kalibriert ist (Default: 1000)

**Warum diese Struktur:**
- `demand` ist immer ≥ 1.0 → schiebt r nach oben (strenger)
- `protection` ist immer ≥ 1.0 → schiebt r nach unten (milder) durch Division
- `bank_factor` normalisiert für die Bankgröße (kleine Bank → höheres r, große Bank → niedrigeres r)
- r bleibt immer positiv, kann nie null oder negativ werden
- Bei neutralen Scores und reference_size Bank heben sich alle Faktoren auf → r ≈ r_base

#### Bank-Size Normalisierung

In einer kleinen Bank (50 Engrams) hat jedes Engram eine hohe Wahrscheinlichkeit bei jedem Recall in den Ergebnissen zu landen — `access_count` steigt dadurch natürlich schneller. In einer großen Bank (50.000 Engrams) ist die Konkurrenz höher und `access_count` steigt langsamer. Ohne Normalisierung würden Engrams in kleinen Banken unfair schnell promoviert und in großen Banken unfair archiviert.

Der `bank_factor` gleicht das aus:

| Bank-Größe | bank_factor | Effekt auf r |
|:----------:|:-----------:|-------------|
| 50 | **1.76** | r steigt → höhere Erwartung, gleicht inflationierte Abrufe aus |
| 200 | **1.30** | r steigt leicht |
| 1.000 | **1.00** | Baseline — kein Effekt |
| 5.000 | **0.81** | r sinkt → niedrigere Erwartung, kompensiert Wettbewerb |
| 50.000 | **0.64** | r sinkt deutlich → faire Chance trotz großer Konkurrenz |

**Dimensionseinfluss auf r:**

| Dimension | Hoher Wert → r | Effekt | Biologisches Vorbild |
|-----------|:--------------:|--------|---------------------|
| Task-Relevance | **steigt** | Strenger — muss sich durch Nutzung beweisen | PFC: exekutive Kontrolle fordert Rechenschaft |
| Novelty | **sinkt** | Milder — braucht Zeit, potenziell wertvoll | CA1: Mismatch Detection schützt das Unbekannte |
| Surprise | **sinkt** | Milder — Lernsignal, korrigiert Weltmodell | Noradrenalin: Plastizitätssignal öffnet Lernfenster |
| Emotional Valence | **sinkt** | Milder — biologisch geschützt | Amygdala: emotionales Tagging stärkt Konsolidierung |

Task-Relevance ist die einzige "fordernde" Dimension (PFC = rationale Kontrolle), die anderen drei sind "schützende" Dimensionen (instinktive Bewahrung).

#### Mode-abhängige Baseline r_base

| Mode | r_base | Bedeutung |
|------|:------:|-----------|
| Precision | 0.8 | Streng — nur intensiv genutzte Info überlebt |
| Validation | 0.6 | Moderat-streng |
| Analogy | 0.4 | Moderat-mild |
| Exploration | 0.3 | Mild — Erinnerungen bleiben leichter stabil |

#### Berechnungsbeispiele (α = 0.5, β = 0.5, r_base = 0.5)

**Task-relevanter Fakt** (task_rel=0.8, novelty=0.2, surprise=0.2, valence=0.3):
```
demand     = 1 + 0.5 × 0.8 = 1.40
protection = 1 + 0.5 × (0.2+0.2+0.3)/3 = 1.12
r = 0.5 × 1.40 / 1.12 = 0.63 → strenger als Baseline
```
→ "Du bist relevant — beweis es durch Nutzung."

**Überraschende neue Erkenntnis** (task_rel=0.3, novelty=0.9, surprise=0.8, valence=0.7):
```
demand     = 1 + 0.5 × 0.3 = 1.15
protection = 1 + 0.5 × (0.9+0.8+0.7)/3 = 1.40
r = 0.5 × 1.15 / 1.40 = 0.41 → milder als Baseline
```
→ "Du bist überraschend und neu — du bekommst mehr Zeit."

**Routinefakt** (alles niedrig: ~0.2):
```
demand     = 1 + 0.5 × 0.2 = 1.10
protection = 1 + 0.5 × 0.2 = 1.10
r = 0.5 × 1.10 / 1.10 = 0.50 → genau Baseline
```
→ "Nichts Besonderes — normaler Zerfall."

**Emotional aufgeladene Erfahrung** (task_rel=0.4, novelty=0.5, surprise=0.9, valence=0.9):
```
demand     = 1 + 0.5 × 0.4 = 1.20
protection = 1 + 0.5 × (0.5+0.9+0.9)/3 = 1.38
r = 0.5 × 1.20 / 1.38 = 0.43 → deutlich milder
```
→ "Emotional bedeutsam — geschützt."

#### Decay-Beispiele (r = 0.5)

| Abrufe | Zyklen | Erwartet | Decay-Faktor | Effekt |
|--------|--------|----------|:------------:|--------|
| 5 | 5 | 2.5 | **1.43** | Verstärkung |
| 50 | 100 | 50 | **1.00** | Stabil |
| 100 | 100 | 50 | **1.17** | Verstärkung |
| 5 | 100 | 50 | **0.46** | Verfall |
| 1 | 100 | 50 | **0.18** | Starker Verfall |

Biologisches Vorbild: Synaptische Plastizität. Häufige Reaktivierung (LTP) stärkt die Synapse, fehlende Reaktivierung schwächt sie. Der logarithmische Verlauf bildet die Ebbinghaus-Vergessenskurve ab: anfangs schneller Zerfall, dann abflachend.

### 5.4 Composite Score

Der Composite Score ist das Produkt aus initialem Thalamus-Wert und dem dynamischen Decay-Faktor:

```
composite = thalamus_overall × decay
```

**Beispiele:**
- Thalamus 0.9 × Decay 1.2 (häufig abgerufen) = **1.08** → über Geburtswert verstärkt
- Thalamus 0.9 × Decay 0.3 (nie abgerufen) = **0.27** → auch wichtige Infos verfallen ohne Nutzung
- Thalamus 0.3 × Decay 1.5 (ständig abgerufen) = **0.45** → häufige Nutzung kompensiert niedrigen Geburtswert teilweise

### Natürlicher Schutz durch hohen Geburtswert

Ein wesentlicher Effekt der multiplikativen Formel: **initial wichtige Informationen erreichen den Archive-Threshold auf natürliche Weise viel später**. Der Decay-Faktor muss umso weiter sinken, je höher der Thalamus-Score war, bevor der Composite unter den Archive-Threshold fällt.

| Thalamus-Score | Decay nötig für Archive (< 0.08) | Bedeutung |
|:--------------:|:--------------------------------:|-----------|
| 0.9 | < 0.089 | Extrem langer Zerfall nötig — Information überlebt viele Zyklen ohne Abruf |
| 0.6 | < 0.133 | Langer Zerfall nötig |
| 0.3 | < 0.267 | Moderater Zerfall reicht bereits |
| 0.1 | < 0.800 | Schnelle Archivierung wenn nicht bald abgerufen |

Das spiegelt das biologische Verhalten wider: eine emotional aufgeladene oder überraschende Erfahrung (hoher Thalamus-Score) bleibt auch ohne aktives Erinnern lange im Gedächtnis. Eine banale, erwartete Information (niedriger Thalamus-Score) verblasst schnell wenn sie nicht durch Wiederholung gestärkt wird.

### 5.5 Thresholds — Promotion und Archivierung

Der Composite Score entscheidet über das Schicksal jedes Engrams. Die Schwellenwerte sind **tag-abhängig**.

#### Promote-Threshold (Working Memory → Buffer)

Facts sind "billige" Informationseinheiten — viele kommen rein, die meisten sind Routine. Sie brauchen einen **höheren Schwellenwert** um zu beweisen, dass sie langfristig relevant sind. Experiences und Opinions sind Erkenntnisse die aus der Arbeit entstehen — sie haben bereits einen Verarbeitungsschritt hinter sich und sind damit eher Kandidaten für den Neocortex.

| Tag-Kategorie | Promote-Threshold | Begründung |
|---------------|:-----------------:|------------|
| fact | 0.7 | Muss sich durch hohen Thalamus-Score ODER häufige Nutzung beweisen |
| experience | 0.4 | Erfahrungen sind bereits verarbeitetes Wissen |
| opinion | 0.4 | Meinungen/Schlussfolgerungen sind Syntheseleistungen |

#### Archive-Threshold

Engrams deren Composite Score unter den Archive-Threshold fällt, werden archiviert (Status → `archived`). Sie sind nicht gelöscht — sie können bei gezielter Suche noch gefunden werden — aber sie nehmen nicht mehr aktiv am Retrieval teil.

| Layer | Archive-Threshold | Begründung |
|-------|:-----------------:|------------|
| Working Memory | 0.08 | Kurzzeitspeicher räumt aggressiver auf |
| Buffer | 0.05 | Buffer-Engrams haben sich schon teilweise bewiesen |

#### Hard Gates (zusätzlich zu Thresholds)

Vor der Promotion werden zwei Hard Gates geprüft. Auch wenn der Composite Score den Promote-Threshold erreicht, muss das Engram beide Gates passieren:

1. **`access_count ≥ min_access`** (STC-Gate) — Ohne Rehearsal keine Konsolidierung. Ein Engram das nie abgerufen wurde kann nicht promoten, egal wie hoch sein Thalamus-Score war. Der Schwellenwert ist **bankgrößen-normalisiert**:

```
min_access = base_min_access × bank_factor
```

Mit `base_min_access = 5` (Default) und dem gleichen `bank_factor` wie beim Decay:

| Bank-Größe | bank_factor | min_access | Bedeutung |
|:----------:|:-----------:|:----------:|-----------|
| 50 | 1.76 | **9** | Kleine Bank — muss öfter abgerufen werden |
| 200 | 1.30 | **7** | |
| 1.000 | 1.00 | **5** | Baseline |
| 5.000 | 0.81 | **4** | |
| 50.000 | 0.64 | **3** | Große Bank — weniger Abrufe nötig |

Biologisches Vorbild: Synaptic Tagging & Capture — das Tag allein (initiale Markierung) reicht nicht, es braucht Capture (wiederholte Aktivierung). In einem dichten neuronalen Netzwerk (große Bank) reichen weniger Aktivierungen um eine Verbindung zu stabilisieren, weil jede Aktivierung gegen mehr Konkurrenz gewonnen hat.

2. **`novelty ≥ 0.2`** (CA1-Gate) — Bereits bekannte Information wird nicht re-konsolidiert. Verhindert dass redundante Engrams den Buffer füllen. Biologisches Vorbild: CA1 Mismatch Detection — nur Information die sich ausreichend von bestehendem Wissen unterscheidet wird langfristig gespeichert.

### 5.6 Kontinuierliche Neubewertung

Der Composite Score wird **in jedem Layer kontinuierlich neu berechnet** — nicht nur beim Übergang zwischen Layers. Das bedeutet:

- **Working Memory:** Engrams werden bei jeder C1-Phase (Session-Ende) neu bewertet. Composite kann steigen (durch Abrufe) oder sinken (durch fehlende Abrufe).
- **Buffer:** Engrams werden bei jeder C2-Phase (alle 24h) neu bewertet. Ein Engram das im Buffer landet, ist nicht sicher — wenn es dort nicht weiter abgerufen wird, kann sein Composite unter den Archive-Threshold fallen und es **altert aus dem Buffer heraus** bevor es den Neocortex erreicht.
- **Archived:** Archivierte Engrams sind nicht tot. Wenn ein archiviertes Engram bei einem gezielten Recall getroffen wird, steigt sein `access_count`. Bei der nächsten Neubewertung kann sein Composite Score über den Archive-Threshold steigen und es **kehrt in den Working Memory oder sogar direkt in den Buffer zurück** (wenn der Promote-Threshold erreicht wird und die Hard Gates erfüllt sind).
- **Neocortex:** Einmal im Neocortex angekommen, ist ein Engram **stabil**. Es wird nicht mehr durch den Composite Score bedroht sondern in den späteren Consolidation-Phasen (C3: Schema Compression, C4: Shared Bank Promotion) betrachtet und umstrukturiert. Der Neocortex ist der sichere Hafen.

Biologisches Vorbild: Erinnerungen die in den Langzeitspeicher (Neocortex) konsolidiert wurden, sind resistent gegen normales Vergessen. Aber sie können durch Reconsolidation (Kapitel 10) und Schema Compression (Kapitel 13) modifiziert und abstrahiert werden. Umgekehrt können "vergessene" Erinnerungen durch einen starken Abruf-Trigger reaktiviert werden — das Phänomen des "plötzlichen Erinnerns" an etwas das man längst vergessen glaubte.

### 5.7 Engram Lifecycle — Zusammenfassung

```
Episode kommt rein
       │
       ▼
┌─────────────────────────┐
│  Thalamus Score          │  4 Dimensionen → mode-gewichteter Overall
│  (Geburtswert)           │  Alles wird gespeichert, Score bestimmt
│                          │  initiale Wichtigkeit
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Working Memory          │  Engram lebt, wird (oder wird nicht) abgerufen
│  (PostgreSQL)            │  access_count steigt bei jedem Recall
│                          │  bank.session_counter ++1 pro Session
│                          │  → sessions_alive wächst derived (O(1))
│                          │  Composite wird bei C1 neu berechnet
└───────────┬─────────────┘
            │                          ▲
            │ Composite < 0.08 ?       │ Recall reaktiviert →
            │──────────┐               │ Composite steigt wieder
            │          ▼               │
            │  ┌───────────────┐       │
            │  │  Archived     │───────┘
            │  │  (inactive)   │  Nicht gelöscht, bei gezielter
            │  └───────┬───────┘  Suche noch findbar
            │          ▲
            │          │ Composite < 0.05 ?
            │          │
            │ Composite ≥ Promote-Threshold (tag-abhängig)
            │ UND access_count ≥ min_access
            │ UND novelty ≥ 0.2 ?
            │
            ▼
┌─────────────────────────┐
│  Buffer                  │  Engram ist Kandidat für Neocortex
│  (Neo4j layer='buffer')  │  Composite wird bei C2 neu berechnet
│                          │  Kann wieder herausfallen → archived
└───────────┬─────────────┘
            │
            │ Composite ≥ Promote-Threshold
            │ UND ncr_cycles_survived ≥ 2 ?
            │
            ▼
┌─────────────────────────┐
│  Neocortex               │  ★ SICHERER HAFEN ★
│  (Neo4j layer='neocortex')│  Kein Decay-basiertes Archivieren mehr
│                          │  Wird in C3 (Schema Compression) und
│                          │  C4 (Shared Bank) umstrukturiert
└─────────────────────────┘
```

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
| **Strength Pre-Filter** | ≥ 0.5 | ≥ 0.1 | ≥ 0.3 | ≥ 0.3 |
| **Thalamus Boost** | Task-Relevance | Novelty | — | Surprise |
| **Weak Links** | Ignoriert | Folgt | Bevorzugt | Ignoriert |
| **Traversal Depth** | Flach | Tief | Mittel | Mittel |
| **Construction** | Konservativ | Kreativ | Cross-Domain | Evidenz-basiert |
| **Reconsolidation** | Minimal | Moderat | Schema-Update | Aggressiv |

### Mode als Transient Signal

Der Mode wird nicht gespeichert — er fließt als Parameter durch alle Storage-Tiers:
- **PostgreSQL:** Mode → SQL-Thresholds
- **Neo4j + Qdrant:** Mode → EngramRetriever-Patterns + Filter

---

## 8. Search & Retrieval (erweitert)

### Erweiterungen gegenüber Hindsight (S1-S7)

**S1 — Fact-Type Filter ablösen:**
- Aktuell: `AND fact_type = $3` → separate Queries pro Type (12 Queries)
- Neu: Tags-basierte Filterung, ein Query über alle Types
- Hybrid-Architektur löst das Problem inhärent: Qdrant liefert Treffer type-agnostisch

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
- **Entscheidung:** Neuer `HybridRetriever` orchestriert PostgreSQL (Engram-Metadaten) + Qdrant (Vektor-Such über Engram-Embeddings + Schema-Centroids) + Neo4j (Schema-Schema-Traversierung).
- Pro Bank gibt es einen passenden Retriever. Session Layer routet je nach Bank-Typ zum richtigen Retriever.

**S7 — Schema- und Engram-Mischtreffer (neu):**

Mit der CLS-konformen Trennung (Buffer = Engrams, Neocortex = Schemas) werden bei jedem Recall **beide Räume gleichzeitig** durchsucht:

```
Query → Embedding
   ↓
Qdrant Vector-Search (gemischte Collection)
   ├── Engram-Treffer (payload.kind = "engram")
   └── Schema-Treffer (payload.kind = "schema")
   ↓
Pro Schema-Treffer: optional Top-N Evidence-Engrams aus
PostgreSQL nachholen (via evidence_engram_ids)
   ↓
Reflect-Pipeline (Kapitel 10) synthetisiert Antwort aus
Mischresultat (Schema-Allgemeinheit + Engram-Spezifik)
```

**Beispiel:** Query "erzähl mir was über Coffee-Meetings"
- Qdrant findet Schema-Treffer `coffee_meeting_1on1` (hohe Cosine-Similarity zum Centroid)
- Qdrant findet Engram-Treffer "Coffee mit Christian, 15.4., 14:30" (separate semantische Nähe)
- Schema liefert die Allgemeinheit: "1:1, Nachmittag, ~45min, produktiv"
- Top-N Evidence-Engrams + freistehende Engram-Treffer liefern konkrete Beispiele
- LLM-Synthese kombiniert beides: "Du hast typischerweise … z.B. mit Christian am 15.4., mit Anna am 22.4. …"

Damit bekommt der User immer das prototypische Wissen (Schema) **und** konkrete Episoden (Engrams), wo das nützlich ist — ohne dass wir am Datenmodell biegen müssen.

### Scoring-Formel (erweitert)

Aktuell Hindsight: `60% CE + 20% RRF + 10% Temporal + 10% Recency`

Ziel: `w1×CE + w2×RRF + w3×Temporal + w4×Recency(strength-moduliert) + w5×Engram_Strength + w6×Thalamus_Weighted`

Gewichte mode-abhängig konfiguriert. Schema-Treffer werden gegenüber Engram-Treffern in Modus `Precision` höher gewichtet (Allgemeinheit bevorzugt), in Modus `Exploration` umgekehrt (Spezifik/Episoden bevorzugt).

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

### Architektur-Überblick

Die Consolidation Pipeline besteht aus **drei unabhängigen Phasen**, die jeweils eigene Trigger und Cooldowns haben. Jede Phase ist ein eigenständiger Task, der über einen API-Endpoint (`POST /v1/default/banks/{bank_id}/ncr/trigger?phase=...`) manuell oder über den `NCRScheduler` automatisch ausgelöst werden kann. Advisory Locks (`pg_try_advisory_lock`) verhindern parallele Runs auf derselben Bank.

```
Working Memory (PostgreSQL)
    │
    │ C1: Composite + Hard Gates → Promote oder Archive
    ▼
Engram Buffer (PostgreSQL + Qdrant) — Hippocampus-Äquivalent
    │
    │ C2: Pattern Recognition → Schema-Erzeugung oder -Reinforcement
    ▼
Neocortex (Neo4j + Qdrant) — ausschließlich Schemas / Patterns
    │
    │ C3: Game-of-Life über Schemas (Restrukturierung)
    ▼
Verfeinerter Schema-Graph
```

**Architektonische Trennung der Speicher:**

- **Buffer = Hippocampus.** Hier leben **alle wichtigen Engrams** (Episoden, Experiences, Opinions). Sie altern via Decay aus dem aktiven Set heraus, werden aber nicht aktiv von Schemas weg-konsolidiert. Bio-Vorbild: Episodengedächtnis bleibt episodisch, solange es relevant ist (Multiple Trace Theory; Nadel & Moscovitch 1997).
- **Neocortex = Schema-Layer.** Enthält **ausschließlich Schemas/Patterns**, keine individuellen Engrams. Bio-Vorbild: klassische CLS-Theorie (McClelland/McNaughton/O'Reilly 1995) — Cortex extrahiert statistische Regelmäßigkeiten und behält keine Episoden.
- **Schemas verweisen indexbasiert auf Engrams.** Top-N `evidence_engram_ids` als UUID-Property am Schema-Knoten — kein Cross-DB-Edge, keine Stub-Knoten in Neo4j. Restliche Verbindung läuft implizit über Embedding-Similarity beim Recall (Indexing Theory; Teyler & DiScenna 1986).

### Phasen-Übersicht

| Phase | Funktion | Trigger | Intervall | Cooldown |
|-------|----------|---------|-----------|----------|
| **C1** | Working Memory → Buffer | Session-Ende | bei jeder Session | keiner |
| **C2** | Pattern Recognition + Schema-Erzeugung/Reinforcement, plus Decay-Re-Evaluation der Buffer-Engrams | NCRScheduler | 24h | 1h |
| **C3** | Schema-Graph Restrukturierung (Game-of-Life über Schemas) | NCRScheduler | 168h (7 Tage) | 6h |

**Was nicht zur Pipeline gehört:**

- **Reconsolidation** (Modifikation bestehender Engrams bei Recall) ist Recall-getrieben und in Kapitel 10 dokumentiert.
- **Cross-Bank Promotion** (Schema-Sharing über Agenten hinweg) ist in Kapitel 15 (Multi-Bank Architecture) beschrieben.

### 12.1 Phase C1 — Working Memory → Buffer

**Trigger:** Automatisch bei Session-Ende. Kein Cooldown — wird bei jeder Session ausgeführt.

**Bio-Vorbild:** Sharp-Wave Ripples in quiet wakefulness (Foster & Wilson 2006) — direkt nach einer aktiven Phase werden die wichtigsten Erfahrungen selektiv ins Hippocampus-System (Buffer) übertragen.

**Composite Score** entscheidet über Promotion (siehe Kapitel 5.3–5.4):

```
bank_factor = log(1 + reference_size) / log(1 + bank_size)
r           = r_base(mode) × (1 + α × task_relevance)
                            / (1 + β × (novelty + surprise + emotional_valence) / 3)
                            × bank_factor
decay       = log(1 + access_count) / log(1 + sessions_alive × r)
composite   = thalamus_overall × decay
```

**Entscheidungslogik pro Engram:**

1. **Archive-Check:** Composite < 0.08 → Status `archived`, Engram nimmt nicht mehr am aktiven Retrieval teil
2. **Hard Gate 1 (STC):** `access_count ≥ min_access` — ohne Rehearsal keine Konsolidierung (min_access = 5 × bank_factor, bankgrößen-normalisiert)
3. **Hard Gate 2 (CA1):** `novelty ≥ 0.2` — bekannte Information wird nicht re-konsolidiert
4. **Promote-Check:** Composite ≥ Promote-Threshold (tag-abhängig):

| Tag-Kategorie | Promote-Threshold | Begründung |
|---------------|:-----------------:|------------|
| fact | 0.7 | Muss sich durch hohen Thalamus-Score ODER häufige Nutzung beweisen |
| experience | 0.4 | Erfahrungen sind bereits verarbeitetes Wissen |
| opinion | 0.4 | Meinungen/Schlussfolgerungen sind Syntheseleistungen |

5. **Sonst:** Engram bleibt im Working Memory, wird beim nächsten C1-Run erneut geprüft

Promotion ist ein **Property Update** (`layer='working'` → `layer='buffer'`), keine physische Datenkopie. Im Buffer leben Engrams mit ihrem vollen Inhalt (Content + Embedding + Metadaten + Tags).

### 12.2 Phase C2 — Pattern Recognition & Schema-Erzeugung

**Trigger:** NCRScheduler (alle 24h) + Manual API. Cooldown: 1h.

**Bio-Vorbild:** SWS-Replay (Slow-Wave Sleep) — Hippocampus-Cortex-Replay komprimiert nicht 1:1, sondern extrahiert "gist" und Statistik (Tse et al. 2007). Schema-Konsolidierung läuft genau in dieser Phase.

**Ziel:** Aus mehreren ähnlichen Engrams im Buffer wird ein **Schema** im Neocortex erzeugt — oder ein bestehendes Schema verstärkt. Die Engrams selbst bleiben im Buffer.

#### Pipeline (deterministisch + ein schmaler LLM-Streifen)

**Schritt 1 — Cluster-Detection (rein berechnet)**

Auf allen aktiven Buffer-Engrams läuft ein Density-Clustering (HDBSCAN) im Embedding-Raum:

```
clusters = hdbscan.fit(buffer_engram_embeddings, min_cluster_size=3)
```

**Schritt 2 — Mindestanforderungen (Game-of-Life R1 + R2)**

Pro Cluster-Kandidat:

- **R1 (Birth):** ≥ 3 Engrams mit paarweiser Cosine ≥ 0.75
- **R2 (Maturation):** Cluster muss ≥ 2 C2-Zyklen überlebt haben (Persistenz-Check via Cluster-Fingerprint), bevor er Schema-Kandidat wird

Einmal-Cluster werden ignoriert — nur wiederkehrende Patterns werden zu Schemas. Damit wird Rauschen herausgefiltert.

**Schritt 3 — Schema-Fingerprint-Match (rein berechnet)**

Vor der Erzeugung wird der Cluster-Centroid gegen alle existierenden Schema-Centroids in Qdrant gemappt:

- **Match (Cosine ≥ 0.85):** Bestehendes Schema wird verstärkt (siehe Schritt 7), kein neues Schema erzeugt
- **Kein Match:** weiter zu Schritt 4 (neue Schema-Erzeugung)

**Schritt 4 — Property-Aggregation (rein berechnet)**

Aus den Tags der Cluster-Engrams werden statistisch die Schema-Properties extrahiert:

```
participant_count = mode([engram.participants for engram in cluster])
duration_avg      = mean([engram.duration for engram in cluster])
time_window       = (min_time, max_time) over cluster
mood              = mode([engram.mood for engram in cluster])
```

Voraussetzung: Engrams sind beim Retain ordentlich getaggt (Thalamus + Tag-Extraktion in Kapitel 6). Damit ist C2 selbst LLM-frei in der Property-Extraktion.

**Schritt 5 — Centroid berechnen (rein berechnet)**

```
schema_centroid = numpy.mean([engram.embedding for engram in cluster])
```

Der Centroid ist das **Aktivierungsmuster** für dieses Pattern. Er hat selbst keinen lesbaren Inhalt, dient aber als Adresse im Vektor-Raum für Pattern-Matching und Recall.

**Schritt 6 — Description-Generation (kleiner LLM-Call)**

Hier — und nur hier — braucht C2 ein LLM. Es ist ein reiner Data-to-Text-Task: aus strukturierten Properties wird ein prägnanter Klartext-Satz.

```
Pipeline-Step: consolidation.schema_description
Tier:          SMALL
Eingabe:       Properties + Evidence-Count
Ausgabe:       1 Satz Klartext
```

Ein lokales Modell (z.B. `qwen2.5:14b` via Ollama) reicht völlig — Details siehe Kapitel 16. Bei Ausfall fällt die Pipeline auf eine **Template-Description** zurück:

```
"{dominant_activity} mit {participant_count} Person(en),
 {time_window}, {mood}, ~{duration_avg}min"
```

**Schritt 7 — Persistierung (cross-DB)**

Der Schema-Knoten in Neo4j hat folgende drei parallele Repräsentationen seines Inhalts:

| Repräsentation | Wo | Wofür |
|---|---|---|
| **Centroid** (Vektor) | Qdrant | Pattern-Matching beim nächsten C2-Lauf, Vektor-Search beim Recall |
| **Description** (Klartext) | Neo4j-Property | menschenlesbarer Inhalt, LLM-Input bei Recall-Synthese |
| **Properties** (key-value) | Neo4j-Properties | strukturierte Abfragen, klare Pattern-Definition |

```cypher
(:Schema {
    id: UUID,
    description: String,                  // Klartext, vom LLM oder Template
    properties: Map[String, Any],         // statistisch aggregiert
    evidence_engram_ids: [UUID; N],       // Top-N stärkste Belege, Default N=5
    evidence_count: Integer,              // Gesamtzahl Evidence (auch jenseits Top-N)
    centroid_qdrant_id: UUID,
    created_at: Timestamp,
    last_reinforced_at: Timestamp,
    cycles_survived: Integer
})
```

In Qdrant liegen Schema-Centroids und Engram-Embeddings in derselben Collection, unterscheidbar via `payload.kind ∈ {"schema", "engram"}`. Damit ist beim Recall **eine** Vektor-Search beide Räume durchsucht (siehe Kapitel 8).

#### Schema-Reinforcement (Game-of-Life R4)

Bei einem Schema-Match in Schritt 3:

- `evidence_count += cluster_size`
- Top-N `evidence_engram_ids`: Wenn ein Cluster-Engram stärker ist als der schwächste Top-N-Eintrag → ID ersetzen
- Centroid neu mitteln (laufender Mittelwert über alle bisherigen Evidence-Embeddings)
- Properties verfeinern (neue Tags werden eingearbeitet — der dominante Wert eines Property-Felds bleibt, kann sich aber bei systematischer Verschiebung anpassen)
- `last_reinforced_at` aktualisieren

R4 läuft **zusätzlich** inkrementell beim Retain (siehe Kapitel 6) — jeder neue Engram wird gegen existierende Schema-Fingerprints geprüft. Das ist die Bio-Entsprechung zu schnellem Schema-Update bei kompatibler Information (Tse et al. 2007: schema-konsistente Erinnerungen konsolidieren in Stunden, nicht Wochen).

#### Decay-Re-Evaluation für Buffer-Engrams (Bestandteil von C2)

Da C2 ohnehin alle aktiven Buffer-Engrams iteriert, übernimmt es zusätzlich die periodische Decay-Re-Evaluation:

1. Ein einziger UPDATE auf Bank-Ebene: `bank.session_counter += 1` — alle Engrams der Bank altern implizit um 1 Session (kein Massenupdate auf Engram-Zeilen)
2. Composite-Score aller Buffer-Engrams wird neu berechnet (`sessions_alive` derived aus `bank.session_counter - engram.created_at_session`)
3. Engrams mit Composite < 0.05 → Status `archived`

Damit wird die ehemalige separate C2a-Phase eingespart. Die Decay-Logik bleibt formelidentisch zu Kapitel 5.4. Häufig abgerufene Engrams halten ihren Wert; vergessene Engrams altern allein durch Zeit aus dem aktiven Set heraus — ohne dass C2 sie aktiv "rauskickt".

#### Was C2 nicht tut

- **Keine LLM-Reasoning-Pipeline.** Pattern Recognition ist deterministisch; das LLM macht nur die Versprachlichung in Schritt 6.
- **Keine Cross-DB-Edges** zwischen Schema und Engram. Der Verweis ist die UUID; Auflösung erfolgt beim Recall via SQL-Lookup auf die Top-N IDs.
- **Keine Engram-Migration in den Cortex.** Engrams bleiben im Buffer, der Cortex enthält nur Schemas.

### 12.3 Phase C3 — Schema-Graph Restrukturierung

**Trigger:** NCRScheduler (alle 7 Tage) + Manual API. Cooldown: 6h.

**Bio-Vorbild:** REM Sleep — kreative Rekombination und Abstraktion. Schemas werden untereinander verglichen, verlinkt, fusioniert oder verworfen.

**Ziel:** Der Schema-Graph im Neocortex wird aufgeräumt und reichhaltiger gemacht. Im Gegensatz zu C2 — das Schemas *erzeugt* — operiert C3 **ausschließlich über bestehende Schemas**.

#### Game-of-Life-Regeln auf Schema-Ebene

**R3 — Abstraction / Specialization**

Zwei Schemas, die semantisch verwandt sind (Centroid-Cosine ≥ 0.7) und sich in mindestens einem Property-Feld systematisch unterscheiden, können zu einem **Hyper-Schema** subsumiert werden. Beispiel:

- `coffee_meeting_1on1_afternoon` und `coffee_meeting_1on1_morning` → Hyper-Schema `coffee_meeting_1on1`
- Modellierung im Graph: `(:Schema)-[:SPECIALIZES]->(:HyperSchema)`

**R5 — Competition / Death**

Schemas, bei denen
- `cycles_since_last_reinforced > K` (Default K = 4 → ca. 4 Wochen ohne neue Evidence) **und**
- `evidence_count < threshold` (z.B. 5)

werden als `archived` markiert. Sie bleiben im Graph für historische Recalls verfügbar, werden aber nicht mehr als aktive Schemas behandelt. Bio-Vorbild: synaptic homeostasis im Cortex.

R3 und R5 laufen rein berechnet — kein LLM-Call. Optional kann R3 ein LLM für die Hyper-Schema-Description nutzen (gleicher Pipeline-Step `consolidation.schema_description`).

#### Was C3 nicht tut

- C3 fasst **keine Engrams an**. Engrams im Buffer bleiben unberührt.
- C3 erzeugt keine neuen Schemas aus Daten — das ist C2-Aufgabe.
- C3 verändert keine Centroid-Embeddings einzelner Schemas; es bildet höchstens neue Hyper-Schemas mit eigenem Centroid.

### 12.4 Reconsolidation (Verweis)

Reconsolidation modifiziert bestehende Engrams **bei Recall** und ist kein Pipeline-Schritt. Trigger: semantische Similarity ≥ 0.6 zwischen Recall-Ergebnis und bestehendem Engram. Detaillierte Logik in Kapitel 10 (Reflect & Reconsolidation).

### 12.5 Cross-Bank Promotion (Verweis)

Die Promotion von Neocortex-Schemas in eine Shared Memory Bank (Multi-Agent) ist nicht Teil der Consolidation Pipeline und in Kapitel 15 (Multi-Bank Architecture) dokumentiert.

### 12.6 Fehler-Isolation und Reporting

Jede Phase ist in `try/except` gewrappt. Ein Fehler in einer Phase blockiert **nicht** die nachfolgenden Phasen. Alle Ergebnisse werden in der `ncr_runs`-Tabelle persistiert (JSONB pro Phase) und sind über `GET /v1/default/banks/{bank_id}/ncr/history` abrufbar.

### 12.7 Zuordnung Konzept → Code

| Phase | Operationen | Code-Datei |
|-------|-------------|------------|
| **C1** | Composite + Hard Gates + Promotion `working → buffer` | `consolidation1.py` |
| **C2** | Cluster-Detection, Schema-Erzeugung, Reinforcement, Decay-Re-Evaluation Buffer-Engrams | `consolidation2.py`, `pattern_clustering.py`, `schema_writer.py` |
| **C3** | Game-of-Life R3 + R5 über Schemas | `consolidation3.py`, `schema_graph_restructure.py` |
| **R4 (incremental)** | Schema-Fit-Check beim Retain | `retain/schema_fit.py` |

**LLM-Calls in der Pipeline** (siehe Kapitel 16 — LLM Routing):

| Subtask | Tier | Beschreibung |
|---------|------|--------------|
| `consolidation.schema_description` | SMALL | Klartext-Beschreibung aus aggregierten Properties (Data-to-Text) |

Alle übrigen Schritte in C1, C2 und C3 sind deterministisch. Composite-Berechnung, Cluster-Detection, Property-Aggregation, Centroid und Game-of-Life-Regeln laufen ohne LLM.

---

## 13. Schema Emergence

### Design-Philosophie: Bottom-up statt Top-down

Schemas sind **keine vordefinierten Kategorien**. Sie entstehen emergent aus den Daten durch einfache lokale Regeln — analog zu Conway's Game of Life. Im Brain-inspired Modell entspricht das der CLS-Theorie: der Cortex baut Schemas aus statistischer Regelmäßigkeit über viele Episoden, nicht aus einer von außen vorgegebenen Ontologie.

In MemoryManager liegen **Engrams im Buffer** (PostgreSQL + Qdrant) und **Schemas im Neocortex** (Neo4j + Qdrant). Schema Emergence ist der Prozess, der über Buffer-Engrams iteriert und daraus Schemas im Neocortex destilliert oder bestehende verstärkt.

### 5 Game-of-Life Regeln

**R1 — Clustering / Birth**
≥ 3 Buffer-Engrams mit paarweiser Cosine-Similarity ≥ 0.75 (auf den Embeddings) bilden einen Cluster-Kandidaten. Operiert im Embedding-Raum, nicht über shared Entities.

**R2 — Repetition / Maturation**
Ein Cluster wird erst zum Schema-Kandidaten, nachdem er ≥ 2 C2-Zyklen überlebt hat, ohne zu zerfallen. Einmalige Cluster werden ignoriert — nur wiederkehrende Muster werden zu Schemas. Das filtert Rauschen heraus.

**R3 — Abstraction / Specialization**
Auf Schema-Ebene: Zwei semantisch verwandte Schemas (Centroid-Cosine ≥ 0.7) mit systematisch unterschiedlichen Property-Werten können zu einem **Hyper-Schema** subsumiert werden — modelliert über `(:Schema)-[:SPECIALIZES]->(:HyperSchema)`.

R3 läuft **nicht** auf Engram-Ebene als Property-Extraktion (das ist die statistische Aggregation in C2 Schritt 4 und ist deterministisch). R3 ist ausschließlich der Schema-zu-Hyper-Schema-Schritt im Cortex.

**R4 — Reinforcement / Growth**
Ein neuer Engram, der per Centroid-Match (Cosine ≥ 0.85) zu einem existierenden Schema passt, verstärkt das Schema:
- `evidence_count++`
- ggf. Eintrag in Top-N `evidence_engram_ids`
- Centroid neu mitteln
- Properties verfeinern
- `last_reinforced_at` aktualisieren

**R5 — Competition / Death**
Schemas mit `cycles_since_last_reinforced > K` (Default K = 4) **und** `evidence_count < threshold` werden `archived`. Sie verschwinden nicht aus dem Graphen, sind aber nicht mehr aktiv. Verhindert Schema-Inflation und entspricht synaptic homeostasis im Cortex.

### Ausführungskontexte

| Regel | Kontext | Frequenz |
|-------|---------|----------|
| R1 + R2 | C2 (Pattern Recognition) | alle 24h |
| R3 + R5 | C3 (Schema-Graph Restrukturierung) | alle 168h (7 Tage) |
| R4 | (a) Inkrementell bei Retain (Schema-Fit-Check) <br> (b) Bei Match in C2 Schritt 3 | bei jedem Retain + alle 24h |

Diese Aufteilung entspricht der biologischen Trennung: schnelle Schema-Verstärkung bei kompatibler Information (R4 incremental, Tse et al. 2007), langsame Pattern-Abstraktion und -Bereinigung in den Schlafphasen (R1+R2 in SWS-Replay, R3+R5 in REM).

Detaillierte Pipeline siehe Kapitel 12.2 (C2) und 12.3 (C3).

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

Feste Task-zu-Model-Tier-Zuordnung. Kein dynamisches Routing zur Laufzeit. Konfiguration über Env-Vars + Budget-Profiles, Switching zwischen Cloud- und lokalen Providern in <30 Sekunden ohne Code-Änderung.

### 3 Tiers

| Tier | Aufgaben | Cloud-Modelle | Lokale Modelle (Ollama) |
|------|----------|---------------|-------------------------|
| **SMALL** | Einfache Klassifikation, Deduplication-Checks, Schema-Description | Claude Haiku / GPT-4o-mini | Phi-3 mini, Qwen 2.5 7B, Llama 3.2 8B |
| **MEDIUM** | Entity Resolution, Schema-Fit-Check, Reconsolidation | Claude Sonnet / GPT-4o | Qwen 2.5 14B, Phi-4 14B, Mistral Small 24B |
| **LARGE** | Komplexe Fact Extraction, Multi-Hop Reflect Think, Constructive Memory, Conflict Resolution | Claude Opus | Qwen 2.5 32B (eng), kein 70B+ auf typischen Macs |

### Pipeline-Steps (Auszug)

| Subtask | Tier | Beschreibung |
|---|---|---|
| `retain.thalamus_scoring` | SMALL | Binary Relevance Gate + 0–1 Scores |
| `retain.fact_extraction` | LARGE | Entity-Linking, kausale + temporale Relationen |
| `retain.observation_synthesis` | MEDIUM | Entity-Level Fakten zu Observations |
| `retain.schema_fit_check` | MEDIUM | Game-of-Life R4 inkrementell |
| `retain.conflict_resolution` | LARGE | Widerspruchsauflösung |
| **`consolidation.schema_description`** | **SMALL** | **Klartext aus Properties (Data-to-Text), C2 Schritt 6** |
| `reflect.think` | LARGE | Multi-Hop-Reasoning + Answer Construction |
| `reflect.constructive_memory_inference` | LARGE | Cross-Fact-Inferenz |
| `reflect.reconsolidation` | MEDIUM | Confirm/Modify/Contradict bei Recall |
| `reflect.prediction_error_detection` | SMALL | Severity-Klassifikation |

`consolidation.schema_description` ist der einzige LLM-Streifen in der gesamten Consolidation Pipeline (siehe Kapitel 12.2 Schritt 6). Alle anderen C1/C2/C3-Schritte sind deterministisch.

### Implementation

**L1:** Vollständiges `TASK_TIER_MAPPING` + `PIPELINE_STEP_TASK_KEY` für alle Operationen, inkl. `consolidation.schema_description`.

**L2:** `LLMConfig` per-Subtask konfigurierbar (Env-Vars `HINDSIGHT_API_{OP}_{SUBTASK}_LLM_{MODEL,PROVIDER}`).

**L3:** `PROVIDER_TIER_MODELS` für Anthropic, OpenAI, Groq mit konkreten Modellnamen pro Tier.

**L4 (Budget-Profile):** `LOW_BUDGET` / `MID_BUDGET` / `HIGH_BUDGET` als komplette Konfigurations-Sets, override-bar pro Pipeline-Step. Priority-Chain: Request > Bank > Env > Profile.

### Lokales LLM-Setup (Ollama)

Hindsight unterstützt Ollama nativ als Provider. Auf einem MacBook Pro M3 Pro mit 36 GB RAM ist die empfohlene Konfiguration:

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:14b              # globaler Default für alle Subtasks
HINDSIGHT_API_LLM_MAX_CONCURRENT=1 # Pflicht für lokale LLMs
HINDSIGHT_API_LLM_TIMEOUT=120      # lokale Modelle brauchen länger
```

Da `PROVIDER_TIER_MODELS` für Ollama bewusst leer ist (lokale Modellnamen sind installations-spezifisch), fällt das Routing für jeden Subtask auf den globalen `LLM_MODEL` zurück. Heißt: ein Modell für alle Tiers, kein Modell-Swap-Overhead in Ollama. Bei 36 GB RAM und ~150 GB/s Memory-Bandwidth des M3 Pro liegt der Sweet Spot bei 14B-Modellen mit 4-bit-Quantisierung.

**Trade-offs:**
- SMALL- und MEDIUM-Tasks: Qualität nahe oder über den jeweiligen Cloud-Pendants
- LARGE-Tasks (`reflect.think`, `fact_extraction`, `conflict_resolution`): merklich schwächer als Sonnet/Opus bei komplexem Multi-Hop-Reasoning
- Speed: ~20 tok/s, Recall-Antwort 5–15 s — interaktiv tragbar, Hintergrund-Tasks irrelevant

### Switching Cloud ↔ Lokal

Profile-basiertes Switching ohne Code-Änderung:

```bash
# Komplett auf Cloud (Haiku)
export LLM_PROVIDER=anthropic
export LLM_MODEL=claude-haiku-4-5-20251001
# hindsight-api restart

# Punktuelles Hochstufen einzelner Subtasks
export HINDSIGHT_API_REFLECT_THINK_LLM_PROVIDER=anthropic
export HINDSIGHT_API_REFLECT_THINK_LLM_MODEL=claude-sonnet-4-6
```

Damit kann z.B. die Recall-Synthese hochwertig (Cloud) bleiben, während alle Hintergrund-Tasks (Schema-Description, Thalamus, Schema-Fit) lokal laufen — der pragmatische Hybrid-Mittelweg.

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
