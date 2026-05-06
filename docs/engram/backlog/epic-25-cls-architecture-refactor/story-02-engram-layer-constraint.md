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

- [x] PostgreSQL Enum `layer` auf nur noch `{working, buffer}` reduziert (Migration `e25a02layer`)
- [x] Pydantic-Type `Layer = Literal['working','buffer']` (`engine/response_models.py:420`; nominaler Pfad `models/engram.py` existiert nicht)
- [x] Bestehende `neocortex`-Engrams werden auf `buffer` zurückgesetzt
- [x] Audit-Spalte `migrated_from_neocortex_at` in Migration vermerkt (NULL für nicht-migrierte)
- [x] Validator: Beim Schreiben eines Engrams mit `layer='neocortex'` wird ValidationError geworfen (Pydantic `Literal` rejection — Test `test_layer_literal_neocortex_rejected`)
- [x] Unit-Tests + Migrations-Test

## Tasks

- [x] **T1 — Alembic Migration `e25a02layer_engram_layer_constraint.py`** (chained on `d6e7f8a9b0c1`): UPDATE `layer='neocortex'` → `layer='buffer'` mit `migrated_from_neocortex_at = NOW()`, neue TIMESTAMP-Spalte hinzugefügt, CHECK enger gezogen auf `('working','buffer')`. Multi-Tenant-Schema via `_qualified_table()` und `_schema_kwarg()` unterstützt.
- [x] **T2 — Pydantic-Type:** `Engram.layer: Literal['working','buffer']` in `engine/response_models.py:420` (Story-Pfad `models/engram.py` existiert nicht; Engram-Pydantic liegt zentral in `response_models.py`). `EngramMetadata.layer` Doc-String aktualisiert. SQLAlchemy `EngramDictionary` CheckConstraint und Layer-Comment aktualisiert. Pydantic `Literal` allein erfüllt den Validator-Anspruch — eigener `field_validator` wäre redundant.
- [ ] **T3 — Repository-Methoden Audit:** `engram_repository.py` durchgehen — alle Stellen, die `layer='neocortex'` lesen oder schreiben, aufdecken und zur Anpassung markieren (Detail-Anpassungen in Story 18).

  **Audit-Ergebnis (2026-05-06, Story 02):** Es gibt keinen `engram_repository.py`-Modul; die Engram-Persistenz läuft über `engine/engram_dictionary.py`. Die folgenden Stellen schreiben oder lesen `layer='neocortex'` und werden in Story 18 entfernt bzw. auf Schema-Pfad migriert:

  | Datei | Zeile | Art | Story-18-Aktion |
  |-------|------:|-----|-----------------|
  | `engine/consolidation/ncr_strengthen.py` | 104, 214, 235, 286 | Write/Read | Modul wird komplett entfernt (Promotion buffer→neocortex entfällt). |
  | `engine/consolidation/multi_bank_promoter.py` | 170, 354, 377 | Write | Engram-basierte Promotion entfällt; Schema-basierte Variante in Stories 23–26. |
  | `engine/consolidation/schema_maturation.py` | 152, 312, 382 | Write/Read | Modul wird in Story 18 entfernt (C2 ersetzt durch Pattern-Recognition-Pipeline in Stories 04–11). |
  | `engine/consolidation/schema_clustering.py` | 47–48 | Read (Cypher) | Modul wird in Story 18 entfernt. |
  | `engine/consolidation/ncr_orchestrator.py` | 380 | Read | NCR-3-Phasen-Refactor in Stories 04+ ersetzt diese Schleife. |
  | `engine/consolidation/ncr_decay.py` | 389–390 | Read | Decay-Re-Eval läuft in Story 11 nur noch über Buffer-Engrams. |
  | `engine/engram_dictionary.py` | 158, 259 | Comment + Cypher-Filter | Filter ändert sich in Story 18 zu `layer IN ('working','buffer')`. |
  | `engine/response_models.py:344` | — | Doc-String | Bereits in Story 02 aktualisiert. |
  | `models.py:360` | — | Comment | Bereits in Story 02 aktualisiert. |

  **Übergangsverhalten zwischen Story 02 und 18:** Die DB-CHECK-Constraint und der Pydantic-Literal lehnen `'neocortex'` ab. Jeder oben aufgelistete Write-Pfad wirft zur Laufzeit (DB- oder Validation-Error). Integration-Tests, die NCR oder multi-bank-promoter ausführen, sind dadurch rot, bis Story 18 den Cleanup macht — die Unit-Test-Suite (Standard-Build-Gate) ist nicht betroffen, weil sie diese Pfade mockt.
- [x] **T4 — Migration-Reverse:** `downgrade()` der Migration restauriert die alte CHECK `('working','buffer','neocortex')`, dreht die in T1 migrierten Zeilen via `migrated_from_neocortex_at IS NOT NULL` zurück auf `layer='neocortex'` und droppt die Audit-Spalte.
- [x] **T5 — Unit-Tests:** `tests/test_engram_models.py::test_layer_literal_neocortex_rejected` (Pydantic-Validator) + `tests/test_engram_layer_migration.py` (Shape-Test mit gemocktem `alembic.op`: revision-Chain, add_column, UPDATE, CHECK-Tightening, Multi-Tenant-Schema, downgrade-Symmetrie). 9 neue Unit-Tests.
