# Story 24 — Multi-Bank: Cross-Agent Schema-Konvergenz

## User Story

Als System soll ich erkennen, wenn mehrere Agent-Banks unabhängig ähnliche Schemas gebildet haben, und diese zu einem **konvergenten Shared-Schema** zusammenführen, damit gleiche Patterns über Agents hinweg gebündelt werden statt redundant zu existieren.

## Kontext

Wenn Agent A ein Schema "Coffee-Meeting" hat und Agent B unabhängig ein sehr ähnliches Schema entwickelt (Centroid-Cosine ≥ 0.85), ist das ein Zeichen für ein robustes, übertragbares Pattern. In der Shared Bank wird daraus **ein** Schema mit erhöhter Konfidenz und Belegen aus beiden Quellen.

## Bestehende Codebasis

- **Schema Promoter:** `engine/multi_bank/schema_promoter.py` (aus Story 23).
- **Qdrant Search:** `kind="schema"`-Filter mit Bank-Filter.

## Akzeptanzkriterien

- [x] `match_existing_shared_schema` (Qdrant `kind=schema` + `bank_id=shared` Filter, Cosine ≥ 0.85) läuft in `promote_schemas_batch` vor jedem `promote_schema_to_shared`-Aufruf.
- [x] Match → `reinforce_shared_schema`-Pfad: `evidence_count` Summe, `cross_agent_count++`, `source_bank_ids` extend (dedup), Centroid running-mean via existierendes `weighted_centroid`, `last_reinforced_at=now`.
- [x] Kein Match → bestehender Story-23 Create-Pfad (initialisiert `cross_agent_count=1`, `source_bank_ids=[source]`, `confidence_tier="agent_local"`).
- [x] `confidence_tier` upgrades automatisch zu `cross_agent_validated` sobald ≥ 2 distinct source banks beteiligt sind. Re-Promotes vom selben Agent (dedup-Pfad) lassen den Tier unverändert; `evidence_count` summiert sich aber trotzdem (Audit-Wert).
- [x] `SchemaPromotionResult` um `reinforced`/`reinforced_ids` erweitert; create- und reinforce-Pfade werden sauber separat gezählt.
- [x] 22 Unit-Tests grün (13 Story 23 + 9 Story 24); Integration-Test verschoben auf Block-G-Wiring.

## Tasks

- [x] **T1 — Shared-Schema-Felder:** Riding on `properties` JSON statt eigene Neo4j-Properties — `source_bank_ids: list[str]`, `cross_agent_count: int`, `confidence_tier: "agent_local"|"cross_agent_validated"`. Spart eine Schema-Migration; `_serialise_props_for_neo4j` aus Story 01 macht den Round-Trip.
- [x] **T2 — Match-vor-Promotion:** `promote_schemas_batch` ruft jetzt `match_existing_shared_schema` vor `promote_schema_to_shared`. Bei Match → reinforce-Pfad mit `result.reinforced++`, sonst Story-23 create-Pfad mit `result.promoted++`.
- [x] **T3 — Konvergenz-Reinforcement-Pfad:** Neuer Helper `reinforce_shared_schema(existing, incoming, *, source_bank_id, ...)`. Centroid-Merge weighted_centroid(old × len(prior_sources), incoming × 1) + L2-renorm.
- [x] **T4 — Konfidenz-Tier-Update:** Implizit über `_PROP_CONFIDENCE_TIER` Konstante; gesetzt sobald `cross_agent_count >= 2`.
- [x] **T5 — Konstante:** `CROSS_AGENT_MATCH_THRESHOLD = 0.85` in `engine/consolidation/constants.py` mit Drift-Guard.
- [x] **T6 — Unit-Tests:** 9 neue Tests — Const-Pin, 4× match_existing_shared_schema (above-threshold / below-threshold / qdrant-fail / empty-centroid-short-circuit), 3× reinforce_shared_schema (first-external-upgrades-tier / same-source-dedup / centroid-running-mean), 1× batch match-promotes-via-reinforce-path.
