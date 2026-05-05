# Memory Score Formulas & Lifecycle

Vollständige Übersicht der Score-Berechnungen und State-Übergänge im Engram Memory System.

> Stand: 2026-05-05
> Code-Referenzen: `hindsight-api/hindsight_api/engine/`

---

## ⚠️ Modell-Kalibrierungs-Bindung

Alle Schwellwerte in diesem Dokument (CE-Min-Score, Strength-Pre-Filter, Similarity-Threshold, Composite-Hard-Gates, Tag-Overlap-Gewichte) sind **empirisch kalibriert** gegen die aktuell konfigurierten Modelle:

| Komponente | Modell | Default-Quelle |
|---|---|---|
| Cross-Encoder | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | `engine/cross_encoder.py` (`LocalSTCrossEncoder`) |
| Embeddings | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | `engine/embeddings.py` (`LocalSTEmbeddings`) |

Ein Modellwechsel **invalidiert** die Kalibrierung — die Schwellwerte stimmen dann nicht mehr mit der neuen Score-Verteilung überein und die Recall-Pipeline kann je nach Verschiebungsrichtung entweder alles durchlassen oder alles wegfiltern.

### Konsequenzen pro Wechsel-Typ

- **Cross-Encoder-Wechsel** → CE-Score-Verteilung ändert sich. `ce_min_score` pro Mode (`recall_orchestrator.py:RECALL_MODE_CONFIG`, aktuell 0.01–0.05) und die BM25-Rescue-Stufe (Step 5.5) müssen neu evaluiert werden.
- **Embedding-Wechsel** → Cosine-Similarity-Verteilung ändert sich. `similarity_threshold` pro Mode (aktuell 0.5–0.7) muss neu gesetzt werden.
- **Strength-Bootstrap-Wechsel** (neue Initial-Werte für frische Engrams) → `strength_pre_filter` pro Mode (`MODE_PROFILES`, aktuell 0.0–0.1) muss nachziehen, sonst gehen entweder alle frischen Engrams oder keine Buffer-Engrams durch.

### Re-Kalibrierungs-Prozess

1. Repräsentatives Query-Set + bekannte Ground-Truth-Engrams in einen Diagnose-Bank laden.
2. Pro Query und Mode: `uv run python -m hindsight_dev.diagnose_recall --query "<query>" --mode <mode>` ausführen und die ausgegebenen BM25-, Semantik- und CE-Werte erfassen.
3. CE-Score-Verteilung pro Mode beobachten (Min/Max/Median) — daraus neue Thresholds ableiten so dass Precision exakte Keyword-Matches sicher zurückgibt und Exploration breit genug bleibt.
4. Schwellwerte an **beiden** Stellen anfassen: `engine/recall_orchestrator.py:RECALL_MODE_CONFIG` (Step-5.5-Filter, max_results, similarity_threshold) und `engine/session/mode_config.py:MODE_PROFILES` (strength_pre_filter, scoring_weights inkl. tag_overlap).
5. `tests/test_ce_threshold_filter.py` erweitern um neue Modi-spezifische Regression-Cases (z.B. "Modus X liefert Engram zurück bei exaktem Keyword-Match").
6. `tests/test_session_mode_config.py` Direction-Asserts (`test_precision_high_ce`, `test_exploration_high_thalamus`) auf das neue Modell-Verhalten anpassen falls die Gewichts-Hierarchie sich verschiebt.

### Hardcoded Thresholds — Single Source of Truth

Beide Konfigurations-Stellen müssen synchron bleiben:

```
hindsight-api/hindsight_api/engine/recall_orchestrator.py:66-71   # RECALL_MODE_CONFIG
hindsight-api/hindsight_api/engine/session/mode_config.py:170-220 # _WEIGHTS_* + MODE_PROFILES
```

Wer das Cross-Encoder- oder Embedding-Modell tauscht ohne neu zu kalibrieren, verschiebt die operative Recall-Qualität ohne Test-Failure — die Tests sind so geschrieben dass sie Outcome (Engram zurückgegeben) prüfen, nicht konkrete Score-Magnituden.

---

## Lifecycle-Übersicht

