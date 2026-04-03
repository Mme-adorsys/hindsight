# Epic 10 — Reflect & Reconsolidation

> RF1-RF4: Priority-basierte Reconsolidation, Retrieval-Cost Optimierung, Semantic Trigger, Disposition-Einfluss.

## Ziel

Hindsight's Reflect modifiziert nur Opinions bei exaktem Entity-Match. Wir erweitern Reconsolidation auf ALLE Engram-Typen, führen Priority-basierte Auswahl ein (schwache Engrams zuerst, dann Prediction-Error-Engrams), ersetzen den exakten Entity-Match durch Semantic Trigger, und lassen die Agent-Disposition die Reconsolidation beeinflussen.

## Bestehende Codebasis (Hindsight)

- **reflect_async:** `engine/memory_engine.py` — Opinion Reinforcement. Sucht Facts mit matchenden Entities, ruft LLM auf um Meinung zu aktualisieren.
- **reflect/ Ordner:** Wenn vorhanden — Reflect Pipeline Dateien.
- **Bank Disposition:** `retain/bank_utils.py` — BankProfile mit Disposition Dict.
- **Qdrant Client:** `engine/qdrant_client.py` (aus Epic 01) — Für Semantic Similarity Check.
- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — Strength, Tags, Thalamus Scores.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `reconsolidation_level`.

## Scope

- RF1: Priority-basierte Reconsolidation (Strength → Prediction Error → Disposition)
- RF2: Retrieval-Cost Optimierung (Qdrant statt 12 SQL Queries)
- RF3: Semantic Trigger (Cosine Similarity ≥ 0.6) zusätzlich zu Entity-Match
- RF4: Disposition-Einfluss auf Reconsolidation-Ergebnis

## Abhängigkeiten

- Epic 02 (Engram Model) — FullEngram mit Strength
- Epic 07 (Search & Retrieval) — EngramRetriever für Semantic Search

## Stories

1. [Priority-basierte Engram Selection](story-01-priority-selection.md) (RF1)
2. [Semantic Trigger & Qdrant Integration](story-02-semantic-trigger.md) (RF2 + RF3)
3. [Disposition-aware Reconsolidation](story-03-disposition-reconsolidation.md) (RF4)
4. [Reconsolidation Integration Tests](story-04-reconsolidation-tests.md)
