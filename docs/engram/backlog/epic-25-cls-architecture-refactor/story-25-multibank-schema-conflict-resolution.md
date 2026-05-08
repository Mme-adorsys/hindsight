# Story 25 — Multi-Bank: Schema-Konflikt-Resolution

## User Story

Als System soll ich erkennen, wenn zwei zu mergende Schemas inhaltlich widersprechen (z.B. unterschiedliche dominante Property-Werte), und konkurrierende Schemas im Shared-Graph als alternative Hypothesen modellieren — damit cross-agent Konflikte nicht stillschweigend einseitig aufgelöst werden.

## Kontext

Beim Merge in Story 24 kann es passieren, dass Agent A "Coffee-Meeting = produktiv" hat und Agent B "Coffee-Meeting = locker". Centroid-Cosine kann hoch sein, aber die Properties divergieren. Statt einen Wert zu überschreiben (Information geht verloren), modellieren wir das als Konflikt: Schema bleibt mit beiden Property-Optionen, plus `:CONTRADICTS`-Edge falls die Schemas wirklich konkurrieren.

## Bestehende Codebasis

- **Shared-Schema:** Story 23 + 24.
- **Properties:** strukturierte key-value Map am Schema-Knoten.

## Akzeptanzkriterien

- [x] `detect_conflicts(props_a, props_b) → list[ConflictReport]` läuft im Multi-Bank-Batch nach `match_existing_shared_schema` und vor `reinforce_shared_schema`.
- [x] Konflikt-Definition (drift-guarded Defaults):
  - Kategoriale Properties: Jaccard-Overlap < `CATEGORICAL_DISJOINT_THRESHOLD = 0.2`
  - Numerische `{min,max,mean}`-Envelopes: |mean_diff| > `NUMERIC_DIVERGENCE_FACTOR = 0.5` × max-Range
  - Skalare Werte (str/int/float/bool): einfache Inequality
  - Type-Mismatch (z.B. categorical vs numeric) wird ebenfalls geflaggt
- [x] Bei Konflikt: Fork-statt-Merge — `_mint_disputed_alternative` legt einen *neuen* Shared-Schema (frische UUID) an mit der Sicht des einreichenden Agents; das *bestehende* Shared-Schema bleibt inhaltlich unangetastet, bekommt aber den Tier-Flip auf `cross_agent_disputed` plus `disputed_keys`-Property. Beide werden via symmetrischer `:CONTRADICTS`-Edge verbunden.
- [x] `confidence_tier`-Werte: `agent_local`, `cross_agent_validated`, `cross_agent_disputed` — Story-25-Erweiterung dokumentiert in den Modul-Konstanten.
- [x] `_BOOKKEEPING_KEYS` (source_bank_id, source_bank_ids, promoted_from_schema_id, cross_agent_count, confidence_tier) werden vom Diff übersprungen — sonst würde jede Promotion einen Konflikt melden.
- [x] `SchemaPromotionResult` um `disputed`/`disputed_ids` erweitert; alle drei Pfade (promoted/reinforced/disputed) werden im Batch-Log gezählt.
- [x] 30 Unit-Tests gesamt grün (22 Story 23/24 + 8 Story 25).

## Tasks

- [x] **T1 — Property-Diff-Engine:** `engine/multi_bank/property_diff.py` mit `detect_conflicts` + `has_conflicts` + `ConflictReport` Dataclass; deterministisch, kein LLM.
- [x] **T2 — Property-Merge mit Konflikt-Handling:** Konflikt-Branch in `promote_schemas_batch` (vor `reinforce_shared_schema`) — bei Konflikt fork via `_mint_disputed_alternative`, sonst Story-24 reinforce.
- [x] **T3 — `:CONTRADICTS`-Edge:** Neue `link_contradicts(client, a_id, b_id)` Helper in `engine/schema/schema_repository.py` (symmetrisch via doppelter MERGE — keine Migration nötig, Neo4j-Edges sind dynamisch).
- [x] **T4 — `confidence_tier`-Erweiterung:** Modul-Konstanten `_TIER_DISPUTED = "cross_agent_disputed"`, `_PROP_DISPUTED_KEYS = "disputed_keys"`.
- [ ] **T5 — Property-Visualisierung beim Recall:** verschoben — der HybridRetriever (Story 15) gibt `properties` ohnehin verbatim zurück, der Disputed-Tier propagiert mit. Eine spezielle "alternative Werte-Set"-Repräsentation kann mit dem Control Plane Schema-Explorer (Stories 27/28) sauberer gemeinsam ausgerollt werden.
- [x] **T6 — Unit-Tests:** 7 TestPropertyDiff (compatible/disjoint kategorisch, numeric in/außerhalb-Range, Skalar-Inequality, Bookkeeping-Skip, single-side key) + 1 TestDisputedFork (Konflikt-Pfad mintet Alternative, flippt existing Tier, legt symmetrische Edge).
