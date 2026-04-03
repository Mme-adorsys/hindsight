# Story 02 — Construction Pipeline

## User Story

Als System soll nach dem Retrieval eine Construction Pipeline laufen die aus den rohen Ergebnissen eine ConstructedAnswer mit Facts, Inferences und Gaps erzeugt.

## Kontext

Die Construction Pipeline sitzt zwischen Retrieval und API-Response. Sie nimmt die ScoredResults, den Working Context, und den Session Mode, und baut daraus eine ConstructedAnswer. Der LLM identifiziert Inferenzen und Lücken. Der Mode steuert wie kreativ die Construction ist.

## Bestehende Codebasis

- **recall_async:** `memory_engine.py` — Liefert ScoredResult Liste.
- **Working Context:** `session/working_context.py` (aus Epic 08) — Episodic Buffer, Inference Layer.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `construction_style`.
- **LLM Wrapper:** `engine/llm_wrapper.py` — LLM-Aufrufe.
- **LLM Routing:** `engine/llm_routing.py` (aus Epic 03) — Construction = Medium-Tier Task.

## Akzeptanzkriterien

- [ ] Construction Pipeline verarbeitet Retrieval-Ergebnisse + Working Context → ConstructedAnswer
- [ ] LLM identifiziert Inferenzen aus den abgerufenen Facts
- [ ] LLM identifiziert Gaps (fehlende Informationen)
- [ ] Mode-Einfluss: Precision → wenig Inferenz, Exploration → mehr Inferenz, Analogy → Cross-Domain, Validation → Gegenargumente
- [ ] Ohne Session: Keine Construction (flache Ergebnisse wie bisher)

## Tasks

- [ ] **T1 — ConstructionPipeline Klasse:** `engine/constructive/pipeline.py`. Klasse `ConstructionPipeline(llm, mode_config)`. Methode `construct(scored_results: list[ScoredResult], query: str, working_context: WorkingContext | None) → ConstructedAnswer`.
- [ ] **T2 — Fact Extraction Phase:** Scored Results → ConstructedFacts konvertieren. Confidence = combined_score. Traversal-Source (weak_link) senkt Confidence. Direct vs. Reconstructed Klassifikation.
- [ ] **T3 — Inference Phase:** LLM-Call (Medium-Tier): "Given these facts about '{query}': {facts}. What can be inferred? Also consider: {working_context.inference_layer}." Mode-Prompt: Precision → "Only state what logically follows", Exploration → "Include speculative connections", Analogy → "Draw parallels to other domains", Validation → "What counter-evidence exists?". Parsing: List of Inference objects.
- [ ] **T4 — Gap Detection Phase:** LLM-Call (Small-Tier): "Given these facts and inferences about '{query}', what important information is missing?" Parsing: List of Gap objects mit suggested_query.
- [ ] **T5 — Confidence Aggregation:** `overall_confidence = weighted_mean(fact_confidences) * coverage_factor`. Coverage Factor: 1.0 wenn keine Gaps, sinkt proportional zur Gap-Relevanz. Mode-Einfluss dokumentieren.
- [ ] **T6 — Working Context Update:** Nach Construction: Confirmed Inferences → Working Context Inference Layer. Gaps → können als neue Goals in den Goal Stack fließen (optional).
- [ ] **T7 — Integration in recall_async:** In `memory_engine.py`: Nach Retrieval und Scoring → ConstructionPipeline aufrufen wenn Session aktiv. ConstructedAnswer in RecallResultModel einsetzen.
- [ ] **T8 — Unit Tests:** Construction mit Precision Mode (wenig Inferenz). Construction mit Exploration Mode (mehr Inferenz). Gap Detection. Confidence Aggregation. Working Context Update nach Construction.