```
                    ┌──────────────────┐
                    │  Retain Episode  │
                    └────────┬─────────┘
                             ↓
                  ┌──────────────────────┐
                  │   Thalamus Gate      │  overall ≥ mode_threshold
                  │  (relevance filter)  │
                  └──────────┬───────────┘
                             ↓ pass
                ┌────────────────────────┐
                │  Working Memory        │  layer='working'
                │  layer='working'       │  strength=0.0
                └────────────┬───────────┘
                             ↓
                    ┌────────────────┐
                    │   C1 (Session  │  recalls auf Engrams
                    │    End/Manual) │  → access_count steigt
                    └────────┬───────┘
                             ↓
                  ┌──────────────────────┐
                  │  3 Hard Gates:       │
                  │  1. novelty ≥ 0.2    │  → sonst ARCHIVED
                  │  2. access_count ≥ 5 │  → sonst stay WORKING
                  │  3. composite        │  → sonst stay WORKING
                  │     ≥ mode_threshold │
                  └──────────┬───────────┘
                             ↓ pass
                  ┌──────────────────────┐
                  │  Buffer              │  layer='buffer'
                  │  layer='buffer'      │  strength=composite
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │  C2 Phase 1: Decay   │  jede Nacht
                  │  strength × 0.9 ×    │  strength fällt
                  │  frequency_bonus     │
                  │  (extra decay > 30d) │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │  C2 Phase 2:         │  3 Criteria:
                  │  Strengthen          │  • strength ≥ 0.4
                  │                      │  • access_count ≥ 3
                  │                      │  • ncr_cycles ≥ 2
                  └──────────┬───────────┘
                             ↓ pass
                  ┌──────────────────────┐
                  │  Neocortex           │  layer='neocortex'
                  │  layer='neocortex'   │  strength += 0.1
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │  C3 Schema           │  Game-of-Life Cluster
                  │  Compression         │  → Meta-Engrams
                  └──────────────────────┘

  Zu jedem Zeitpunkt: strength < 0.05  →  ARCHIVED
```

---

## 1. Retain — Thalamus Gate

**Datei:** `engine/thalamus.py`

Beim Retain berechnet der Thalamus 4 Dimensionen für jedes Content-Item. Der gewichtete Overall-Score entscheidet, ob der Content in die Working Memory aufgenommen wird.

### Per-Dimension Scores (alle in `[0, 1]`)

```
novelty           = 1.0 - max_similarity_to_existing_engrams_in_qdrant
                    (= 1.0 wenn Bank leer)

surprise          = 1.0 - cosine(expectation_emb, outcome_emb)
                    Fallback: LLM-Score wenn keine expectation/outcome

task_relevance    = cosine(content_emb, task_context_emb)
                    Fallback: 0.5 wenn kein Context

emotional_valence = min(1.0, prediction_error * 1.5)
                    Fallback: LLM-Score wenn keine expectation/outcome
```

> **Hybrid Scoring (Epic 24+):** surprise und emotional_valence werden vom Gate berechnet wenn expectation+outcome vorhanden sind. Sonst werden die LLM-Scores aus der Fact-Extraction verwendet (kommen "gratis" mit dem Extraction-Call).

### Mode-gewichteter Overall

```
overall = w_nov · novelty
        + w_sur · surprise
        + w_task · task_relevance
        + w_emo · emotional_valence
```

| Mode | novelty | surprise | task | emotional |
|---|---|---|---|---|
| **Exploration** | **0.4** | 0.2 | 0.2 | 0.2 |
| **Precision** | 0.15 | 0.2 | **0.45** | 0.2 |
| **Validation** | 0.2 | **0.4** | 0.2 | 0.2 |
| **Analogy** | 0.3 | 0.2 | 0.3 | 0.2 |

### Gate Thresholds (mode-abhängig)

```
overall ≥ mode_threshold  →  pass
overall <  mode_threshold  →  drop
```

| Mode | Threshold |
|---|---|
| Precision | 0.25 |
| Validation | 0.20 |
| Analogy | 0.20 |
| Exploration | 0.15 |

> Diese Werte wurden für `paraphrase-multilingual-MiniLM-L12-v2` rekalibriert. Mit dem alten English-only Model waren sie 0.4 / 0.3 / 0.3 / 0.2.

### Resultat

Engrams die das Gate passieren erhalten:
- `layer = 'working'`
- `strength = 0.0`
- 4 Dimensions-Scores werden in `engram_dictionary` gespeichert

---

## 2. Recall — access_count Update

**Datei:** `engine/recall_orchestrator.py`

Jeder Recall ist eine Suche, die per Mode-Config eine begrenzte Anzahl von Top-N Ergebnissen zurückliefert. **Nur die zurückgelieferten Engrams** bekommen `access_count += 1`.

```
access_count = access_count + 1
last_accessed = NOW()
```

### Mode-Config

| Mode | Similarity-Threshold | Token-Budget | CE-Min Score | Max Results |
|---|---|---|---|---|
| Precision | 0.7 | 1024 | 0.3 | 3 |
| Validation | 0.6 | 2048 | 0.2 | 5 |
| Analogy | 0.5 | 2048 | 0.1 | 5 |
| Exploration | 0.5 | 2048 | 0.05 | 10 |

