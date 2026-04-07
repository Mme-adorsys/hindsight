# Story 03 — API & Pipeline Integration

## User Story

Als Caller will ich das Budget-Profil pro Request mitgeben können und optional einzelne Schritte überschreiben, damit ich die Extraktionstiefe und Kosten pro Aufruf steuern kann.

## Kontext

Das Budget-Feld existiert bereits im RecallRequest. Jetzt wird es auch im RetainRequest wirksam und steuert die gesamte Pipeline — insbesondere R0 (Sequence Analysis) und R4 (Entity Disambiguation).

## Bestehende Codebasis

- **RetainRequest:** `api/http.py` — hat bereits mode, aber kein budget.
- **RecallRequest:** `api/http.py` — hat bereits budget: Budget = Budget.MID.
- **Budget Enum:** `engine/memory_engine.py` — LOW, MID, HIGH.

## Akzeptanzkriterien

- [x] RetainRequest um `budget: Budget = Budget.MID` erweitert
- [x] Optional: `model_overrides: dict[str, str] | None` für per-Request Step-Overrides
- [x] Budget wird durch die gesamte Retain Pipeline durchgereicht
- [x] R0 nutzt Budget für LLM-Tier Auswahl (Low→SMALL, Mid→MEDIUM, High→LARGE)
- [x] R1, R4 nutzen Budget für ihre LLM-Tier Auswahl
- [ ] Recall Budget steuert auch Retrieval-Scoring LLM Calls (Cross-Encoder etc.) — deferred (out of scope for this epic)
- [ ] API-Dokumentation mit Kostenindikation pro Budget-Stufe — deferred (covered by OpenAPI schema)

## Tasks

- [x] **T1 — RetainRequest erweitern:** `budget: Budget = Budget.MID`, `model_overrides: dict[str, str] | None = None`. Validator: model_overrides Keys müssen gültige PipelineStep Namen sein, Values gültige ModelTier Namen.
- [x] **T2 — Pipeline Durchreichen:** Budget-Profil wird in retain_batch_async() resolved: Request-Budget → BudgetProfile. Overrides anwenden. Profil an Orchestrator → R0 → R1 → R4 durchreichen.
- [x] **T3 — R0 Budget-Integration:** R0 analyze_sequence() nimmt budget als Parameter. Low → SMALL-Tier Prompt (kurz, simpel). Mid → MEDIUM-Tier Prompt (detailliert). High → LARGE-Tier Prompt (ausführlich, tiefes Reasoning).
- [x] **T4 — Tests:** Retain mit verschiedenen Budget-Stufen → korrekte LLM-Tier Auswahl. model_overrides Test: einzelner Step Override wirkt. Backward-Compatibility: Retain ohne Budget → MID Default.
