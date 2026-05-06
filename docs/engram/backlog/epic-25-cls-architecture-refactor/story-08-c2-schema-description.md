# Story 08 — C2 Schema-Description-Generation

## User Story

Als C2-Phase soll ich aus den aggregierten Schema-Properties einen prägnanten Klartext-Satz generieren (via kleinem LLM, Tier SMALL), damit das Schema einen menschenlesbaren `description`-Wert hat. Bei LLM-Ausfall fällt die Pipeline auf eine Template-Description zurück.

## Kontext

Das Schema hat drei Repräsentationen seines Inhalts (siehe Konzept Kapitel 4.2): Centroid (Vektor, "Adresse"), Properties (strukturiert, statistisch aggregiert), Description (Klartext, menschenlesbar). Description ist die einzige LLM-getriebene Stelle in C2 — und reines Data-to-Text, kein Reasoning. Pipeline-Step heißt `consolidation.schema_description` (Tier SMALL), Routing über das bestehende `llm_routing.py` (siehe Epic 03 + Konzept Kapitel 16).

## Bestehende Codebasis

- **LLM Routing:** `engine/llm_routing.py` mit `TASK_TIER_MAPPING`, `PIPELINE_STEP_TASK_KEY`, `LLMRegistry`.
- **LLM Wrapper:** `engine/llm_wrapper.py::LLMProvider.call(messages, ...)`.
- **Property Aggregator:** `engine/consolidation/property_aggregator.py` (aus Story 07).

## Akzeptanzkriterien

- [ ] Neuer Pipeline-Step `consolidation.schema_description` in `TASK_TIER_MAPPING` (Tier SMALL)
- [ ] Eintrag in `PIPELINE_STEP_TASK_KEY` (PipelineStep.SCHEMA_DESCRIPTION)
- [ ] Funktion `generate_schema_description(properties: dict, evidence_count: int) -> str`
- [ ] Prompt: prägnant, 1-Satz-Output, Sprache Deutsch (Default — Bank-Sprache wenn konfiguriert)
- [ ] Template-Fallback bei LLM-Failure: `"{dominant_activity} mit {participant_count} Person(en), {time_window}, {mood}, ~{duration_avg}min"`
- [ ] Unit-Tests mit Mock-LLM

## Tasks

- [ ] **T1 — Pipeline-Step-Eintrag:** In `engine/llm_routing.py` neuen `PipelineStep.SCHEMA_DESCRIPTION = "schema_description"` und Mapping `consolidation.schema_description → SMALL`.
- [ ] **T2 — Description-Generator:** `engine/consolidation/schema_description.py::generate_schema_description(properties, evidence_count)` mit LLM-Call via `LLMRegistry.get_llm("consolidation", "schema_description")`.
- [ ] **T3 — Prompt-Template:** Inline-String oder `engine/prompts/schema_description.md`. Anweisung: prägnant, 1 Satz, kein Reasoning.
- [ ] **T4 — Template-Fallback:** Bei `LLMException` oder Timeout → `_template_description(properties)` aus statisch generierter Format-Logik.
- [ ] **T5 — Pipeline-Integration:** In `c2_pattern_recognition.py` nach Property-Aggregation `generate_schema_description()` aufrufen, Ergebnis ins Schema-Modell schreiben.
- [ ] **T6 — Unit-Tests:** (a) Mock-LLM gibt String zurück → Description gesetzt. (b) LLM wirft Fehler → Template greift. (c) Properties-Edge (alle leer) → fallback gibt leeren String, kein Crash.
