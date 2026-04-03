# Story 03 — Prediction Error Detection & Feedback

## User Story

Als System soll erkannt werden wenn die konstruierte Antwort von der aktuellen Erwartung abweicht, und dieser Prediction Error als Feedback in Session Mode + Reconsolidation fließen.

## Kontext

Biologisch: Prediction Errors treiben Lernen an. Wenn das Ergebnis von der Erwartung abweicht, werden beteiligte Synapsen modifiziert. Im System: Session hat eine `current_expectation`. Wenn die ConstructedAnswer davon signifikant abweicht → Prediction Error → Feedback an (1) Session Mode Shift (→ Validation), (2) Reconsolidation Priority (beteiligte Engrams werden geflaggt), (3) Thalamus Surprise Score Boost für zukünftige ähnliche Inputs.

## Bestehende Codebasis

- **Session:** `session/session_manager.py` (aus Epic 06) — `current_expectation`, `process_signal(ModeSignal.PREDICTION_ERROR)`.
- **Prediction Error Registry:** `reflect/prediction_error_registry.py` (aus Epic 10) — `flag_prediction_error(engram_id, context)`.
- **ConstructedAnswer:** `constructive/models.py` (aus Story 01) — Facts mit Engram-IDs.
- **ConstructionPipeline:** `constructive/pipeline.py` (aus Story 02) — Liefert ConstructedAnswer.

## Akzeptanzkriterien

- [ ] Prediction Error Detection vergleicht ConstructedAnswer mit current_expectation
- [ ] LLM-basierter Vergleich (nicht nur String-Match)
- [ ] Bei Error: Session Mode → Validation Shift (via ModeSignal)
- [ ] Bei Error: Beteiligte Engrams im Prediction Error Registry flaggen
- [ ] Error-Stärke: Scalar 0-1 (leichte Abweichung vs. fundamentaler Widerspruch)
- [ ] Ohne current_expectation: Kein Prediction Error Check

## Tasks

- [ ] **T1 — PredictionErrorDetector:** `engine/constructive/prediction_error.py`. Klasse `PredictionErrorDetector(llm)`. Methode `detect(answer: ConstructedAnswer, expectation: str) → PredictionError | None`. LLM-Call (Small-Tier): "Compare this answer with the expectation. Is there a significant deviation? Rate 0-1."
- [ ] **T2 — PredictionError Dataclass:** `PredictionError(severity: float, description: str, conflicting_fact_ids: list[str], expected_summary: str, actual_summary: str)`. Severity 0-0.3 → Minor, 0.3-0.7 → Moderate, 0.7-1.0 → Major.
- [ ] **T3 — Session Mode Feedback:** Bei PredictionError mit severity ≥ 0.3: `session_manager.process_signal(ModeSignal.PREDICTION_ERROR)` → Automatischer Shift zu Validation (wenn kein expliziter Mode gesetzt). Error-Details im Mode-History Log.
- [ ] **T4 — Reconsolidation Flagging:** Bei PredictionError: Alle `conflicting_fact_ids` → `prediction_error_registry.flag_prediction_error()`. Diese Engrams bekommen Priorität bei nächster Reconsolidation (Epic 10).
- [ ] **T5 — Integration in recall_async:** In `memory_engine.py`: Nach Construction → Prediction Error Check wenn `session.current_expectation` gesetzt. Error-Ergebnis im ConstructedAnswer Metadata speichern.
- [ ] **T6 — Unit Tests:** Error Detection bei Abweichung. Kein Error bei Übereinstimmung. Severity-Klassifikation. Session Mode Shift bei Error. Engram-Flagging im Registry. Ohne Expectation → Kein Check.
