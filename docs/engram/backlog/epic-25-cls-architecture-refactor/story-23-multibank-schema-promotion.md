# Story 23 — Multi-Bank: Schema-Promotion in Shared Bank

## User Story

Als System soll ich starke Schemas einer Agent-Bank in die Shared Memory Bank promoten können (statt wie früher Neocortex-Engrams), damit cross-agent Wissen geteilt wird.

## Kontext

Die alte Multi-Bank-Promotion (Epic 14) promotet Engrams mit `layer='neocortex'` und Strength ≥ 0.6 in die Shared Bank. In der neuen Architektur gibt es keine Neocortex-Engrams mehr — die generalisierten Strukturen sind die Schemas. Heißt: cross-agent Wissensaustausch passiert auf Schema-Ebene, nicht mehr auf Engram-Ebene.

## Bestehende Codebasis

- **Multi-Bank-Promoter:** `engine/multi_bank/multi_bank_promoter.py` (aus Epic 14) — operiert heute auf Neocortex-Engrams.
- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01).
- **Shared Bank Setup:** Bank mit `tier='shared'`.

## Akzeptanzkriterien

- [ ] Promotion-Kandidaten sind ab jetzt **Schemas** der Agent-Banks (nicht mehr Engrams)
- [ ] Promotion-Bedingungen:
  - `evidence_count ≥ 10`
  - `cycles_survived ≥ 3` (das Schema hat sich über Wochen bewährt)
  - `last_reinforced_at` innerhalb der letzten 7 Tage
- [ ] Promotion ist eine **Schema-Kopie** in die Shared Bank (neuer Knoten mit eigener ID, evidence_engram_ids bleiben leer in Shared — Evidence ist agent-lokal)
- [ ] Original-Schema in Agent-Bank bleibt erhalten (keine Verschiebung — Sharing ist Replikation)
- [ ] Logging: pro Promotion-Lauf gezählt
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `promote_schema_to_shared(schema, source_bank_id, shared_bank_id)`:** In `engine/multi_bank/schema_promoter.py` (neue Datei). Schema-Kopie erstellen, neue ID, Felder wie Original aber `evidence_engram_ids=[]`, `evidence_count=schema.evidence_count` (für Audit beibehalten).
- [ ] **T2 — Promotion-Trigger:** Manuelle API + zukünftiger Scheduler-Hook. Endpoint `POST /v1/banks/{bank_id}/promote-schemas` mit Filter-Optionen (Default: Bedingungen aus Akzeptanzkriterien).
- [ ] **T3 — Konstanten:** `SHARED_PROMOTION_MIN_EVIDENCE = 10`, `SHARED_PROMOTION_MIN_CYCLES = 3`, `SHARED_PROMOTION_MAX_DAYS_INACTIVE = 7`.
- [ ] **T4 — Audit-Link:** `:Schema {bank=shared}` bekommt eine Property `source_bank_id` für Nachvollziehbarkeit (welche Agent-Bank hat es promotet).
- [ ] **T5 — Cleanup alter Multi-Bank-Engram-Promoter:** Alte `multi_bank_promoter.py`-Logik (Engram-Promotion) entfernen oder umbenennen — Engram-Promotion gibt es nicht mehr.
- [ ] **T6 — Unit-Tests:** (a) Schema erfüllt alle Bedingungen → in Shared Bank kopiert. (b) Schema mit evidence_count=8 → nicht promotet. (c) Promotion ist idempotent (gleiches Schema 2× promotet → Shared-Schema wird verstärkt, nicht dupliziert — siehe Story 24).
