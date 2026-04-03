# Epic 11 — Constructive Memory

> Retrieval als Rekonstruktion: {facts, inferences, gaps}. Prediction Error Detection.

## Ziel

Das System gibt nicht einfach gespeicherte Fakten zurück — es konstruiert eine Antwort aus Fragmenten, ergänzt Lücken durch Inferenz, und markiert Unsicherheiten. Das Retrieval Payload ist `ConstructedAnswer {facts, inferences, gaps, confidence, mode_influence}`. Der Mode beeinflusst die Construction: Precision → konservativ, Exploration → kreativ, Analogy → cross-domain, Validation → evidenz-basiert. Prediction Error Detection vergleicht die Antwort mit `Session.current_expectation`.

## Bestehende Codebasis

- **recall_async:** `memory_engine.py` — Liefert `RecallResultModel` mit flacher MemoryFact-Liste. Keine Inferenz, keine Gap-Erkennung.
- **Working Context:** `session/working_context.py` (aus Epic 08) — Inference Layer, Episodic Buffer.
- **Session:** `session/session_manager.py` (aus Epic 06) — `current_expectation`, Mode, Episode Buffer.
- **Prediction Error Registry:** `reflect/prediction_error_registry.py` (aus Epic 10) — Bereit für Flags.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `construction_style`.
- **LLM Routing:** `engine/llm_routing.py` (aus Epic 03) — Construction ist Medium/Large-Tier Task.

## Scope

- ConstructedAnswer Datenmodell
- Construction Pipeline: Facts → Inferences → Gaps
- Mode-abhängige Construction
- Prediction Error Detection
- Feedback Loop: Prediction Error → Session Mode Shift + Reconsolidation Flag

## Abhängigkeiten

- Epic 07 (Search & Retrieval) — Retrieval-Ergebnisse als Input
- Epic 06 (Session Layer) — Mode, current_expectation
- Epic 08 (Working Context) — Inference Layer als Kontext
- Epic 10 (Reconsolidation) — Prediction Error Registry

## Stories

1. [ConstructedAnswer Data Model](story-01-constructed-answer.md)
2. [Construction Pipeline](story-02-construction-pipeline.md)
3. [Prediction Error Detection & Feedback](story-03-prediction-error.md)
