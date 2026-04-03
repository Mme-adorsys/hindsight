# Epic 04 — Thalamus Filter

> Relevance Scoring Gate. Entscheidet was gespeichert wird.

## Ziel

Implementierung des Thalamus Filters als Relevance Scoring Gate vor der Retain Pipeline. Jede eingehende Episode wird auf 4 Dimensionen bewertet (Novelty, Surprise, Task-Relevance, Emotional Valence). Unter dem Threshold wird verworfen. Darüber fließt die Episode mit initialem Thalamus-Score in die Retain Pipeline.

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/engine/retain/orchestrator.py` — Retain Orchestrator. Der Thalamus Filter wird VOR dem Orchestrator geschaltet.
- `hindsight-api/hindsight_api/engine/retain/types.py` — ExtractedFact mit neuen `thalamus_scores` (aus Epic 02).
- `hindsight-api/hindsight_api/engine/memory_engine.py` → `retain_batch_async()` — Einstiegspunkt. Thalamus Filter wird hier integriert.
- `hindsight-api/hindsight_api/engine/llm_routing.py` — LLM Routing (aus Epic 03). Thalamus Scoring ist ein Small-Tier Task.
- `hindsight-api/hindsight_api/engine/embeddings.py` — Embedding-Generierung. Für Novelty-Check (Similarity gegen bestehende Engrams).
- `hindsight-api/hindsight_api/engine/engram_storage.py` — Engram Storage Service (aus Epic 01). Für Novelty-Query gegen Qdrant.

## Scope

- Thalamus Scoring Logik (4 Dimensionen + Overall)
- Mode-abhängige Gewichtung und Thresholds
- Integration vor der Retain Pipeline
- Novelty-Check via Qdrant Similarity Query

## Nicht in Scope

- Retain Pipeline Umbau (→ Epic 05)
- Session Layer Automatik (→ Epic 06)

## Abhängigkeiten

- Epic 02 (Engram Data Model) — ThalamusScores Modell
- Epic 01 (Storage) — Qdrant für Novelty-Check

## Referenzen

- `concept.md` → Abschnitt 5 (Thalamus Filter)
- `engram_architecture_complete.md` → Kapitel 2 (Thalamus Filter)

## Stories

1. [Thalamus Scoring Engine](story-01-scoring-engine.md)
2. [Retain Pipeline Integration](story-02-pipeline-integration.md)
