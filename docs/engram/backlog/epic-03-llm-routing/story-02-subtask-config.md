# Story 02 — Per-Subtask LLMConfig (L2)

## User Story

Als System brauche ich die Möglichkeit, innerhalb einer Operation (z.B. Retain) verschiedene LLM-Modelle für verschiedene Subtasks zu nutzen, statt ein Modell für alles.

## Kontext

Hindsight's `config.py` unterstützt Per-Operation Overrides (`RETAIN_LLM_PROVIDER`, `RETAIN_LLM_MODEL`), aber nicht Per-Subtask. Alle Steps in Retain nutzen denselben `retain_llm`. Wir erweitern das Config-System so, dass jeder Subtask sein eigenes Modell konfigurieren kann — mit Fallback auf das Operation-Level und dann auf das Global-Level.

## Bestehende Codebasis

- **Config:** `hindsight_api/config.py` — `LLM_PROVIDER`, `LLM_MODEL` (global). `RETAIN_LLM_PROVIDER`, `RETAIN_LLM_MODEL` (operation-level). Fallback-Kette: Operation → Global.
- **LLM Wrapper:** `hindsight_api/engine/llm_wrapper.py` — Erstellt LLM-Instanz aus Provider + Model + API Key. Muss jetzt mehrere Instanzen pro Operation verwalten können.
- **Nutzung in Retain:** `fact_extraction.py`, `deduplication.py`, `link_creation.py` — bekommen aktuell eine einzelne LLM-Instanz injiziert.
- **Nutzung in Reflect:** `think_utils.py`, `memory_engine.py:reflect_async` — bekommt `reflect_llm`.

## Akzeptanzkriterien

- [ ] Config unterstützt Per-Subtask Env-Vars (z.B. `RETAIN_FACT_EXTRACTION_LLM_MODEL`)
- [ ] 3-stufige Fallback-Kette: Subtask → Operation → Global
- [ ] LLM Wrapper kann mehrere Instanzen basierend auf Tier/Subtask liefern
- [ ] Bestehende Config (Operation-Level) funktioniert weiterhin unverändert
- [ ] Kein Breaking Change für Nutzer die nur globales LLM konfigurieren

## Tasks

- [ ] **T1 — Config-Schema erweitern:** In `hindsight_api/config.py` neues Pattern für Subtask-Level Env-Vars: `{OPERATION}_{SUBTASK}_LLM_PROVIDER`, `{OPERATION}_{SUBTASK}_LLM_MODEL`. Beispiele: `RETAIN_FACT_EXTRACTION_LLM_MODEL`, `RETAIN_DEDUP_LLM_MODEL`, `REFLECT_THINK_LLM_MODEL`. Alle optional — Fallback auf Operation-Level, dann Global.
- [ ] **T2 — LLM Resolver implementieren:** In `hindsight_api/engine/llm_routing.py` (aus Story 01) Funktion `resolve_llm_config(operation, subtask) → LLMConfig`. Liest 3-stufig: Subtask Env-Var → Operation Env-Var → Global Env-Var. Gibt Provider + Model + API Key zurück.
- [ ] **T3 — LLM Registry erstellen:** In `llm_wrapper.py` oder `llm_routing.py`: Cache für LLM-Instanzen. `get_llm(operation, subtask) → LLMInstance`. Erstellt Instanz beim ersten Aufruf, cached für Wiederverwendung. Vermeidet dass für jeden Call eine neue Instanz erstellt wird.
- [ ] **T4 — Retain Pipeline umstellen:** In `retain/orchestrator.py` statt einer `retain_llm` Instanz den LLM Resolver nutzen. Jeder Subtask bekommt sein eigenes `get_llm('retain', 'fact_extraction')` etc. Die einzelnen Module (`fact_extraction.py`, `deduplication.py`, `link_creation.py`) bekommen die aufgelöste LLM-Instanz injiziert — kein Breaking Change in deren Interface.
- [ ] **T5 — Reflect Pipeline umstellen:** In `memory_engine.py:reflect_async` und `think_utils.py` analog: `get_llm('reflect', 'think')`, `get_llm('reflect', 'opinion_extraction')`.
- [ ] **T6 — Tests:** Config-Fallback-Kette testen (Subtask → Operation → Global). LLM Registry Cache testen. Bestehende Config ohne Subtask-Vars muss weiterhin funktionieren.
