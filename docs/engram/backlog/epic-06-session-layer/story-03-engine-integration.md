# Story 03 — MemoryEngine Integration

## User Story

Als Agent möchte ich bei retain, recall und reflect optional eine Session mitgeben, damit das Memory-System mode-abhängig arbeitet.

## Kontext

Die MemoryEngineInterface hat aktuell keine Session-Parameter. Wir erweitern die Interface-Methoden um einen optionalen `session_id` Parameter. Wenn keine Session übergeben wird, arbeitet das System mit dem Default-Mode (Precision) — volle backward compatibility.

## Bestehende Codebasis

- **MemoryEngineInterface:** `hindsight_api/engine/interface.py` — ABC mit `retain_batch_async(bank_id, contents_dicts, ...)`, `recall_async(bank_id, query, ...)`, `reflect_async(bank_id, ...)`. Kein Session-Parameter.
- **MemoryEngine:** `hindsight_api/engine/memory_engine.py` — Implementierung. ~3500 Zeilen. Alle Methoden nehmen `bank_id` als Routing-Key.
- **SessionManager:** `hindsight_api/engine/session/session_manager.py` (aus Story 02) — Liefert Session-Objekte.
- **ModeConfig:** `hindsight_api/engine/session/mode_config.py` (aus Story 01) — Mode → Config Resolution.
- **API Router:** `hindsight_api/api/` — FastAPI Endpoints die MemoryEngine aufrufen.

## Akzeptanzkriterien

- [x] `retain_batch_async`, `recall_async`, `reflect_async` akzeptieren optionalen `session_id: str | None = None`
- [x] Ohne session_id: Verhalten identisch zu Hindsight (backward compat)
- [x] Mit session_id: ModeConfig wird aus Session resolved und an Pipeline-Schritte durchgereicht
- [x] SessionManager wird als Dependency in MemoryEngine injiziert
- [x] API-Endpoints akzeptieren optionalen `session_id` Header oder Parameter
- [x] Integration Test: retain + recall mit expliziter Session (Precision vs. Exploration) zeigt unterschiedliches Verhalten

## Tasks

- [x] **T1 — Interface erweitern:** In `interface.py`: `session_id: str | None = None` zu `retain_batch_async()`, `recall_async()`, `reflect_async()` hinzufügen. Default None für backward compat.
- [x] **T2 — SessionManager als MemoryEngine Dependency:** In `memory_engine.py`: `SessionManager` als optionaler Constructor-Parameter. `self._session_manager = session_manager or SessionManager()`. Lazy-Init wenn keiner injiziert wird.
- [x] **T3 — Session Resolution Helper:** In `memory_engine.py`: Private Methode `_resolve_session_config(session_id) → ModeConfig`. Wenn session_id → SessionManager.get_session() → get_mode_config(). Wenn kein session_id → Default Precision Config.
- [x] **T4 — Retain Integration:** In `retain_batch_async()`: ModeConfig auflösen. Session an Orchestrator durchreichen (für Embedding-Enrichment Story 02 Epic 05). Thalamus Gate Threshold aus ModeConfig nehmen.
- [x] **T5 — Recall Integration:** In `recall_async()`: ModeConfig auflösen. Strength Pre-Filter aus ModeConfig. Thalamus Boost Dimension. Weak Link Policy. Traversal Depth. Scoring Weights. Alles als Parameter an die Search-Pipeline.
- [x] **T6 — Reflect Integration:** In `reflect_async()`: ModeConfig auflösen. Reconsolidation Level aus ModeConfig bestimmt Aggressivität.
- [x] **T7 — API Endpoint Erweiterung:** In FastAPI Routers: `session_id` als optionaler Query-Parameter oder Header. Wird an MemoryEngine Methoden weitergereicht.
- [x] **T8 — Integration Test:** Test mit 2 Sessions (Precision + Exploration). Gleicher retain-Input → unterschiedliche Thalamus Thresholds angewendet. Gleicher recall-Query → unterschiedliche Strength Pre-Filter und Scoring Weights.
