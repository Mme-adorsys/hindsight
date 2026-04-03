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

- [ ] ThalamusScores Modell definiert (novelty, surprise, task_relevance, emotional_valence, overall)
- [ ] ExtractedFact hat optionale Felder `tags` und `thalamus_scores` (optional für Rückwärtskompatibilität)
- [ ] ProcessedFact erbt die neuen Felder
- [ ] Engram als eigenständiges Pydantic Model mit allen Feldern aus concept.md Abschnitt 4
- [ ] FullEngram Felder konkretisiert (aus Epic 01 Story 04)
- [ ] Fact Extraction LLM-Prompt extrahiert Tags + Thalamus-Scores
- [ ] Bestehende Pipeline läuft weiter (neue Felder sind optional mit Defaults)

## Tasks

- [ ] **T1 — ThalamusScores Dataclass definieren:** In `hindsight_api/engine/retain/types.py` neue Dataclass: `ThalamusScores { novelty: float = 0.0, surprise: float = 0.0, task_relevance: float = 0.0, emotional_valence: float = 0.0, overall: float = 0.0 }`. Alle Scores im Bereich 0.0-1.0.
- [ ] **T2 — ExtractedFact erweitern:** Neue optionale Felder: `tags: List[str] = field(default_factory=list)`, `thalamus_scores: Optional[ThalamusScores] = None`. fact_type bleibt bestehen (Rückwärtskompatibilität), wird aber perspektivisch durch tags abgelöst.
- [ ] **T3 — Engram Pydantic Model definieren:** In `hindsight_api/engine/response_models.py` neues Model: `Engram { engram_id: UUID, text: str, embedding: Optional[List[float]], tags: List[str], strength: float, layer: Literal['buffer', 'neocortex'], abstraction_level: float, thalamus_scores: ThalamusScores, created_at: datetime, last_accessed: Optional[datetime], access_count: int, session_ref: Optional[UUID], status: Literal['active', 'archived', 'decayed'], confidence_score: Optional[float] }`.
- [ ] **T4 — FullEngram Felder konkretisieren:** Das in Epic 01 Story 04 vorbereitete FullEngram-Model mit konkreten Sub-Models verbinden: `EngramMetadata` nutzt Felder aus dem Dictionary, `EngramContent` nutzt text + embedding aus Qdrant, `EngramRelationship` nutzt Neo4j-Daten. Import-Referenzen zwischen den Models klären.
- [ ] **T5 — Fact Extraction Prompt erweitern:** In `hindsight_api/engine/retain/fact_extraction.py` den LLM-Output-Schema um `tags` (List[str]) und `thalamus_scores` (Object mit novelty, surprise, task_relevance, emotional_valence) ergänzen. Prompt-Instruktionen anpassen: Tags sollen frei gewählt werden (keine vordefinierte Liste), Thalamus-Scores sollen relative Einschätzungen sein (0.0-1.0).
- [ ] **T6 — Extraction Response Parsing anpassen:** In `fact_extraction.py` das Parsing der LLM-Response erweitern um tags und thalamus_scores. Fallback auf leere Tags und None für thalamus_scores wenn LLM die Felder nicht liefert.
- [ ] **T7 — Unit Tests:** Tests für: ThalamusScores Defaults, ExtractedFact mit und ohne neue Felder, Engram Model Validierung, FullEngram Zusammensetzung. Bestehende Tests müssen weiterhin grün sein.
