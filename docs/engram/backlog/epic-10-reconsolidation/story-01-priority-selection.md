# Story 01 — Priority-basierte Engram Selection (RF1)

## User Story

Als System soll Reconsolidation alle Engram-Typen betreffen, mit einer Prioritätsreihenfolge die schwache und fehlerhafte Engrams bevorzugt.

## Kontext

Hindsight reconsolidiert nur Opinions. Wir erweitern auf alle Engram-Typen und führen eine dreistufige Priorität ein: (1) Schwache Engrams zuerst — sie sind fragiler und profitieren am meisten von Reconsolidation, (2) Prediction-Error Engrams — sie haben sich bei Recall als fehlerhaft/veraltet erwiesen, (3) Disposition-abhängig — Agent-Persönlichkeit kann bestimmte Engrams bevorzugen.

## Bestehende Codebasis

- **reflect_async:** `memory_engine.py` — Filtert auf `fact_type='opinion'`. Sucht Entities → LLM Reinforcement.
- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — Strength, Tags.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `reconsolidation_level: minimal/moderate/schema_update/aggressive`.

## Akzeptanzkriterien

- [x] Reconsolidation nicht mehr auf Opinions beschränkt — alle Engram-Typen
- [x] Priority Queue: Schwache Engrams (strength < 0.3) → Prediction-Error Engrams → Rest
- [x] Budget-System: Max N Engrams pro Reconsolidation-Zyklus (Mode-abhängig)
- [x] Reconsolidation-Level steuert Aggressivität: Minimal → nur kritische Updates, Aggressive → auch spekulative Updates
- [x] Prediction-Error Flag: Engrams die bei Recall einen Widerspruch erzeugt haben werden geflaggt

## Tasks

- [x] **T1 — Prediction Error Registry:** Neues Modul `engine/reflect/prediction_error_registry.py`. In-Memory Registry (pro Session) das Engram-IDs speichert die einen Prediction Error verursacht haben. `flag_prediction_error(engram_id, error_context: str)`. Wird von Constructive Memory (Epic 11) befüllt, aber die Datenstruktur wird jetzt angelegt.
- [x] **T2 — Priority Queue:** `engine/reflect/reconsolidation_queue.py`. Funktion `build_reconsolidation_queue(engrams: list[FullEngram], prediction_errors: list[str], mode_config: ModeConfig) → list[FullEngram]`. Sortierung: (1) Prediction-Error Engrams, (2) Strength ascending (schwächste zuerst), (3) Last-accessed ascending (älteste zuerst). Budget: Minimal→5, Moderate→15, Schema_update→25, Aggressive→50.
- [x] **T3 — reflect_async erweitern:** In `memory_engine.py`: `reflect_async()` nicht mehr auf Opinions filtern. Stattdessen: Priority Queue bauen → Top-N Engrams → LLM Reconsolidation. Session-ID als optionaler Parameter (wie in Epic 06).
- [x] **T4 — Strength Update nach Reconsolidation:** Nach LLM-Reconsolidation: Engram Strength anpassen. Bestätigt (LLM sagt "korrekt") → Strength +0.1. Modifiziert → Strength bleibt gleich (Content wurde aktualisiert). Widerspruch erkannt → Strength -0.2.
- [x] **T5 — Unit Tests:** Priority Queue Sortierung. Budget-Limits pro Level. Strength Update nach Reconsolidation. Prediction Error Engrams bekommen Priorität. Alle Engram-Typen werden berücksichtigt.
