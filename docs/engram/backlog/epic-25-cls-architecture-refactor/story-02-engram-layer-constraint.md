# Story 02 — Engram-Layer auf {working, buffer} einschränken + Migration

## User Story

Als System soll der Engram-Layer-Wertebereich auf `{working, buffer}` eingeschränkt sein, damit der Neocortex strikt schema-only bleibt. Bestehende Engrams mit `layer='neocortex'` werden migriert.

## Kontext

In der alten Architektur konnte ein Engram via NCR Phase 2 nach `layer='neocortex'` promoten. In der neuen Architektur (CLS-strikt) leben Engrams ausschließlich in Working oder Buffer; Generalisierung passiert auf Schema-Ebene. Bestehende `neocortex`-Engrams sind keine echten Schemas — sie werden nach `buffer` zurückmigriert (mit Vermerk) und können in C2 als Quellmaterial für Schema-Erzeugung dienen.

## Bestehende Codebasis

- **Engram Dictionary:** PostgreSQL `engram_dictionary` Tabelle mit `layer ENUM('working','buffer','neocortex')`.
- **Pydantic Engram-Model:** `models/engram.py` mit `Layer = Literal['working','buffer','neocortex']`.
- **NCR Strengthen:** `engine/consolidation/ncr_strengthen.py` — promotet Engrams auf `neocortex` (wird in Story 18 entfernt).

## Akzeptanzkriterien

- [ ] PostgreSQL Enum `layer` auf nur noch `{working, buffer}` reduziert (Migration)
- [ ] Pydantic-Type `Layer = Literal['working','buffer']`
- [ ] Bestehende `neocortex`-Engrams werden auf `buffer` zurückgesetzt
- [ ] Audit-Spalte `migrated_from_neocortex_at` in Migration vermerkt (NULL für nicht-migrierte)
- [ ] Validator: Beim Schreiben eines Engrams mit `layer='neocortex'` wird ValidationError geworfen
- [ ] Unit-Tests + Migrations-Test

## Tasks

- [ ] **T1 — Alembic Migration `25_02_engram_layer_constraint`:** UPDATE alle `layer='neocortex'` → `layer='buffer'`, Spalte `migrated_from_neocortex_at TIMESTAMP NULL` hinzufügen, gesetzt für migrierte Zeilen. Anschließend Enum-Constraint enger ziehen (`CHECK (layer IN ('working','buffer'))`).
- [ ] **T2 — Pydantic-Type:** `Layer` Literal in `models/engram.py` einschränken. Validator ergänzen, der bei `layer='neocortex'` einen klaren ValueError wirft.
- [ ] **T3 — Repository-Methoden Audit:** `engram_repository.py` durchgehen — alle Stellen, die `layer='neocortex'` lesen oder schreiben, aufdecken und zur Anpassung markieren (Detail-Anpassungen in Story 18).
- [ ] **T4 — Migration-Reverse:** Down-Migration schreibt `layer='buffer'` zurück bzw. ergänzt 'neocortex' wieder im Enum, falls Rollback nötig.
- [ ] **T5 — Unit-Tests:** Validator-Test (neocortex schreibt → Error). Migrations-Test mit fixture-Datenbank (Pre-State neocortex, Post-State buffer + migrated_from_neocortex_at gesetzt).
