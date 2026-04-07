# Refactoring — MemoryEngine Decomposition

## User Story

Als Entwickler will ich, dass `memory_engine.py` von einem 5000-Zeilen God Object zu einer schlanken Fassade wird, die an spezialisierte Orchestratoren delegiert — damit Cross-Component Bugs vermieden werden und neue Features (M5+) sauber integrierbar sind.

## Kontext

`memory_engine.py` hat aktuell **5004 Zeilen**, **82 Funktionen/Methoden**, davon **65 auf der `MemoryEngine`-Klasse**. Die Datei enthält 6 verschiedene Domänen (Retain, Recall, Reflect, CRUD, Entity, Bank) in einer einzigen Klasse. Das hat in M3 und M4 wiederholt zu P1-Bugs geführt — insbesondere bei Cross-Component-Integration (PE→Reconsolidation Feedback Loop, Weak-Link Boost, Co-Activation Caps).

**Ursache:** Jedes Epic lagert seine Logik sauber in Module aus (`reflect/`, `constructive/`, `search/`, `session/`, `retain/`), aber die Orchestrierung — das Zusammenschalten der Module — bleibt komplett in `memory_engine.py`. Neue Features fügen 200-400 Zeilen hinzu, ohne dass alte entfernt werden.

**Ziel:** `MemoryEngine` wird zur **schlanken Fassade** (~400-500 Zeilen). Infrastructure-Aufbau (Pool, Embeddings, LLM Registry, Config) wandert in `EngineContext.create()`. Stateless Utilities wandern in `engine/utils.py`. Die Fassade delegiert Operationen an 5 Orchestratoren. Die bestehende API-Schicht (`routes.py`) ruft weiterhin `MemoryEngine` auf — keine Breaking Changes.

## Architektur nach Refactoring

```
API Layer (routes.py)
    │
    ▼
MemoryEngine (Fassade, ~400-500 Zeilen)
    ├── __init__  → self._ctx = await EngineContext.create(config)
    ├── close, health_check
    ├── _authenticate_tenant
    ├── execute_task (dispatch → Orchestratoren)
    ├── ~65 Delegation-Einzeiler
    │
    ├──→ EngineContext (engine/engine_context.py)
    │       pool, embeddings, cross_encoder, llm_registry, query_analyzer
    │       config, task_backend, operation_validator
    │       create(config) — async Factory (LLM-Config + initialize)
    │       _build_llm_config(tier, config) — generische LLM-Config-Factory
    │       close() — Pool + Embeddings shutdown
    │
    ├──→ engine/utils.py (stateless)
    │       fq_table(), validate_sql_schema(), acquire_with_retry()
    │       Budget enum, utcnow(), tiktoken cache
    │
    ├──→ RetainOrchestrator
    │       retain_async, retain_batch_async, _retain_batch_async_internal
    │       _find_duplicate_facts_batch
    │
    ├──→ RecallOrchestrator
    │       recall_async, _search_with_retries, _filter_by_token_budget
    │       to_tuple_format (+ Construction Pipeline + PE Detection)
    │
    ├──→ ReflectOrchestrator
    │       reflect_async, _reconsolidate_engrams_async
    │       _evaluate_engram_reconsolidation_async
    │       _reinforce_opinions_async, _evaluate_opinion_update_async
    │       _extract_and_store_opinions_async
    │
    ├──→ AdminOperations
    │       list_memory_units, list_documents, get_document, get_chunk
    │       delete_document, delete_memory_unit, delete_bank
    │       get_graph_data, list_banks, get_bank_stats
    │       list_operations, cancel_operation
    │
    └──→ EntityOperations
            get_entity_observations, get_entity_observations_batch
            list_entities, get_entity_state, get_entity
            regenerate_entity_observations, _regenerate_observations_sync
            _handle_regenerate_observations
```

## Regeln

1. **Keine Funktionsänderungen.** Kein neues Feature, kein Bugfix. Pure Extraktion. Die M4-Findings werden separat gefixt.
2. **API-Kompatibilität.** `MemoryEngine` behält alle öffentlichen Methoden-Signaturen. Sie delegieren an den jeweiligen Orchestrator.
3. **Shared Infrastructure.** Pool, Embeddings, LLM Registry, Config, `fq_table()`, `acquire_with_retry()` — werden als Dependencies in die Orchestratoren injiziert (Konstruktor), nicht als Globals.
4. **Tests müssen grün bleiben.** Alle bestehenden Tests müssen unverändert passieren. Keine Test-Änderungen außer Import-Pfad-Anpassungen wenn Orchestratoren direkt getestet werden.
5. **Ein Orchestrator pro Datei.** Jeder Orchestrator ist eine Klasse in einer eigenen Datei im `engine/`-Verzeichnis.
6. **`execute_task` bleibt in der Fassade** und dispatched an die richtigen Orchestratoren (gleiche Logik, nur delegiert).
7. **Bank-Management** (`get_bank_profile`, `update_bank_disposition`, `merge_bank_background`) gehört in `AdminOperations` — es sind reine DB-Operationen.

