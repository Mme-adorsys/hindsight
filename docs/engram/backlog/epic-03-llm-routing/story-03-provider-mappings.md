# Story 03 — Provider-Mappings (L3)

## User Story

Als Operator brauche ich vordefinierte Model-Mappings pro Provider und Tier, damit ich nur den Provider wählen muss und das System automatisch die richtigen Modelle pro Subtask zuweist.

## Kontext

Story 01 definiert welcher Subtask welches Tier braucht. Story 02 macht es konfigurierbar. Story 03 liefert die **sinnvollen Defaults** — damit man nicht 20 Env-Vars setzen muss sondern nur `LLM_PROVIDER=anthropic` und das System weiß: Small=Haiku, Medium=Sonnet, Large=Opus.

## Bestehende Codebasis

- **Provider Support:** `hindsight_api/engine/llm_wrapper.py` — Unterstützt: openai, groq, ollama, lmstudio, anthropic, gemini. Jeder Provider hat unterschiedliche Model-Namen.
- **LLM Routing:** `hindsight_api/engine/llm_routing.py` (aus Story 01+02) — TASK_TIER_MAPPING + resolve_llm_config.

## Akzeptanzkriterien

- [ ] Provider-Tier-Mappings für mindestens Anthropic und OpenAI definiert
- [ ] Wenn nur `LLM_PROVIDER` gesetzt ist, werden automatisch passende Modelle pro Tier gewählt
- [ ] Explizite Subtask-Config überschreibt die Tier-Defaults
- [ ] Mapping ist erweiterbar für neue Provider
- [ ] Dokumentation welches Modell für welchen Tier bei welchem Provider genutzt wird

## Tasks

- [x] **T1 — Provider-Tier-Mapping definieren:** In `hindsight_api/engine/llm_routing.py` Dictionary `PROVIDER_TIER_MODELS: Dict[str, Dict[ModelTier, str]]`. Anthropic: Small → claude-haiku-4-5-20251001, Medium → claude-sonnet-4-6, Large → claude-opus-4-6. OpenAI: Small → gpt-4o-mini, Medium → gpt-4o, Large → gpt-4o (kein stärkeres verfügbar). Groq: Small → llama-3.1-8b, Medium → llama-3.1-70b, Large → llama-3.1-70b. Ollama: konfigurierbar (lokale Modelle variieren).
- [x] **T2 — Auto-Resolution in resolve_llm_config:** Wenn kein explizites Model für einen Subtask konfiguriert ist: Tier aus TASK_TIER_MAPPING holen → Model aus PROVIDER_TIER_MODELS für den aktiven Provider nachschlagen. Fallback-Kette wird: Explizites Subtask-Model → Tier-Default für Provider → Operation-Level Model → Global Model.
- [x] **T3 — Config-Dokumentation:** Markdown-Datei oder ausführlicher Docstring der erklärt: Welche Env-Vars es gibt, wie die Fallback-Kette funktioniert, welche Defaults pro Provider gelten, und wie man für spezielle Subtasks überschreibt.
- [x] **T4 — Tests:** Auto-Resolution für Anthropic (Haiku/Sonnet/Opus). Auto-Resolution für OpenAI (mini/4o). Expliziter Override schlägt Auto-Resolution. Unbekannter Provider fällt auf Global zurück.
