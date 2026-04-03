# Story 01 — Consolidation 1: Session → Buffer

## User Story

Als System soll Consolidation 1 Thalamus-gefilterte Facts aus dem Working Memory (PostgreSQL) in den Engram Buffer (Dictionary layer='buffer') überführen.

## Kontext

Consolidation 1 läuft nach jeder Session oder periodisch. Es transformiert kurzfristige memory_units (PostgreSQL) in Engrams im Buffer-Layer. Thalamus-Score bestimmt die initiale Strength. Nur memory_units die den Thalamus-Gate bestanden haben werden konsolidiert (das ist bereits in Epic 04/05 implementiert — hier geht es um den expliziten Layer-Übergang).

## Bestehende Codebasis

- **memory_units:** PostgreSQL Tabelle mit Facts aus Retain.
- **Engram Dictionary:** `engine/engram_repository.py` — `create_engram()` mit `layer='buffer'`.
- **StorageService:** `engine/storage_service.py` (aus Epic 01) — Cross-DB Write.

## Akzeptanzkriterien

- [ ] memory_units die bereits als Engrams existieren (via Retain) bekommen layer='buffer'
- [ ] Initiale Strength basierend auf Thalamus Overall Score: `strength = thalamus_scores.overall * 0.5`
- [ ] Consolidation 1 ist idempotent (kann mehrfach laufen ohne Duplikate)
- [ ] Batch-fähig: Verarbeitet alle pending memory_units seit letztem Run

## Tasks

- [ ] **T1 — Consolidation1 Service:** `engine/consolidation/consolidation1.py`. Klasse `Consolidation1Service(engram_repo, storage_service)`. Methode `run(bank_id) → ConsolidationResult`. Lädt alle memory_units ohne zugeordnetes Engram → Erstellt Engrams mit layer='buffer'.
- [ ] **T2 — Strength Initialization:** Initiale Strength = `thalamus_scores.overall * 0.5 + base_strength`. base_strength = 0.1 (jedes Engram startet mit Mindest-Strength). Cap bei 0.5 (Buffer-Engrams sind initial nie stärker als 0.5).
- [ ] **T3 — Idempotenz:** Tracking: `memory_units.engram_id` FK auf Engram Dictionary. Wenn bereits verknüpft → Skip. Alternativ: `consolidated_at` Timestamp auf memory_units.
- [ ] **T4 — Batch Processing:** Verarbeitet in Batches von 100. Timeout nach 5 Minuten (konfigurierbar). Logging: Anzahl konsolidierter Units, Failures.
- [ ] **T5 — Unit Tests:** Consolidation erzeugt Engrams mit layer='buffer'. Strength-Berechnung korrekt. Idempotenz: Doppelter Run erzeugt keine Duplikate. Batch-Processing.