## Tasks

- [ ] **T1 — EngineContext + Factory:** Neues Modul `engine/engine_context.py`. Dataclass `EngineContext` mit: `pool` (asyncpg.Pool), `embeddings`, `cross_encoder`, `llm_registry`, `query_analyzer`, `config`, `task_backend`, `operation_validator`. Dazu eine **async Factory-Methode** `EngineContext.create(config)` die den gesamten Startup übernimmt:
  - LLM-Config-Aufbau (aktuell 254 Zeilen in `__init__` — 3× identisches Provider-URL-Defaulting für default/retain/reflect → 1× generische Factory-Funktion `_build_llm_config(tier, config)`)
  - `initialize()`-Logik (aktuell 128 Zeilen): Pool-Erstellung, Embeddings-Init, Cross-Encoder-Init, Query-Analyzer-Init, LLM-Verifikation
  - `close()` — Pool + Embeddings shutdown
  - Die Fassade ruft nur noch `self._ctx = await EngineContext.create(config)` auf
  - Alle Orchestratoren bekommen einen `EngineContext` im Konstruktor statt direktem Zugriff auf `self._pool` etc.

- [ ] **T2 — Utility-Modul extrahieren:** Neues Modul `engine/utils.py`. Enthält die aktuell 192 Zeilen Module-Level-Code aus `memory_engine.py`: `fq_table()`, `validate_sql_schema()`, `Budget` Enum, `utcnow()`, tiktoken-Cache-Logik, `acquire_with_retry()`. Diese Funktionen sind bereits stateless — reine Cut-and-paste Extraktion. Imports in `memory_engine.py` und allen Orchestratoren auf `from .utils import ...` umstellen.

- [ ] **T3 — AdminOperations extrahieren:** Neue Datei `engine/admin_operations.py`. Klasse `AdminOperations(ctx: EngineContext)`. Methoden: `list_memory_units`, `list_documents`, `get_document`, `get_chunk`, `delete_document`, `delete_memory_unit`, `delete_bank`, `get_graph_data`, `list_banks`, `get_bank_stats`, `list_operations`, `cancel_operation`, `get_bank_profile`, `update_bank_disposition`, `merge_bank_background`. Cut-and-paste — keine Logikänderung. `MemoryEngine` Methoden werden zu Einzeilern: `return await self._admin.list_memory_units(...)`.

- [ ] **T4 — EntityOperations extrahieren:** Neue Datei `engine/entity_operations.py`. Klasse `EntityOperations(ctx: EngineContext)`. Methoden: `get_entity_observations`, `get_entity_observations_batch`, `list_entities`, `get_entity_state`, `get_entity`, `regenerate_entity_observations`, `_regenerate_observations_sync`, `_handle_regenerate_observations`. Gleiche Mechanik wie T3.

- [ ] **T5 — RetainOrchestrator extrahieren:** Neue Datei `engine/retain_orchestrator.py`. Klasse `RetainOrchestrator(ctx: EngineContext)`. Methoden: `retain_async`, `retain_batch_async`, `_retain_batch_async_internal`, `_find_duplicate_facts_batch`, `_handle_batch_retain`, `_handle_access_count_update`. Diese Methoden nutzen bereits `retain/`-Submodule — der Orchestrator koordiniert nur deren Zusammenspiel.

- [ ] **T6 — RecallOrchestrator extrahieren:** Neue Datei `engine/recall_orchestrator.py`. Klasse `RecallOrchestrator(ctx: EngineContext)`. Methoden: `recall_async`, `_search_with_retries`, `to_tuple_format`, `_filter_by_token_budget`. Enthält auch die Construction Pipeline Integration (Epic 11) und PE Detection. Das ist der größte Block (~1400 Zeilen) und der komplexeste — sorgfältig extrahieren.

