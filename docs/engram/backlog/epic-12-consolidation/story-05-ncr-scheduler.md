# Story 05 — NCR Scheduler & Orchestration

## User Story

Als System soll der NCR als orchestrierter Batch-Prozess laufen der die 3 Phasen sequenziell ausführt.

## Kontext

Der NCR läuft periodisch (Default: alle 24h, konfigurierbar). Er orchestriert die 3 Phasen in Reihenfolge: Decay → Strengthen → Schema Compression. Jede Phase kann unabhängig fehlschlagen ohne die anderen zu blockieren. Logging und Metrics für Monitoring.

## Bestehende Codebasis

- **DecayProcessor:** `consolidation/ncr_decay.py` (aus Story 02)
- **StrengthenProcessor:** `consolidation/ncr_strengthen.py` (aus Story 03)
- **SchemaProcessor:** `consolidation/schema_processor.py` (aus Story 04)
- **Task Backend:** Hindsight hat Background-Task Infrastruktur.

## Akzeptanzkriterien

- [x] NCR Orchestrator führt 3 Phasen sequenziell aus
- [x] Jede Phase ist fault-tolerant (Fehler → Log + Continue)
- [x] Scheduling: Periodisch konfigurierbar (Default: 24h)
- [x] Manueller Trigger möglich (API Endpoint)
- [x] Locking: Nur ein NCR gleichzeitig (Advisory Lock)
- [x] Metrics: Dauer pro Phase, Anzahl verarbeiteter Engrams, Fehler

## Tasks

- [x] **T1 — NCR Orchestrator:** `engine/consolidation/ncr_orchestrator.py`. Klasse `NCROrchestrator(decay, strengthen, schema, engram_repo)`. Methode `async run(bank_id) → NCRReport`. Sequenz: Consolidation 1 → Phase 1 (Decay) → Phase 2 (Strengthen) → Phase 3 (Schema). Jede Phase in try/except mit Error-Logging.
- [x] **T2 — NCRReport Dataclass:** `NCRReport(bank_id, started_at, completed_at, phase1: DecayResult, phase2: StrengthenResult, phase3: SchemaResult, errors: list[str])`. Wird geloggt und optional via API abrufbar.
- [x] **T3 — Advisory Lock:** PostgreSQL Advisory Lock vor NCR-Start. Verhindert parallele NCR-Runs. Pattern aus Hindsight Migrations (die nutzen bereits Advisory Locks). Lock-Release im finally-Block.
- [x] **T4 — Periodic Scheduler:** Konfigurierbar via HindsightConfig: `NCR_INTERVAL_HOURS=24`, `NCR_ENABLED=true`. Background Task der periodisch `orchestrator.run()` aufruft. Graceful Shutdown bei Application-Stop.
- [x] **T5 — Manual Trigger API:** FastAPI Endpoint: `POST /api/banks/{bank_id}/ncr/trigger`. Startet NCR manuell. Gibt NCRReport zurück. Rate-Limited: Max 1 manueller Trigger pro Stunde.
- [x] **T6 — Unit Tests:** Orchestrator führt alle 3 Phasen aus. Fehler in Phase 1 blockiert nicht Phase 2. Advisory Lock verhindert parallele Runs. NCRReport vollständig befüllt. Manueller Trigger funktioniert.
