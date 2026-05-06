# Story 25 — Multi-Bank: Schema-Konflikt-Resolution

## User Story

Als System soll ich erkennen, wenn zwei zu mergende Schemas inhaltlich widersprechen (z.B. unterschiedliche dominante Property-Werte), und konkurrierende Schemas im Shared-Graph als alternative Hypothesen modellieren — damit cross-agent Konflikte nicht stillschweigend einseitig aufgelöst werden.

## Kontext

Beim Merge in Story 24 kann es passieren, dass Agent A "Coffee-Meeting = produktiv" hat und Agent B "Coffee-Meeting = locker". Centroid-Cosine kann hoch sein, aber die Properties divergieren. Statt einen Wert zu überschreiben (Information geht verloren), modellieren wir das als Konflikt: Schema bleibt mit beiden Property-Optionen, plus `:CONTRADICTS`-Edge falls die Schemas wirklich konkurrieren.

## Bestehende Codebasis

- **Shared-Schema:** Story 23 + 24.
- **Properties:** strukturierte key-value Map am Schema-Knoten.

## Akzeptanzkriterien

- [ ] Property-Diff-Check beim Merge (Story 24): pro key wird geprüft, ob die Werte kompatibel sind
- [ ] Konflikt-Definition:
  - Kategoriale Properties: unterschiedliche Modi mit Konfidenz ≥ 0.6 in beiden Schemas
  - Numerische Properties: Differenz > 50% der Range
  - Listen-Properties: Disjunktheit > 80%
- [ ] Bei Konflikt:
  - Properties werden als Liste alternativer Werte gespeichert (`{value: ..., source_bank: ..., confidence: ...}`)
  - Neuer Edge `(:Schema)-[:CONTRADICTS]->(:Schema)` zu konkurrierenden Schemas
  - Property-Konfidenz-Tier sinkt (`confidence_tier="cross_agent_disputed"`)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Property-Diff-Engine:** `engine/multi_bank/property_diff.py::detect_conflicts(props_a, props_b) -> list[ConflictReport]`.
- [ ] **T2 — Property-Merge mit Konflikt-Handling:** Erweiterung `reinforce_shared_schema()` um Conflict-Branch.
- [ ] **T3 — `:CONTRADICTS`-Edge:** Neue Edge-Definition in Neo4j-Schema-Migration.
- [ ] **T4 — `confidence_tier`-Erweiterung:** Enum um `cross_agent_disputed` ergänzen.
- [ ] **T5 — Property-Visualisierung:** Bei Disputed-Schemas wird im Output beim Recall das alternative Werte-Set zurückgegeben (nicht der Modus).
- [ ] **T6 — Unit-Tests:** (a) Kompatible Properties → kein Konflikt. (b) Modi divergieren → Konflikt erkannt + Property als Liste gespeichert. (c) `:CONTRADICTS`-Edge wird angelegt zwischen konkurrierenden Schemas.