- [ ] **T7 — ReflectOrchestrator extrahieren:** Neue Datei `engine/reflect_orchestrator.py`. Klasse `ReflectOrchestrator(ctx: EngineContext)`. Methoden: `reflect_async`, `_reconsolidate_engrams_async`, `_evaluate_engram_reconsolidation_async`, `_handle_reconsolidate_engrams`, `_reinforce_opinions_async`, `_handle_reinforce_opinion`, `_evaluate_opinion_update_async`, `_extract_and_store_opinions_async`, `_handle_form_opinion`. Opinion-Methoden bleiben hier (sie sind Teil des Reflect-Flows).

- [ ] **T8 — MemoryEngine zur Fassade reduzieren:** `memory_engine.py` aufräumen. Bleibt: `__init__` (~30 Zeilen: EngineContext + alle Orchestratoren instanziieren), `close` (delegiert an `self._ctx.close()`), `health_check`, `execute_task` (dispatch, ~78 Zeilen), `_authenticate_tenant`, `_validate_operation`, ~65 Delegation-Einzeiler (~195 Zeilen), synchrone Wrapper `retain()` + `recall()`. **Zielgröße: ~400-500 Zeilen.**

- [ ] **T9 — execute_task Dispatch aktualisieren:** `execute_task()` dispatched task_types an die Orchestratoren: `batch_retain` → `self._retain._handle_batch_retain(...)`, `reconsolidate_engrams` → `self._reflect._handle_reconsolidate_engrams(...)`, `regenerate_observations` → `self._entity._handle_regenerate_observations(...)`, etc. Gleiche Logik, nur delegiert.

- [ ] **T10 — Tests anpassen:** Import-Pfade in bestehenden Tests prüfen. Alle Tests, die `MemoryEngine`-Methoden direkt aufrufen, müssen weiterhin über `MemoryEngine` gehen (Fassade). Falls Tests interne Methoden (z.B. `_find_duplicate_facts_batch`) direkt mocken, müssen die Mock-Pfade auf die neuen Orchestratoren zeigen. **Alle bestehenden Tests müssen grün bleiben.**

- [ ] **T11 — Verification:** `ruff check` + `ruff format` + komplette Test-Suite (Unit + Integration). memory_engine.py ≤ 500 Zeilen. Kein neuer Orchestrator > 1500 Zeilen. Kein öffentliches API-Verhalten geändert.

## Akzeptanzkriterien

- [ ] `memory_engine.py` ≤ 500 Zeilen
- [ ] `engine/engine_context.py` existiert mit async Factory + LLM-Config-Builder
- [ ] `engine/utils.py` existiert mit allen stateless Utility-Funktionen
- [ ] 5 neue Orchestrator-Dateien existieren in `engine/`
- [ ] Alle bestehenden Tests grün (Unit + Integration)
- [ ] `ruff check` + `ruff format` ohne Fehler
- [ ] Keine öffentliche API-Signatur geändert
- [ ] `execute_task` dispatched korrekt an Orchestratoren

## Reihenfolge

Empfohlen: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11

T1 (EngineContext + Factory) zuerst — alle anderen Tasks hängen davon ab. T2 (Utils) direkt danach — wird von allen Orchestratoren importiert. T3+T4 (Admin + Entity) sind die einfachsten Extraktionen und schaffen Vertrauen. T5-T7 (Orchestratoren) sind komplexer. T8 (Fassade) erst nachdem alle Extraktionen stehen. T9+T10+T11 zum Schluss.

## Zeilen-Budget nach Refactoring (geschätzt)

| Datei | Zeilen | Inhalt |
|---|---|---|
| `memory_engine.py` (Fassade) | ~400-500 | init, close, health, auth, dispatch, 65 Einzeiler |
| `engine/engine_context.py` | ~400-450 | Dataclass, Factory, LLM-Config-Builder, initialize, close |
| `engine/utils.py` | ~200 | fq_table, validate_sql, Budget, utcnow, tiktoken, acquire_with_retry |
| `engine/admin_operations.py` | ~600-700 | 16 Admin/Bank Methoden |
| `engine/entity_operations.py` | ~400-500 | 8 Entity Methoden |
| `engine/retain_orchestrator.py` | ~600-800 | 6 Retain Methoden |
| `engine/recall_orchestrator.py` | ~1200-1400 | 4 Recall Methoden + Construction + PE |
| `engine/reflect_orchestrator.py` | ~800-1000 | 9 Reflect/Opinion Methoden |
| **Gesamt** | **~4600-5350** | Gleicher Code, 8 Dateien statt 1 |
