# Story 01 — Sessions-Alive Taktgeber

## User Story

Als System soll das Engram-Aging auf `sessions_alive` basieren statt auf `op_count` oder `cycles_alive`, damit der natürliche Arbeitsrhythmus (Sessions) den Zerfall steuert und nicht die Anzahl der Retain-Operationen.

## Kontext

Das bisherige Aging basiert auf `op_count` (Bank-Operationen, inkrementiert nur bei Retain) bzw. `ncr_cycles` (NCR-Durchläufe). Problem: `op_count` steigt nur bei Retain, nicht bei Recall — ein Engram das ständig abgerufen aber nie mit neuen Daten ergänzt wird, altert nicht. `sessions_alive` ist der natürliche Taktgeber: jede Session ist eine Arbeitseinheit, und ein Engram beweist seinen Wert dadurch dass es über mehrere Sessions hinweg abgerufen wird.

## Bestehende Codebasis

- **Bank Model:** `models.py` → `Bank` Klasse mit `op_count`. Kein `session_count` Feld.
- **Engram Dictionary:** PostgreSQL `engram_dictionary` → `created_at` (Timestamp), kein `created_at_session`.
- **Session Layer:** `engine/session_manager.py` → Session open/close Events.
- **NCR Decay:** `engine/consolidation/ncr_decay.py` → Nutzt `bank.op_count - engram.created_at_op` als Alter.
- **Scoring:** `engine/consolidation/scoring.py` → `compute_recount_score(access_count, cycles_alive)`.

## Akzeptanzkriterien

- [ ] Neues Feld `session_count` auf Bank Model (Integer, default 0)
- [ ] `session_count` wird bei Session-Close inkrementiert (einmal pro Session, nicht pro Operation)
- [ ] Neues Feld `created_at_session` auf Engram Dictionary (Integer, Session-Zähler bei Erstellung)
- [ ] `sessions_alive` berechenbar: `bank.session_count - engram.created_at_session`
- [ ] Bestehende Engrams erhalten Migration: `created_at_session = 0` (alle starten beim gleichen Alter)
- [ ] `op_count` bleibt bestehen (Backwards-Kompatibilität), wird aber nicht mehr für Aging verwendet

## Tasks

- [ ] **T1 — Bank Model erweitern:** Neues Feld `session_count: int = 0` auf `Bank` Model in `models.py`. Alembic Migration für PostgreSQL Tabelle `banks`. Default 0.
- [ ] **T2 — Session-Close Hook:** In `engine/session_manager.py` bei Session-Close: `bank.session_count += 1`. Muss atomar sein (SQL `UPDATE banks SET session_count = session_count + 1 WHERE id = ?`). Darf nur einmal pro Session feuern, nicht bei Reconnects.
- [ ] **T3 — Engram Dictionary erweitern:** Neues Feld `created_at_session: int` auf `engram_dictionary` Tabelle. Alembic Migration. Bei `create_engram()` wird `created_at_session = bank.session_count` gesetzt.
- [ ] **T4 — sessions_alive Property:** Computed Property oder Helper-Funktion: `sessions_alive(bank, engram) → bank.session_count - engram.created_at_session`. Minimum 0 (Guard gegen Race Conditions).
- [ ] **T5 — Datenmigration:** Alembic Data-Migration: Alle existierenden Engrams bekommen `created_at_session = 0`. Alle existierenden Banks bekommen `session_count` = Anzahl abgeschlossener Sessions (Query auf Session-Tabelle) oder 0 als Fallback.
- [ ] **T6 — Unit Tests:** session_count Inkrement bei Session-Close. created_at_session korrekt gesetzt bei Engram-Erstellung. sessions_alive Berechnung korrekt. Idempotenz: Doppelter Close inkrementiert nicht doppelt.
