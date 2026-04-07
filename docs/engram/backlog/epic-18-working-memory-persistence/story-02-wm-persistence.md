# Story 02 — Working Memory Persistence

## User Story

Als System will ich das Working Memory zwischen Sessions persistieren, damit die nächste Session mit warmem Kontext startet statt bei Null anzufangen.

## Kontext

Das Working Memory hält den Zustand der über eine einzelne Session hinaus relevant ist: welche Ziele verfolgt werden, welche Engrams aktiv waren, welche Inferences bestätigt wurden. Persistenz in PostgreSQL als JSONB ermöglicht flexible Schema-Evolution.

**Priming-Effekt:** Wenn die letzte Session intensiv mit bestimmten Engrams gearbeitet hat, sind diese beim nächsten Session-Start sofort im Focus/Supporting Tier verfügbar — ohne erneuten Recall.

## Bestehende Codebasis

- **WorkingContext:** `engine/session/working_context.py` — Wird zum persistenten WorkingMemory.
- **Engram Dictionary:** `engine/engram_dictionary.py` — PostgreSQL Repository Pattern (als Referenz).

## Akzeptanzkriterien

- [ ] WorkingMemory Dataclass: goal_stack, active_engrams (3 Tiers), confirmed_inferences, session_history (letzte N session_ids + Timestamps)
- [ ] PostgreSQL Tabelle `working_memory`: bank_id (PK), state (JSONB), updated_at
- [ ] Alembic Migration für working_memory Tabelle
- [ ] WorkingMemoryRepository: load(bank_id), save(bank_id, state), exists(bank_id)
- [ ] Serialisierung: WorkingMemory ↔ JSONB (mit Version-Feld für Schema-Migration)
- [ ] Session-Start: load() → WorkingMemory (oder leeres Default wenn nicht vorhanden)
- [ ] Session-Ende: save() → PostgreSQL
- [ ] Periodisches Speichern: alle N Minuten während aktiver Session (Crash-Safety)

## Tasks

- [ ] **T1 — WorkingMemory Dataclass:** Felder: bank_id, goal_stack (list[Goal]), active_engrams (ActiveEngrams mit focus/supporting/peripheral), confirmed_inferences (list[Inference]), session_history (list[SessionRef], max 20), schema_version (int, für Migration). Methoden: to_dict(), from_dict().
- [ ] **T2 — PostgreSQL Tabelle:** Alembic Migration: `working_memory` Tabelle. bank_id VARCHAR PK, state JSONB NOT NULL DEFAULT '{}', schema_version INT DEFAULT 1, updated_at TIMESTAMPTZ. Index auf updated_at.
- [ ] **T3 — WorkingMemoryRepository:** CRUD: load(bank_id) → WorkingMemory, save(bank_id, wm) → None, exists(bank_id) → bool. JSON Serialisierung mit schema_version Check. Upsert-Semantik (INSERT ON CONFLICT UPDATE).
- [ ] **T4 — Session Integration:** SessionManager.create_session(): load WorkingMemory für bank_id. SessionManager.end_session(): save WorkingMemory. Periodischer Save: Timer-basiert (konfigurierbar, Default 5 Minuten).
- [ ] **T5 — Tests:** Persistence Roundtrip (save → load → verify). Leeres Default für neue Banks. Schema-Version Check. Periodischer Save Test. Concurrent-Access Safety (Locking).
