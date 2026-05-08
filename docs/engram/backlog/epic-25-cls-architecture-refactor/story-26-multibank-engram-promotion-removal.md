# Story 26 — Multi-Bank: Engram-Promotion entfernen + Konzept-Cleanup

## User Story

Als System soll ich keine Engrams mehr in die Shared Bank promoten — Sharing passiert ausschließlich auf Schema-Ebene (Stories 23–25). Bestehende geteilte Engrams werden migriert oder archiviert.

## Kontext

In der alten Architektur konnten Engrams mit `layer='neocortex'` und Strength ≥ 0.6 in die Shared Bank wandern (Epic 14). In der neuen Architektur gibt es keine Neocortex-Engrams mehr (Story 02), und Sharing ist konzeptuell eine Schema-Aufgabe. Diese Story sorgt für sauberen Cleanup: alter Promotion-Pfad entfernen, bestehende geteilte Engrams in Shared Bank migrieren oder archivieren.

## Bestehende Codebasis

- **Multi-Bank-Promoter (alt):** `engine/multi_bank/multi_bank_promoter.py` (Engram-Promotion).
- **Shared Bank:** kann Engrams mit `tier='shared'` enthalten — diese müssen behandelt werden.

## Akzeptanzkriterien

- [x] Legacy `engine/consolidation/multi_bank_promoter.py` (~702 LOC, engram-basierte Shared-Promotion aus Epic 14) gelöscht.
- [x] NCR-Orchestrator `shared`-Phase ruft jetzt `promote_schemas_batch` aus dem neuen `engine/multi_bank/schema_promoter.py`. `NCRReport.promotion` Type-Annotation auf `SchemaPromotionResult` umgestellt.
- [ ] Eigener `/v1/banks/.../promote-engrams` Endpoint: existierte nie als separate Route — Promotion lief schon über `POST /v1/default/banks/{bank_id}/ncr/trigger?phase=shared`. Der Endpoint bleibt erhalten, nur die Implementierung dahinter ist jetzt schema-basiert.
- [ ] Migration für existierende Shared-Engrams: nicht nötig — Story 02 hat den Engram-Layer bereits auf `{working, buffer}` eingegrenzt und vorhandene `neocortex`-Engrams via Audit-Migration auf `buffer` umgesetzt. Es gibt keine Waisen mehr im falschen Layer.
- [x] Legacy-Tests (`test_multi_bank_promoter.py` 985 LOC + `test_multi_bank_integration.py` 682 LOC) gelöscht.
- [x] Build-Gates grün (45 Tests in `test_ncr_orchestrator.py` + `test_schema_promoter.py`).

## Tasks

- [x] **T1 — Code-Cleanup:** `git rm engine/consolidation/multi_bank_promoter.py` (702 LOC raus). Kein Wrapper-Stub — Story 23 T5 hatte das ohnehin als deferred markiert, der Pfad existiert nirgends mehr außerhalb dieser Datei.
- [x] **T2 — API-Endpoint:** Nicht nötig. Die NCR-Trigger-Route bleibt unverändert; nur die Implementierung hinter `phase=shared` wurde im NCROrchestrator auf `promote_schemas_batch` umgestellt.
- [x] **T3 — Migration:** Nicht nötig — Story 02 (a111c84) hat den Layer-Constraint mit Backfill bereits in Block A erledigt; alle `neocortex`-Engrams sind seither auf `buffer` migriert. Kein neuer DB-Schritt erforderlich.
- [x] **T4 — Konzept-Doku-Update:** `engine/multi_bank/__init__.py` Docstring beschreibt die schema-only Sharing-Architektur; kein eigenes README im Modul, kein Doku-Drift-Risiko.
- [x] **T5 — Tests bereinigen:** Beide Legacy-Test-Dateien gelöscht (`test_multi_bank_promoter.py` + `test_multi_bank_integration.py`). Die neue Funktionalität ist durch `tests/test_schema_promoter.py` (30 Tests, Stories 23–25) abgedeckt.
- [ ] **T6 — Smoke-Test:** verschoben — `tests/test_knowledge_evolution.py` und `tests/test_coffee_meeting_lifecycle.py` (Block E) decken den Gesamt-Flow ab; ein dedizierter Multi-Bank-Smoke folgt mit Block-G-Wiring (Stories 27/28 Control Plane brauchen ohnehin einen E2E-Auflauf).
