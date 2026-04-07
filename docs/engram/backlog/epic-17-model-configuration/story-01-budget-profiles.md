# Story 01 — Budget-Profile & Defaults

## User Story

Als Betreiber will ich zwischen Low, Medium und High Budget-Profilen wählen können, damit jeder Pipeline-Schritt automatisch das passende Modell zugewiesen bekommt ohne dass ich jeden Schritt einzeln konfigurieren muss.

## Kontext

Das bisherige LLM Routing hat ein starres TASK_TIER_MAPPING (8 Subtasks → SMALL/MEDIUM/LARGE). Budget-Profile erweitern das um eine höhere Abstraktionsebene: der Betreiber wählt "Low Budget" und alle Schritte bekommen automatisch kosteneffiziente Modelle zugewiesen.

## Bestehende Codebasis

- **ModelTier:** `engine/llm_routing.py` — Enum SMALL, MEDIUM, LARGE.
- **TASK_TIER_MAPPING:** `engine/llm_routing.py` — Dict[str, ModelTier], 8 Einträge.
- **Budget:** `engine/memory_engine.py` — Enum LOW, MID, HIGH (existiert bereits für Recall).

## Akzeptanzkriterien

- [ ] BudgetProfile Frozen Dataclass mit Mapping: pipeline_step → ModelTier
- [ ] 3 vordefinierte Profile: LOW_BUDGET, MID_BUDGET, HIGH_BUDGET als Module-Level Konstanten
- [ ] Pipeline-Steps als Enum: R0_SEQUENCE_ANALYSIS, R1_FACT_EXTRACTION, R4_ENTITY_DISAMBIGUATION, THALAMUS, REFLECT, CONSTRUCTIVE_MEMORY, SCHEMA_COMPRESSION, OBSERVATION (+ erweiterbar)
- [ ] Profile-Werte entsprechen der Empfehlungstabelle aus epic.md
- [ ] resolve_llm_config() akzeptiert Budget-Profil als Parameter
- [ ] Profile sind immutable (frozen) — Overrides erzeugen neue Instanzen
- [ ] Dokumentation: welches Profil für welchen Anwendungsfall

## Tasks

- [ ] **T1 — PipelineStep Enum:** Neues Enum `PipelineStep` in llm_routing.py mit allen konfigurierbaren Schritten. Erweiterbar für zukünftige Schritte. Rückwärts-Mapping zu bestehenden TASK_TIER_MAPPING Keys.
- [ ] **T2 — BudgetProfile Dataclass:** Frozen Dataclass mit `steps: dict[PipelineStep, ModelTier]`. Factory-Methode `with_override(step, tier) → BudgetProfile` die eine neue Instanz mit dem Override erzeugt.
- [ ] **T3 — Vordefinierte Profile:** LOW_BUDGET, MID_BUDGET, HIGH_BUDGET als Module-Level Konstanten. Werte gemäß Empfehlungstabelle. Unit-Tests dass alle Steps in jedem Profil definiert sind.
- [ ] **T4 — resolve_llm_config Erweiterung:** Neue Signatur: `resolve_llm_config(step: PipelineStep, budget: BudgetProfile | None = None, ...)`. Budget-Profil hat Vorrang vor TASK_TIER_MAPPING. Fallback: MID_BUDGET wenn kein Profil übergeben.
- [ ] **T5 — Tests:** Profile-Immutability Test. with_override erzeugt neue Instanz. resolve_llm_config mit verschiedenen Profilen. Backward-Compatibility: resolve_llm_config ohne Profil verhält sich wie bisher.