---

## 3. C1 Consolidation: Working → Buffer

**Datei:** `engine/consolidation/scoring.py` + `consolidation1.py`

Triggert am Session-Ende (kein Cooldown). Selektiert Engrams aus Working Memory und promotet sie zu Buffer.

### 3 Hard Gates (alle müssen bestanden werden)

```
1. novelty < 0.2                  →  ARCHIVE  (bekannte Info)
2. access_count < 5               →  STAY in Working Memory
3. composite_score < threshold    →  STAY in Working Memory
```

### Composite Score Formel

```
saliency        = max(emotional_valence, surprise)
recall_score    = log(1 + access_count) / log(2 + cycles_alive)
                  cycles_alive = bank.op_count - engram.created_at_op

composite       = recall_score + 0.3 · saliency

# Clamp zur Vermeidung von inf/overflow
composite       = min(10.0, composite)
```

**Bio-Mapping:**
- `recall_score`: rehearsal-dependent capture (Synaptic Tagging & Capture model)
- `saliency boost`: amygdala modulation (emotional) + noradrenaline (surprise)
- `log/log` Form: logarithmic forgetting curve in hippocampus
- Min-access Gate: STC requirement — tagging allein reicht nicht, capture muss auch passieren

### Mode-Promotion-Thresholds

```
composite ≥ threshold  →  PROMOTE to Buffer
```

| Mode | Threshold |
|---|---|
| Precision | 0.8 |
| Validation | 0.7 |
| Analogy | 0.6 |
| Exploration | 0.5 |

### Auf Promotion

```
layer    = 'buffer'
strength = composite_score
```

### Beispielrechnungen

| Szenario | emot | sur | sal | access | cycles | recall_score | composite | Precision (0.8) | Exploration (0.5) |
|---|---|---|---|---|---|---|---|---|---|
| Wichtig, 5x recalled | 0.8 | 0.7 | 0.8 | 5 | 10 | 0.72 | **0.96** | PROMOTE | PROMOTE |
| Unwichtig, 30x recalled | 0.1 | 0.1 | 0.1 | 30 | 10 | 1.38 | **1.41** | PROMOTE | PROMOTE |
| Unwichtig, 5x recalled | 0.1 | 0.1 | 0.1 | 5 | 10 | 0.72 | **0.75** | skip | PROMOTE |
| Unwichtig, 5x, alt | 0.1 | 0.1 | 0.1 | 5 | 50 | 0.45 | **0.48** | skip | skip |

---

## 4. C2 Phase 1: Decay

**Datei:** `engine/consolidation/ncr_decay.py`

Läuft periodisch (alle 24h) auf alle aktiven `buffer` und `neocortex` Engrams.

### Decay Formel

```
frequency_bonus = 1.0 + log10(1 + access_count) / 10
new_strength    = current_strength · 0.9 · frequency_bonus

# Extra-Decay für stale Engrams
if days_since_access > 30:
    new_strength *= 0.95 ^ (days_since_access / 30)
```

**Bio-Mapping:** SWS / Sharp-Wave Ripples — schwache synaptische Verbindungen werden im Slow-Wave Sleep gepruned. Häufige Aktivierung wirkt dem Decay entgegen (LTP Late).

### Archive Threshold

```
if new_strength < 0.05:
    layer = 'archived'
    status = 'archived'
    # Links bleiben erhalten für C3 Schema Formation
```

### Beispielrechnungen

| Start-Strength | access_count | days_since | new_strength | Effekt |
|---|---|---|---|---|
| 1.0 | 0 | 0 | 0.900 | -10% (Standard-Decay) |
| 1.0 | 10 | 0 | 0.994 | -0.6% (Frequency-Bonus kompensiert fast komplett) |
| 1.0 | 100 | 0 | 1.081 | **+8.1%** (Frequency-Bonus überkompensiert!) |
| 1.0 | 0 | 60 | 0.811 | -19% (Standard + Extra-Decay) |
| 0.1 | 0 | 0 | 0.090 | -10% |
| 0.06 | 0 | 0 | 0.054 | **→ ARCHIVED** (unter 0.05) |

> **Hinweis:** Sehr häufig accessed Engrams können beim Decay sogar an Strength gewinnen. Das ist beabsichtigt — repeated activation strengthens synaptic connections.

---

## 5. C2 Phase 2: Strengthen (Buffer → Neocortex)

**Datei:** `engine/consolidation/ncr_strengthen.py`

Läuft im selben NCR-Cycle wie Decay. Promotet Buffer-Engrams zu Neocortex wenn alle Criteria erfüllt sind.

### Promotion Criteria (alle drei)

