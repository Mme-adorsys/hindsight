# Epic 03 — LLM Routing

> Querschnitt. Parallel zu Epic 01+02 umsetzbar.

## Ziel

Rule-based LLM-Routing einführen: Jeder Subtask im System bekommt ein festes Model-Tier zugewiesen (Small/Medium/Large). Die bestehende LLMConfig um Per-Subtask-Zuweisung erweitern und konkrete Provider-Mappings definieren.

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/config.py` — LLM Config: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`. Per-Operation Overrides: `RETAIN_LLM_*`, `REFLECT_LLM_*`. Aktuell: Ein Modell pro Operation, nicht pro Subtask.
- `hindsight-api/hindsight_api/engine/llm_wrapper.py` — LLM Abstraction Layer. Unterstützt: openai, groq, ollama, lmstudio, anthropic, gemini. `LLM_MAX_CONCURRENT` (32), `LLM_TIMEOUT` (120s).
- `hindsight-api/hindsight_api/engine/retain/fact_extraction.py` — Nutzt LLM für Fact Extraction (aktuell: retain_llm).
- `hindsight-api/hindsight_api/engine/retain/deduplication.py` — Nutzt LLM für Dedup-Checks.
- `hindsight-api/hindsight_api/engine/retain/link_creation.py` — Nutzt LLM für Causal Link Extraction.
- `hindsight-api/hindsight_api/engine/search/think_utils.py` — Nutzt LLM für Reflect.

**Aktuelles Pattern:**
- `config.py` definiert `RETAIN_LLM_PROVIDER`, `RETAIN_LLM_MODEL` etc. als optionale Overrides
- `llm_wrapper.py` erstellt LLM-Instanz basierend auf Config
- Alle Subtasks innerhalb von Retain nutzen denselben `retain_llm`

## Scope

- Task-to-Model-Tier Mapping definieren (L1)
- LLMConfig um Per-Subtask Model Assignment erweitern (L2)
- Konkrete Provider-Mappings pro Tier definieren (L3)

## Nicht in Scope

- Dynamisches Routing (entschieden: rule-based, nicht dynamisch)
- Neue LLM-Provider hinzufügen

## Abhängigkeiten

- Keine (Querschnitt, parallel zu Epic 01+02)

## Referenzen

- `concept.md` → Abschnitt 16 (LLM Routing)
- Memory Bank: "LLM Model Routing Architecture Decision — Rule-based, nicht dynamisch"

## Stories

1. [Task-to-Tier Mapping](story-01-task-tier-mapping.md)
2. [Per-Subtask LLMConfig](story-02-subtask-config.md)
3. [Provider-Mappings](story-03-provider-mappings.md)
