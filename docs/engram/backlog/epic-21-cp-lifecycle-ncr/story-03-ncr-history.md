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

- [ ] Neue Tabelle `ncr_runs` mit: run_id, bank_id, started_at, completed_at, duration_seconds, trigger (manual/scheduled), consolidation_stats, decay_stats, strengthen_stats, schema_stats, errors
- [ ] Jeder NCR-Run wird automatisch persistiert (sowohl manuell getriggert als auch scheduled)
- [ ] `GET /v1/default/banks/{bank_id}/ncr/history?limit=20` liefert die letzten Runs
- [ ] Trigger-Endpoint liefert weiterhin den Report als Response (Backward Compatible)
- [ ] Migration ist Alembic-basiert

## Tasks

- [ ] **T1 — DB Model & Migration** — Neues SQLAlchemy Model `NCRRun` mit allen Feldern. Alembic Migration `add_ncr_runs_table`. Stats-Felder als JSONB (consolidation_stats, decay_stats, strengthen_stats, schema_stats, errors) — flexibel für zukünftige Schema-Änderungen.

- [ ] **T2 — NCR Orchestrator: Persistence Hook** — Nach erfolgreichem NCR-Run den Report in `ncr_runs` speichern. Trigger-Type als Parameter (`manual` vs. `scheduled`). Fehler bei der Persistierung dürfen den NCR-Run selbst nicht blockieren (try/except, log warning).

- [ ] **T3 — History Endpoint** — Neuer Route Handler `GET /v1/default/banks/{bank_id}/ncr/history` in `http.py`. Query-Parameter: `limit` (default 20, max 100). Response: `{ runs: NCRRunResponse[] }`. Sortiert nach `started_at DESC`.

- [ ] **T4 — CP API Routes** — Zwei Routes: `src/app/api/ncr/trigger/route.ts` (POST, proxy), `src/app/api/ncr/history/route.ts` (GET, proxy mit bank_id und limit Parameter).

- [ ] **T5 — CP Client erweitern** — In `src/lib/api.ts`: `triggerNCR(bankId: string): Promise<NCRReport>`, `getNCRHistory(bankId: string, limit?: number): Promise<{ runs: NCRReport[] }>`. Typed Interfaces für NCRReport.
