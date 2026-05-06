# Story 26 — Multi-Bank: Engram-Promotion entfernen + Konzept-Cleanup

## User Story

Als System soll ich keine Engrams mehr in die Shared Bank promoten — Sharing passiert ausschließlich auf Schema-Ebene (Stories 23–25). Bestehende geteilte Engrams werden migriert oder archiviert.

## Kontext

In der alten Architektur konnten Engrams mit `layer='neocortex'` und Strength ≥ 0.6 in die Shared Bank wandern (Epic 14). In der neuen Architektur gibt es keine Neocortex-Engrams mehr (Story 02), und Sharing ist konzeptuell eine Schema-Aufgabe. Diese Story sorgt für sauberen Cleanup: alter Promotion-Pfad entfernen, bestehende geteilte Engrams in Shared Bank migrieren oder archivieren.

## Bestehende Codebasis

- **Multi-Bank-Promoter (alt):** `engine/multi_bank/multi_bank_promoter.py` (Engram-Promotion).
- **Shared Bank:** kann Engrams mit `tier='shared'` enthalten — diese müssen behandelt werden.

## Akzeptanzkriterien

- [ ] Alter Engram-Promotion-Code entfernt (war in Story 23 T5 angekündigt)
- [ ] API-Endpoints für Engram-Promotion (`/v1/banks/.../promote-engrams`) entfernt oder auf 410 Gone gesetzt
- [ ] Migration: bestehende Engrams in Shared Bank werden zu Engrams im Buffer (`tier='shared'` Bank, `layer='buffer'`) — sie können dann via C2 in der Shared Bank zu Shared-Schemas konsolidiert werden
- [ ] Doku-Update: Multi-Bank-Konzept im README/Doku reflektiert die neue Schema-only-Logik
- [ ] Unit-Tests + Migrations-Test

## Tasks

- [ ] **T1 — Code-Cleanup:** `engine/multi_bank/multi_bank_promoter.py` löschen oder leerer Wrapper, der einen Deprecation-Error wirft.
- [ ] **T2 — API-Endpoint deprecaten:** `/v1/banks/{bank_id}/promote-engrams` auf 410 Gone.
- [ ] **T3 — Migration bestehender Shared-Engrams:** Alembic-Migration, die in Shared Banks alle existierenden Engrams auf `layer='buffer'` setzt (sofern sie auf 'neocortex' standen).
- [ ] **T4 — Konzept-Doku-Update:** README in `engine/multi_bank/` (falls vorhanden) auf die neue Schema-only-Promotion umstellen.
- [ ] **T5 — Tests bereinigen:** Alte Engram-Promotion-Tests entfernen oder umbauen auf Schema-Promotion (Stories 23–25).
- [ ] **T6 — Smoke-Test:** Nach Migration ist die Shared Bank konsistent (keine Waisenkind-Engrams im falschen Layer).
