# Story 01 — Thalamus Scoring Engine

## User Story

Als System brauche ich eine Scoring-Komponente die eingehende Episoden auf 4 Dimensionen bewertet, damit nur relevante Information in die Retain Pipeline gelangt.

## Kontext

Der Thalamus Filter ist das biologische Äquivalent des sensorischen Gatings — er entscheidet was Aufmerksamkeit verdient. Die 4 Scores (Novelty, Surprise, Task-Relevance, Emotional Valence) werden gewichtet kombiniert. Die Gewichtung hängt vom Session Mode ab (Exploration → Novelty-Boost, Precision → Relevance-Boost).

## Bestehende Codebasis

- **ThalamusScores:** `hindsight_api/engine/retain/types.py` (aus Epic 02) — Dataclass mit novelty, surprise, task_relevance, emotional_valence, overall.
- **Session:** `hindsight_api/engine/response_models.py` (aus Epic 02) — Session Model mit mode und current_expectation.
- **Embeddings:** `hindsight_api/engine/embeddings.py` — Embedding-Generierung für Novelty-Check (Similarity gegen bestehende Engrams).
- **Qdrant Client:** `hindsight_api/engine/qdrant_client.py` (aus Epic 01) — `search_similar()` für Novelty-Check.
- **LLM Routing:** `hindsight_api/engine/llm_routing.py` (aus Epic 03) — Thalamus Scoring ist Small-Tier Task.

## Akzeptanzkriterien

- [ ] ThalamFilter Klasse mit `score(episode, session) → ThalamusScores` Methode
- [ ] Novelty Score: basierend auf Qdrant Similarity (niedriger Similarity = höhere Novelty)
- [ ] Surprise Score: Abweichung von Session.current_expectation (LLM-basiert oder heuristisch)
- [ ] Task-Relevance Score: Relevanz zum Session.task_context (Embedding Similarity)
- [ ] Emotional Valence Score: LLM-basierte Einschätzung (Small-Tier)
- [ ] Overall Score: gewichtete Kombination, mode-abhängig
- [ ] Mode-abhängige Thresholds definiert

## Tasks

- [ ] **T1 — ThalamusFilter Klasse erstellen:** Neues Modul `hindsight_api/engine/thalamus.py`. Klasse `ThalamusFilter` mit Dependency auf Qdrant Client, Embedding Provider, LLM (Small-Tier). Hauptmethode: `async score(text: str, session: Session) → ThalamusScores`.
- [ ] **T2 — Novelty Score implementieren:** Embedding des Input-Texts generieren → Qdrant `search_similar(embedding, limit=5)` → Höchste Similarity als Basis: `novelty = 1.0 - max_similarity`. Kein Match in Qdrant → novelty = 1.0. Sehr ähnlich (>0.95) → novelty ≈ 0.0.
- [ ] **T3 — Surprise Score implementieren:** Wenn `session.current_expectation` gesetzt: Embedding-Similarity zwischen Input und Expectation berechnen. Hohe Similarity = niedrige Surprise (erwartet). Niedrige Similarity = hohe Surprise. Wenn keine Expectation: surprise = 0.5 (neutral).
- [ ] **T4 — Task-Relevance Score implementieren:** Wenn `session.task_context` gesetzt: Embedding-Similarity zwischen Input und Task Context. Hohe Similarity = hohe Relevance. Wenn kein Task Context: relevance = 0.5 (neutral).
- [ ] **T5 — Emotional Valence Score implementieren:** LLM-Call (Small-Tier via LLM Routing): "Rate the emotional significance of this text on a scale of 0.0-1.0". Einfacher Prompt, kurze Response. Fallback auf 0.3 bei LLM-Fehler.
- [ ] **T6 — Mode-abhängige Gewichtung:** In `thalamus.py` Dictionary `MODE_WEIGHTS: Dict[RetrievalMode, Dict[str, float]]`. Exploration: {novelty: 0.4, surprise: 0.2, relevance: 0.2, emotion: 0.2}. Precision: {novelty: 0.1, surprise: 0.2, relevance: 0.5, emotion: 0.2}. Validation: {novelty: 0.2, surprise: 0.4, relevance: 0.2, emotion: 0.2}. Analogy: {novelty: 0.3, surprise: 0.2, relevance: 0.3, emotion: 0.2}. Overall = gewichtete Summe.
- [ ] **T7 — Threshold-Konfiguration:** Mode-abhängige Thresholds: Exploration: 0.2 (niedriger, mehr durchlassen), Precision: 0.4 (höher, nur Relevantes), Validation: 0.3, Analogy: 0.3. Konfigurierbar über Env-Vars mit Defaults.
- [ ] **T8 — Unit Tests:** Score-Berechnung mit bekannten Inputs. Mode-abhängige Gewichtung. Threshold-Filtering. Fallback bei fehlendem Session Context.
