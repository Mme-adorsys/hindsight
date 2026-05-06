# Story 22 — Schema Reconsolidation Window & Drift-Tracking

## User Story

Als System soll ich Schema-Centroid-Drifts und Property-Änderungen aus Reconsolidation auditieren können (last_drifted_at, drift_history), damit nachvollziehbar bleibt, wie sich ein Schema über Recalls hinweg verändert hat — analog zum biologischen Reconsolidation-Window.

## Kontext

Reconsolidation kann den Schema-Centroid und die Properties verschieben (Story 21). Das ist gewünscht (Schemas adaptieren sich an neue Realität), aber auch riskant (ein einzelner falscher Recall könnte ein Schema "verbiegen"). Wir brauchen ein Audit-Trail und eine Throttle-Mechanik, damit Drift kontrollierbar bleibt.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01).
- **Reconsolidation-Branch:** aus Story 21.

## Akzeptanzkriterien

- [ ] Neue Felder am `:Schema`-Knoten: `drift_count: Integer = 0`, `last_drifted_at: Timestamp NULL`
- [ ] Pro Drift-Event (Centroid-Verschiebung) wird `drift_count++`, `last_drifted_at=now`
- [ ] Throttle: Wenn `drift_count > MAX_DRIFTS_PER_DAY` (default 5), wird kein weiterer Drift mehr durchgeführt — Schema bleibt stabil bis zum nächsten C2-Lauf, der Counter resettet
- [ ] Drift-Event-Log in Tabelle `schema_drift_events { id, schema_id, alpha, query_hash, timestamp, mode }` für Audit
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Schema-Modell erweitern:** `drift_count`, `last_drifted_at` als Property auf `:Schema`. Migration.
- [ ] **T2 — Drift-Events-Tabelle:** PostgreSQL `schema_drift_events`. Alembic-Migration.
- [ ] **T3 — Throttle-Logic:** In `reconsolidation_orchestrator.py` Schema-Branch: vor Drift prüfen `drift_count_last_24h < MAX_DRIFTS_PER_DAY`. Bei Überschreitung Drift skippen + Logging.
- [ ] **T4 — Reset-Hook:** In `c2_pattern_recognition.py` (Story 06+10) bei Schema-Reinforcement → `drift_count = 0` zurücksetzen.
- [ ] **T5 — Konstante:** `MAX_SCHEMA_DRIFTS_PER_DAY = 5` in `constants.py`.
- [ ] **T6 — Unit-Tests:** (a) 5 Drifts in 24h erlaubt, 6. wird geblockt. (b) C2-Reinforcement resettet Counter. (c) Audit-Eintrag pro Drift in DB.
