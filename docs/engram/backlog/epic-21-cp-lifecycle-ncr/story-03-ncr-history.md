# Story 03 — NCR History Persistence

## User Story

Als System will ich NCR-Run-Reports persistent speichern, damit das NCR Dashboard eine History der letzten Runs anzeigen kann, auch nach Server-Neustart.

## Kontext

Der NCR Orchestrator (`ncr_orchestrator.py`) führt die 4-Phase Pipeline aus und liefert einen Report als Return-Value. Dieser Report wird aktuell nicht persistiert — nach dem API-Response ist er weg. Für ein Dashboard brauchen wir eine `ncr_runs` Tabelle in PostgreSQL und einen History-Endpoint.

## Bestehende Codebasis

- **NCR Orchestrator:** `hindsight_api/engine/ncr/ncr_orchestrator.py` — `run_ncr()` liefert Report Dict mit: consolidation, decay, strengthen, schema, errors, duration.
- **NCR Trigger Endpoint:** `POST /v1/default/banks/{bank_id}/ncr/trigger` in `http.py` — triggert NCR, liefert Report als Response.
- **PostgreSQL:** Verwaltet über Alembic Migrations.

## Akzeptanzkriterien

- [x] Neue Tabelle `ncr_runs` mit: run_id, bank_id, started_at, completed_at, duration_seconds, trigger (manual/scheduled), consolidation_stats, decay_stats, strengthen_stats, schema_stats, promotion_stats, errors
- [x] Jeder NCR-Run wird automatisch persistiert (sowohl manuell getriggert als auch scheduled via `trigger` Parameter)
- [x] `GET /v1/default/banks/{bank_id}/ncr/history?limit=20` liefert die letzten Runs (limit 1–100)
- [x] Trigger-Endpoint liefert weiterhin den Report als Response (Backward Compatible — Response-Shape unverändert)
- [x] Migration ist Alembic-basiert (`a2b3c4d5e6f7_add_ncr_runs_table.py`, down_revision → `f0a1b2c3d4e5`)

## Tasks

- [x] **T1 — DB Model & Migration** — `NCRRun` SQLAlchemy-Model in `models.py` angehängt (UUID run_id mit `gen_random_uuid()`, FK auf `banks.bank_id` CASCADE, CheckConstraint `trigger IN ('manual','scheduled')`, Index `idx_ncr_runs_bank_started`). Alle Phase-Stats als JSONB. Alembic-Migration `a2b3c4d5e6f7_add_ncr_runs_table.py` folgt dem `f0a1b2c3d4e5_add_working_memory` Pattern (Multi-Schema via `op.get_context().config.get_main_option('target_schema')`, up+down implementiert).

- [x] **T2 — NCR Orchestrator: Persistence Hook** — `NCROrchestrator.run()` um `trigger: Literal["manual","scheduled"] = "manual"` Parameter erweitert. Neue Methode `_persist_report()` serialisiert Phasen via `dataclasses.asdict()` + `json.dumps(default=str)` und INSERTed mit `$N::jsonb`-Cast (Pattern aus `working_memory_repo.py`). Aufruf im `finally`-Block der `run()`-Methode (nach `completed_at`-Set, nach lock-release) **und** auf dem lock-already-held-Frühabbruch-Pfad — beide in try/except eingepackt, Fehler landen im Warning-Log ohne den NCR-Run zu blockieren. `NCRScheduler._loop()` nutzt `trigger="scheduled"`. 53 bestehende NCR-Unit-Tests grün.

- [x] **T3 — History Endpoint** — `api_ncr_history` Handler in `http.py` mit `limit: int = Query(default=20, ge=1, le=100)`. Pydantic-Models `NCRRunHistoryItem` und `NCRHistoryResponse`. SQL: `SELECT ... FROM ncr_runs WHERE bank_id = $1 ORDER BY started_at DESC LIMIT $2`. Robuster `_parse_jsonb`-Helper für asyncpg-Ergebnisse (dict oder String). Authentication via `_authenticate_tenant`. `api_ncr_trigger` ruft `run(bank_id, trigger="manual")`.

- [x] **T4 — CP API Routes** — `src/app/api/ncr/trigger/route.ts` (POST, `bank_id` aus JSON-Body, AbortController mit 5-min-Timeout + 504-Response bei Timeout, leitet Dataplane-Errors mit Original-Status-Code weiter). `src/app/api/ncr/history/route.ts` (GET, `bank_id` + `limit` aus Query, `cache: "no-store"`). Direct-fetch-Pattern wie `/api/config` und Story 01.

- [x] **T5 — CP Client erweitern** — `src/lib/api.ts`: Getrennte Interfaces — `NCRReport` (flache Shape für Trigger-Response mit phase1_decay/phase2_strengthen/phase3_schema) und `NCRRunHistoryItem` (JSONB-Shape mit `*_stats`-Feldern, passend zum History-Endpoint). `NCRHistoryResponse` als `{runs: NCRRunHistoryItem[]}`. Methoden `triggerNCR(bankId)` und `getNCRHistory(bankId, limit?)` in `ControlPlaneClient`.
