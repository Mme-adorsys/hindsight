# Story 01 — Engram Pydantic Models

## User Story

Als System brauche ich ein Engram-Datenmodell das Tags, Thalamus-Scores und Strength enthält, damit Wissenseinheiten mehrdimensional bewertet und gefiltert werden können — statt nur über den starren fact_type.

## Kontext

Hindsight's aktuelles Modell kennt nur `fact_type` (world, experience, opinion, observation) als Kategorisierung und `confidence_score` als einzige Bewertungsdimension. Engrams ersetzen das durch: Tags (flexible Kategorisierung), Thalamus-Scores (4-dimensionale Relevanzbewertung), Strength (Konsolidierungsstärke), Layer (buffer/neocortex), und Abstraction Level. Die bestehenden Dataclasses werden erweitert, nicht ersetzt — so bleibt der Code kompatibel bis die Pipelines in späteren Epics umgebaut werden.

## Bestehende Codebasis

- **ExtractedFact:** `hindsight_api/engine/retain/types.py` — Dataclass. Felder: fact_text, fact_type, entities, occurred_start/end, where, causal_relations, content_index, chunk_index, context, mentioned_at, metadata. **Erweiterung:** tags (List[str]), thalamus_scores (ThalamusScores).
- **ProcessedFact:** `hindsight_api/engine/retain/types.py` — Erweitert ExtractedFact um embedding, resolved EntityRefs, chunk_id, document_id. Erbt die neuen Felder automatisch.
- **FullEngram:** `hindsight_api/engine/response_models.py` — In Epic 01 Story 04 vorbereitet. Hier die konkreten Felder definieren.
- **Fact Extraction Prompt:** `hindsight_api/engine/retain/fact_extraction.py` — LLM-Prompt der Facts extrahiert. Muss Tags + Thalamus-Scores im Output-Schema ergänzen.
- **VALID_RECALL_FACT_TYPES:** `hindsight_api/engine/response_models.py` — frozenset(["world", "experience", "opinion"]). Wird durch Tags-basierte Filterung perspektivisch abgelöst (aber nicht in diesem Epic).

## Akzeptanzkriterien

- [x] ThalamusScores Modell definiert (novelty, surprise, task_relevance, emotional_valence, overall)
- [x] ExtractedFact hat optionale Felder `tags` und `thalamus_scores` (optional für Rückwärtskompatibilität)
- [x] ProcessedFact erbt die neuen Felder
- [x] Engram als eigenständiges Pydantic Model mit allen Feldern aus concept.md Abschnitt 4
- [x] FullEngram Felder konkretisiert (aus Epic 01 Story 04)
- [x] Fact Extraction LLM-Prompt extrahiert Tags + Thalamus-Scores
- [x] Bestehende Pipeline läuft weiter (neue Felder sind optional mit Defaults)

## Tasks

- [x] **T1 — ThalamusScores Dataclass definieren:** In `hindsight_api/engine/engram_types.py` (circular import fix — nicht in retain/types.py). `ThalamusScores { novelty, surprise, task_relevance, emotional_valence, overall }` alle 0.0-1.0.
- [x] **T2 — ExtractedFact erweitern:** `tags: list[str]` + `thalamus_scores: ThalamusScores | None` in ExtractedFact + ProcessedFact. from_extracted_fact() reicht neue Felder durch.
- [x] **T3 — Engram Pydantic Model definieren:** `Engram` in `response_models.py` mit allen Feldern aus concept.md Abschnitt 4.
- [x] **T4 — FullEngram Felder konkretisieren:** EngramMetadata hat bereits alle Thalamus-Felder aus Epic 01. Verifiziert — kein Refactoring nötig.
- [x] **T5 — Fact Extraction Prompt erweitern:** `ThalamusScoresLLM` Pydantic Model + tags/thalamus_scores in alle ExtractedFact*-Varianten + TAGS/THALAMUS SCORES Prompt-Sektionen.
- [x] **T6 — Extraction Response Parsing anpassen:** Parsing-Loop extrahiert tags + thalamus_scores aus raw JSON. Fallback auf leere Tags / None.
- [x] **T7 — Unit Tests:** 23/23 Tests grün in `tests/test_engram_models.py`.
