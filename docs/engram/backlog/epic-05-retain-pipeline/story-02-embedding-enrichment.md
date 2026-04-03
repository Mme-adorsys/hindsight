# Story 02 — Embedding Enrichment (R2)

## User Story

Als System soll die Embedding-Generierung um Session-Kontext und Thalamus-Scores angereichert werden, damit semantische Ähnlichkeit mehr Dimensionen berücksichtigt als nur den Fakt-Text.

## Kontext

Hindsight's `augment_texts_with_dates()` fügt nur das Datum zum Fakt-Text hinzu. Wir erweitern das um:
- **Temporaler Kontext:** Beibehalten (Datum + relative Zeitangaben)
- **Session-Kontext:** Task Context und Mode der aktiven Session
- **Thalamus-Score Kontext:** Dominante Dimension als semantischer Hinweis

Das Ziel: Zwei Fakten mit gleichem Text aber unterschiedlichem Kontext (z.B. einmal in Exploration-Mode, einmal in Precision-Mode) bekommen leicht unterschiedliche Embeddings, was kontextbewussteres Retrieval ermöglicht.

## Bestehende Codebasis

- **Embedding Processing:** `hindsight_api/engine/retain/embedding_processing.py` — `augment_texts_with_dates(facts, format_date_fn)` erzeugt augmentierte Texte. `generate_embeddings_batch(embeddings_model, texts)` generiert Embedding-Vektoren.
- **Embeddings:** `hindsight_api/engine/embeddings.py` — Embedding-Modell (384-dim Default). Wird vom Orchestrator injiziert.
- **Orchestrator:** `hindsight_api/engine/retain/orchestrator.py` — Ruft `augment_texts_with_dates()` in Schritt 2 auf, dann `generate_embeddings_batch()`.
- **Session:** `hindsight_api/engine/response_models.py` (aus Epic 02) — Session mit `mode`, `task_context`, `current_expectation`.

## Akzeptanzkriterien

- [ ] Augmentierter Text enthält Session-Kontext (task_context) wenn vorhanden
- [ ] Augmentierter Text enthält dominante Thalamus-Dimension als Hinweis
- [ ] Bestehende Datum-Augmentierung bleibt erhalten
- [ ] Ohne Session/Thalamus Scores: Verhalten identisch zu Hindsight (nur Datum)
- [ ] Embedding-Dimension bleibt unverändert (384-dim)
- [ ] Performance: Augmentierung hat keinen messbaren Impact auf Pipeline-Durchsatz

## Tasks

- [ ] **T1 — augment_texts_with_context() erstellen:** In `embedding_processing.py` neue Funktion `augment_texts_with_context(facts, format_date_fn, session=None)`. Baut auf `augment_texts_with_dates()` auf. Fügt hinzu: Session task_context (wenn vorhanden), dominante Thalamus-Dimension als Label (z.B. "[high novelty]", "[task relevant]"). Format: `"{fact_text} | {date_context} | {session_context} | {thalamus_hint}"`. Leere Segmente werden weggelassen.
- [ ] **T2 — Dominante Dimension berechnen:** Helper-Funktion `get_dominant_thalamus_label(thalamus_scores: ThalamusScores) -> str | None`. Gibt die Dimension mit dem höchsten Score zurück als Label: "high novelty", "surprising", "task relevant", "emotionally significant". Nur wenn Score > 0.6 (klare Dominanz). Sonst None.
- [ ] **T3 — Orchestrator umstellen:** In `orchestrator.py`: Aufruf von `augment_texts_with_dates()` durch `augment_texts_with_context()` ersetzen. Session als optionalen Parameter durchreichen. Die alte Funktion bleibt als Fallback (wenn `augment_texts_with_context` importiert wird, ruft sie intern `augment_texts_with_dates` auf).
- [ ] **T4 — Unit Tests:** Augmentierung mit allen Kontexten. Augmentierung nur mit Datum (backward compat). Dominante Dimension bei verschiedenen Score-Verteilungen. Kein Session-Kontext → nur Datum.