```
1. strength             ≥ 0.4
2. access_count         ≥ 3
3. ncr_cycles_survived  ≥ 2
```

### Auf Promotion

```
layer       = 'neocortex'
strength    = min(strength + 0.1, 1.0)   # Boost
promoted_at = NOW()
```

### Für Survivors die nicht promoted werden

```
ncr_cycles_survived += 1
```

So bauen sich Engrams über mehrere NCR-Cycles auf. Ein Engram braucht mindestens 2 Cycles im Buffer bevor es zur Neocortex promoted werden kann — das simuliert die multiple-night consolidation.

---

## 6. C3 Schema Compression

**Datei:** `engine/consolidation/schema_processor.py`

Läuft seltener (default alle 7 Tage). Findet Cluster ähnlicher Neocortex-Engrams und erzeugt Meta-Engrams (Schemas) die das Pattern abstrahieren.

> Detail-Formeln im SchemaProcessor — Game-of-Life Regeln für Cluster-Erkennung, LLM-basierte Pattern-Extraktion.

---

## 7. NCR Phasen-Trigger

**Datei:** `engine/consolidation/ncr_orchestrator.py`

| Phase | Trigger | Cooldown | Frequenz |
|---|---|---|---|
| **C1** (Working→Buffer) | Session-Ende | 0 | jede Session |
| **C2** (Decay + Strengthen) | NCR Scheduler | 1h | täglich (24h) |
| **C3** (Schema) | NCR Scheduler | 6h | wöchentlich (7d) |
| **Shared** (Multi-Bank) | NCR Scheduler | 1h | optional |

### API

```bash
POST /ncr/trigger?phase=c1     # Nur C1
POST /ncr/trigger?phase=c2     # Nur Decay + Strengthen
POST /ncr/trigger?phase=c3     # Nur Schema
POST /ncr/trigger              # Alle Phasen (default)
```

---

## Zusammenhang aller Scores

```
┌─────────────────────────────────────────────────────────────┐
│  Beim Retain berechnet:                                     │
│  • novelty (Qdrant similarity search)                       │
│  • surprise (cosine expectation/outcome ODER LLM)           │
│  • task_relevance (cosine content/context)                  │
│  • emotional_valence (prediction_error * 1.5 ODER LLM)      │
│  → overall = mode-gewichtete Summe                          │
│  → Gate: overall ≥ mode_threshold                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Beim Recall:                                               │
│  • access_count += 1 (für zurückgelieferte Engrams)         │
│  • last_accessed = NOW()                                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Bei C1 (Session-Ende):                                     │
│  • saliency = max(emotional, surprise)                      │
│  • recall_score = log(1+access)/log(2+cycles)               │
│  • composite = recall_score + 0.3*saliency                  │
│  → Gates: novelty≥0.2 AND access≥5 AND composite≥threshold  │
│  → Promote to Buffer mit strength=composite                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Bei C2 Decay (täglich):                                    │
│  • frequency_bonus = 1 + log10(1+access)/10                 │
│  • new_strength = strength * 0.9 * frequency_bonus          │
│  • Extra-Decay wenn stale > 30d                             │
│  → Archive wenn strength < 0.05                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Bei C2 Strengthen (täglich):                               │
│  • strength≥0.4 AND access≥3 AND ncr_cycles≥2               │
│  → Promote Buffer→Neocortex mit strength += 0.1             │
└─────────────────────────────────────────────────────────────┘
```

---

## Konstanten-Referenz

| Konstante | Wert | Datei |
|---|---|---|
| MIN_ACCESS_FOR_PROMOTE | 5 | scoring.py |
| MIN_NOVELTY_FOR_PROMOTE | 0.2 | scoring.py |
| SALIENCY_WEIGHT | 0.3 | scoring.py |
| ARCHIVE_THRESHOLD_WM | 0.08 | scoring.py |
| ARCHIVE_THRESHOLD_BUFFER | 0.05 | scoring.py |
| _DEFAULT_DECAY_RATE | 0.9 | ncr_decay.py |
| _ARCHIVE_THRESHOLD | 0.05 | ncr_decay.py |
| _EXTRA_DECAY_BASE | 0.95 | ncr_decay.py |
| _EXTRA_DECAY_DAYS_UNIT | 30 | ncr_decay.py |
| _DEFAULT_PROMOTION_STRENGTH | 0.4 | ncr_strengthen.py |
| _DEFAULT_PROMOTION_ACCESS | 3 | ncr_strengthen.py |
| _DEFAULT_PROMOTION_NCR_CYCLES | 2 | ncr_strengthen.py |
| _PROMOTION_STRENGTH_BOOST | 0.1 | ncr_strengthen.py |
