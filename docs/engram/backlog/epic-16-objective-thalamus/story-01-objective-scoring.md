# Story 01 — Objektive Score-Berechnung

## User Story

Als System will ich alle 4 Thalamus-Dimensionen rein embedding-basiert berechnen, damit die Scores deterministisch, reproduzierbar und kostenfrei sind.

## Kontext

Die aktuelle Implementierung nutzt einen LLM-Call für Emotional Valence (≈$0.0002/Call). Das ist nicht-deterministisch und skaliert schlecht. Der neue Ansatz leitet alle Scores aus Embedding-Operationen ab. Die neuen API-Felder (Expectation, Outcome) aus Epic 15 liefern die nötigen Inputs.

**Kernänderung:** Surprise und Emotional Valence basieren jetzt BEIDE auf dem Prediction Error (Expectation↔Outcome), aber messen unterschiedliche Aspekte:
- Surprise = binär "ist es anders als erwartet?" (Direction-agnostic)
- Emotional Valence = "wie bedeutsam ist die Abweichung?" (Magnitude mit Amplification)

## Bestehende Codebasis

- **ThalamusFilter:** `engine/thalamus.py` — _score_novelty, _score_surprise, _score_task_relevance, _score_emotional_valence.
- **ThalamusScores:** `engine/engram_types.py` — Dataclass mit 5 Feldern.
- **Tests:** `tests/test_thalamus.py` — 28+ Tests, vollständig gemocked.

## Akzeptanzkriterien

- [ ] _score_surprise() nutzt Expectation + Outcome Embeddings statt Expectation + Input
- [ ] _score_emotional_valence() nutzt Prediction Error Magnitude statt LLM-Call
- [ ] LLM-Dependency in ThalamusFilter.__init__() entfernt (kein `llm` Parameter mehr)
- [ ] Valence Amplification Factor als Klassen-Konstante mit Env-Var Override
- [ ] Fallback-Werte: Surprise=0.5 wenn Expectation ODER Outcome fehlt, Valence=0.3 wenn eines fehlt
- [ ] Alle 4 Scores sind deterministisch (gleicher Input → gleicher Output)
- [ ] Bestehende Tests angepasst, LLM-Mocks durch Embedding-Mocks ersetzt
- [ ] Neue Tests: Surprise mit bekannten Expectation/Outcome Paaren, Valence Amplification

## Tasks

- [ ] **T1 — _score_surprise() Refactoring:** Input: expectation_embedding, outcome_embedding (statt input_embedding, session). Berechnung: `1.0 - cosine(expectation_embedding, outcome_embedding)`. Fallback: 0.5 wenn expectation oder outcome None.
- [ ] **T2 — _score_emotional_valence() Refactoring:** LLM-Call entfernen. Input: expectation_embedding, outcome_embedding. Berechnung: `min(1.0, prediction_error * VALENCE_AMPLIFICATION)`. Prediction Error = `1.0 - cosine(expectation_embedding, outcome_embedding)`. `VALENCE_AMPLIFICATION: float = 1.5` (Env-Var: `HINDSIGHT_API_VALENCE_AMPLIFICATION`). Fallback: 0.3 wenn eines fehlt.
- [ ] **T3 — _score_task_relevance() Anpassung:** Input: content_embedding, context_embedding (statt input_embedding, session.task_context). Context kommt jetzt direkt vom Caller (Epic 15), nicht aus der Session. Fallback: 0.5 wenn Context fehlt.
- [ ] **T4 — ThalamusFilter Signatur anpassen:** `__init__(self, qdrant, embeddings)` — LLM Parameter entfernen. `score(self, content, context?, expectation?, outcome?, session, bank_id?)` — neue Signatur mit optionalen Feldern. Embeddings werden intern für alle vorhandenen Felder generiert.
- [ ] **T5 — Konstanten und Konfiguration:** `VALENCE_AMPLIFICATION: Final[float] = 1.5` mit Env-Var Override. Bio-Mapping Dokumentation: "Amplification models the amygdala's role in magnifying prediction errors into emotional significance."
- [ ] **T6 — Tests aktualisieren:** Alle LLM-Mocks durch Embedding-Mocks ersetzen. Neue Tests: deterministische Score-Verifikation (gleicher Input → gleicher Output über 100 Runs). Surprise mit identischem Expectation/Outcome → 0.0. Surprise mit orthogonalen Vektoren → 1.0. Valence Amplification Test.
