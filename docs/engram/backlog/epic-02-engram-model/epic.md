# Epic 02 — Engram Data Model

> Zentrale Wissenseinheit. Ersetzt das flache Fact-Modell.

## Ziel

Engram als neues Kern-Datenmodell einführen. ExtractedFact und ProcessedFact um Tags und Thalamus-Scores erweitern. Episode und Session als Hilfsmodelle für Input und Steuerung definieren.

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/engine/retain/types.py` — ExtractedFact, ProcessedFact, RetainContent Dataclasses. Hier werden Tags + Thalamus-Scores ergänzt.
- `hindsight-api/hindsight_api/engine/response_models.py` — MemoryFact, RecallResult, ReflectResult Pydantic Models. FullEngram aus Epic 01 Story 04 lebt hier.
- `hindsight-api/hindsight_api/models.py` — SQLAlchemy ORM (MemoryUnit). EngramDictionary aus Epic 01 Story 03 lebt hier.
- `hindsight-api/hindsight_api/engine/retain/fact_extraction.py` — LLM-Prompt für Fact Extraction. Muss Tags + Thalamus-Scores im Output-Format ergänzen.
- `hindsight-api/hindsight_api/engine/memory_engine.py` — MemoryEngine. Nutzt ExtractedFact/ProcessedFact durchgängig.

**Bestehende Modelle die erweitert werden:**
- `ExtractedFact` — Dataclass mit: fact_text, fact_type, entities, occurred_start/end, where, causal_relations, content_index, chunk_index, context, mentioned_at, metadata
- `ProcessedFact` — Erweitert ExtractedFact um: embedding (384-dim), resolved EntityRefs, chunk_id, document_id
- `MemoryFact` — Pydantic Response-Modell für Recall: id, text, fact_type, entities, context, timestamps, activation score

## Scope

- ExtractedFact/ProcessedFact um Tags + Thalamus-Scores erweitern
- Engram als eigenständiges Pydantic Model definieren
- Episode und Session als neue Modelle
- Fact Extraction LLM-Prompt anpassen (Tags + Thalamus-Scores extrahieren)

## Nicht in Scope

- Thalamus Filter Logik (→ Epic 04)
- Retain Pipeline Umbau (→ Epic 05)
- Session Layer Implementierung (→ Epic 06)

## Abhängigkeiten

- Epic 01 (Storage Architecture) — FullEngram Model, EngramDictionary Table

## Referenzen

- `concept.md` → Abschnitt 4 (Engram Data Model)
- `engram_architecture_complete.md` → Kapitel 3 (Memory Engrams)

## Stories

1. [Engram Pydantic Models](story-01-engram-models.md)
2. [Episode & Session Models](story-02-episode-session-models.md)
