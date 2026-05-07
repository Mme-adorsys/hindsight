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

- [x] `consolidation.schema_description` in `TASK_TIER_MAPPING` (`ModelTier.SMALL`)
- [x] `PipelineStep.SCHEMA_DESCRIPTION` + Eintrag in `PIPELINE_STEP_TASK_KEY` + Default `SMALL` in allen drei Budget-Profilen (LOW/MID/HIGH — Description ist nie Reasoning)
- [x] `generate_schema_description(properties, evidence_count, llm_caller=None) -> str`
- [x] Deutsches 1-Satz-Prompt-Template inline; Bank-Sprache-Konfiguration kommt erst bei tatsächlicher Pipeline-Verdrahtung in C2-Orchestrator (Story 09+)
- [x] Template-Fallback bei `llm_caller=None`, LLM-Exception oder leerer Response — generischer Format-String aus Properties (jeder Type-Mode)
- [x] 17 Unit-Tests mit AsyncMock-LLM

## Tasks

- [x] **T1 — Pipeline-Step:** `engine/llm_routing.py` — `PipelineStep.SCHEMA_DESCRIPTION = "schema_description"`, `PIPELINE_STEP_TASK_KEY[SCHEMA_DESCRIPTION] = "consolidation.schema_description"`, `TASK_TIER_MAPPING["consolidation.schema_description"] = SMALL`, plus Eintrag SMALL in `LOW_BUDGET`, `MID_BUDGET`, `HIGH_BUDGET`.
- [x] **T2 — Generator:** `engine/consolidation/schema_description.py::generate_schema_description(properties, evidence_count, llm_caller)`. **Architektur-Entscheidung:** statt direkter `LLMRegistry`-Abhängigkeit nimmt die Funktion eine injizierte `DescriptionLLMCaller` async-Callable — sauber für Unit-Tests UND Production-Wiring (Caller wired typischerweise ein `LLMRegistry.get_llm(...)`-Wrapper). Constants: `MAX_DESCRIPTION_CHARS = 240`.
- [x] **T3 — Prompt-Template:** Inline-`PROMPT_TEMPLATE` (Deutsch, 1 Satz, kein Reasoning, JSON-Properties + Evidence-Count). Drift-Guard-Test prüft Placeholder-Existenz.
- [x] **T4 — Template-Fallback:** `_template_description(properties, evidence_count)` rendert generisch `"Muster über N Engrams: key1=value1, key2~mean2, key3=min3..max3"`. Triggers: `llm_caller=None`, Exception aus dem Caller, leere/whitespace-only Response.
- [x] **T5 — Pipeline-Integration:** `CreationPayload.description: str = ""` Feld hinzugefügt. Neue async `attach_descriptions(payloads, llm_caller)` in `c2_pattern_recognition.py` — sequenziell (nicht `asyncio.gather`), damit WARN-Logs in Reihenfolge auftreten. `prepare_creation_payloads` (Story 07) bleibt sync und reines Aggregations.
- [x] **T6 — Unit-Tests:** 17 Tests in `tests/test_schema_description.py` (LLM happy-path mit Whitespace-Trim, LLM raises → Template, leere LLM-Response → Template, kein Caller → Template, leere Properties → leerer Output, Zero-Evidence → leerer Output, Truncation auf MAX_DESCRIPTION_CHARS, Template alle 3 Type-Modi, Template-Edge `evidence_count`-only, defensive non-dict skip, Strip-Internals, attach_descriptions LLM/Template/Empty, Prompt-Drift-Guard, Cap-Sanity-Bound).

## Implementation Notes

- **Bewusste Tier-Wahl in HIGH-Budget:** Auch HIGH lässt Schema-Description auf SMALL — pure Data-to-Text, kein analytisches Work; Opus-Tokens hier sind Verschwendung.
- **Sequential vs. Gathered:** `attach_descriptions` läuft sequenziell durch die Payloads. Warum: LLM-Endpoint ist shared, Descriptions sind kurz, und im Fallback-Fall wollen wir die WARN-Logs in Reihenfolge — `gather` würde sie verschachteln.
- **No `text-only` Sprach-Switch:** Das Prompt ist bewusst Deutsch (concept-Sprache des Projekts). Bank-Konfiguration für Multi-Sprache kommt erst, wenn der C2-Orchestrator (Story 09+) den `llm_caller` aus Bank-Config baut.
