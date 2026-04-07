# Epic 16 — Objektiver Thalamus-Scoring-Rahmen

> Alle 4 Dimensionen embedding-basiert, deterministisch, ohne LLM-Kosten.

## Ziel

Refactoring des Thalamus Filters (Epic 04) zu einem vollständig objektiven, deterministischen Scoring-System. Alle 4 Dimensionen werden rein embedding-basiert berechnet. Der bisherige LLM-Call für Emotional Valence wird durch eine Prediction-Error-basierte Formel ersetzt. Die neuen API-Felder (Expectation, Outcome aus Epic 15) liefern die notwendigen Inputs.

## Design-Entscheidungen

**Neuroscience-Basis:**
- **Novelty** → CA1 Mismatch Detection: "Wie anders ist das im Vergleich zu dem was ich kenne?"
- **Surprise** → Noradrenaline/Prediction Error: "Wie stark weicht das Outcome von der Expectation ab?"
- **Task-Relevance** → PFC Top-Down Attention: "Wie relevant ist das für meinen aktuellen Kontext?"
- **Emotional Valence** → Amygdala/Dopamin/Cortisol: "Wie bedeutsam ist der Prediction Error?" (Magnitude des Deltas)

**Objektiver Scoring-Rahmen:**

| Dimension | Input | Berechnung | Fallback (wenn Input fehlt) |
|-----------|-------|------------|----------------------------|
| Novelty | Content | `1.0 - max_similarity(Qdrant, content_embedding)` | Unverändert (kein Fallback nötig) |
| Surprise | Expectation + Outcome | `1.0 - cosine(embed(expectation), embed(outcome))` | 0.5 (neutral) wenn eines fehlt |
| Task-Relevance | Content + Context | `cosine(embed(content), embed(context))` | 0.5 (neutral) wenn Context fehlt |
| Emotional Valence | Expectation + Outcome | `f(prediction_error_magnitude)` | 0.3 (low, Inklusions-Bias) wenn eines fehlt |

**Valence-Formel:** Die Emotional Valence wird aus dem Prediction Error abgeleitet. Ein großes Delta zwischen Expectation und Outcome = hohe emotionale Bedeutung (egal ob positiv oder negativ). Kleines Delta = Routine. Das ist analog zur Amygdala — sie reagiert auf Signifikanz, nicht auf Richtung.

`emotional_valence = min(1.0, prediction_error_magnitude * valence_amplification_factor)`

Wobei `prediction_error_magnitude = 1.0 - cosine(embed(expectation), embed(outcome))` und `valence_amplification_factor` konfigurierbar ist (Default: 1.5 — leichte Verstärkung damit mittlere Prediction Errors nicht zu niedrig scoren).

**Vorteil:** Alle 4 Dimensionen sind jetzt:
- Deterministisch (gleicher Input → gleicher Output)
- Reproduzierbar (keine LLM-Varianz)
- Kostenlos (keine LLM-Calls, nur Embeddings)
- Schnell (nur Vektor-Operationen)

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/engine/thalamus.py` — ThalamusFilter Klasse. Refactoring-Target.
- `hindsight-api/hindsight_api/engine/engram_types.py` — ThalamusScores Dataclass.
- `hindsight-api/tests/test_thalamus.py` — Bestehende Tests (28+), müssen angepasst werden.
- `hindsight-api/hindsight_api/engine/retain/orchestrator.py` — Thalamus-Aufruf mit neuen Feldern.

## Scope

- Surprise-Berechnung auf Expectation↔Outcome umstellen
- Emotional Valence auf Prediction-Error-Magnitude umstellen (LLM-Call entfernen)
- Task-Relevance auf Content↔Context umstellen (statt task_context aus Session)
- Fallback-Werte für fehlende Felder definieren
- Valence Amplification Factor als Konfiguration
- Bestehende Tests anpassen, neue Tests für objektive Berechnung

## Nicht in Scope

- API-Erweiterung (→ Epic 15, muss zuerst fertig sein)
- Mode-abhängige Gewichtung (bleibt wie in Epic 04)
- Threshold-Anpassung (bleibt wie in Epic 04)

## Abhängigkeiten

- **Epic 15** (API Enrichment) — liefert Expectation + Outcome Felder
- Epic 04 (Thalamus Filter) — wird refactored

## Referenzen

- `concept.md` → Abschnitt 5 (Thalamus Filter) — wird mit objektivem Rahmen aktualisiert
- `engram_architecture_complete.md` → Kapitel 2 (Thalamus Filter)

## Stories

1. [Objektive Score-Berechnung](story-01-objective-scoring.md)
2. [Thalamus Integration mit erweiterten Feldern](story-02-field-integration.md)
