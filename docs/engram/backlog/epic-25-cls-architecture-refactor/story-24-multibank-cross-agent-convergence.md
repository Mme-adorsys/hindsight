# Story 24 — Multi-Bank: Cross-Agent Schema-Konvergenz

## User Story

Als System soll ich erkennen, wenn mehrere Agent-Banks unabhängig ähnliche Schemas gebildet haben, und diese zu einem **konvergenten Shared-Schema** zusammenführen, damit gleiche Patterns über Agents hinweg gebündelt werden statt redundant zu existieren.

## Kontext

Wenn Agent A ein Schema "Coffee-Meeting" hat und Agent B unabhängig ein sehr ähnliches Schema entwickelt (Centroid-Cosine ≥ 0.85), ist das ein Zeichen für ein robustes, übertragbares Pattern. In der Shared Bank wird daraus **ein** Schema mit erhöhter Konfidenz und Belegen aus beiden Quellen.

## Bestehende Codebasis

- **Schema Promoter:** `engine/multi_bank/schema_promoter.py` (aus Story 23).
- **Qdrant Search:** `kind="schema"`-Filter mit Bank-Filter.

## Akzeptanzkriterien

- [ ] Vor jeder Schema-Promotion (Story 23) wird in der Shared Bank gesucht: gibt es bereits ein ähnliches Schema (Cosine ≥ 0.85)?
- [ ] Match → **Shared-Schema verstärken** statt neu anlegen:
  - `evidence_count += incoming.evidence_count`
  - `cross_agent_count++` (neues Feld am Shared-Schema)
  - `source_bank_ids` Array um neue source_bank_id ergänzen
  - Centroid laufender Mittelwert
- [ ] Kein Match → neues Shared-Schema (Story 23 Pfad)
- [ ] Cross-agent-Konfidenz: Schemas mit `cross_agent_count ≥ 2` bekommen `confidence_tier="cross_agent_validated"` als Property
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Shared-Schema-Felder:** `cross_agent_count: Integer = 1`, `source_bank_ids: list[UUID]`, `confidence_tier: Enum["agent_local", "cross_agent_validated"]` als Properties am Shared-Schema.
- [ ] **T2 — Match-vor-Promotion:** In `schema_promoter.py::promote_schema_to_shared()` zuerst `match_existing_shared_schema(centroid, threshold=0.85)` aufrufen. Bei Match → Reinforcement-Pfad, sonst Neuanlage.
- [ ] **T3 — Konvergenz-Reinforcement-Pfad:** Helper `reinforce_shared_schema(shared_schema, incoming_schema, source_bank_id)`.
- [ ] **T4 — Konfidenz-Tier-Update:** Bei `cross_agent_count >= 2` → `confidence_tier="cross_agent_validated"` setzen.
- [ ] **T5 — Konstante:** `CROSS_AGENT_MATCH_THRESHOLD = 0.85` in `constants.py`.
- [ ] **T6 — Unit-Tests:** (a) Erstmaliger Schema-Promote → cross_agent_count=1, agent_local. (b) Zweiter ähnlicher Schema-Promote → cross_agent_count=2, cross_agent_validated. (c) Unähnliches Schema von Agent B → neues Shared-Schema, kein Merge.
