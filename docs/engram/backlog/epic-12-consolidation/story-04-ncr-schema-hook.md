# Story 04 — NCR Phase 3: Schema Compression Hook

## User Story

Als System soll NCR Phase 3 einen Hook bereitstellen der die Schema Emergence Regeln (Epic 13) aufruft.

## Kontext

NCR Phase 3 ist das REM-Äquivalent: Muster werden über Engrams erkannt, Schemas erzeugt oder gestärkt. Die eigentliche Schema-Logik kommt in Epic 13 — hier definieren wir nur das Interface und den Hook damit die NCR-Orchestration komplett ist.

## Akzeptanzkriterien

- [ ] NCR Phase 3 ruft ein `SchemaProcessor` Interface auf
- [ ] Default-Implementation: NoOp (tut nichts, bis Epic 13 die echte Implementation liefert)
- [ ] Interface: `process(bank_id, neocortex_engrams) → SchemaResult`
- [ ] SchemaResult: Neue Schemas, gestärkte Schemas, gelöschte Schemas
- [ ] Phase 3 läuft nur auf Neocortex-Layer Engrams (nicht Buffer)

## Tasks

- [ ] **T1 — SchemaProcessor Interface:** `engine/consolidation/schema_processor.py`. ABC `SchemaProcessor` mit `async process(bank_id, engrams: list[FullEngram]) → SchemaResult`. `SchemaResult(created: int, strengthened: int, deleted: int, details: list)`.
- [ ] **T2 — NoOpSchemaProcessor:** Default-Implementation: `NoOpSchemaProcessor(SchemaProcessor)` — gibt `SchemaResult(0, 0, 0, [])` zurück. Wird in Epic 13 durch echte Implementation ersetzt.
- [ ] **T3 — NCR Phase 3 Orchestration:** In NCR Orchestrator (Story 05): Phase 3 ruft `schema_processor.process()` auf mit allen Neocortex-Engrams. Ergebnis wird geloggt.
- [ ] **T4 — Unit Tests:** NoOp Processor liefert leeres Ergebnis. Interface-Contract: SchemaResult ist korrekt typisiert. Phase 3 ruft Processor auf (Mock verify).
