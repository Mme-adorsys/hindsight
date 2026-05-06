# Story 13 — C3 Hyper-Schema-Bildung (R3)

## User Story

Als C3-Phase soll ich semantisch verwandte Schemas (Cosine ≥ 0.7) zu einem Hyper-Schema zusammenfassen können, damit der Cortex auch über Schemas selbst abstrahiert (Game-of-Life R3 auf Schema-Ebene).

## Kontext

R3 lief in der alten Architektur als Property-Extraktion auf Engram-Ebene. In der neuen Architektur ist die Property-Extraktion deterministisch in C2 verlagert (Story 07). R3 wird hier zur **Schema-zu-Hyper-Schema-Subsumption**: zwei verwandte Schemas mit systematisch unterschiedlichen Property-Werten werden zu einem Hyper-Schema verallgemeinert. Beispiel: `coffee_meeting_1on1_morning` + `coffee_meeting_1on1_afternoon` → `coffee_meeting_1on1`.

## Bestehende Codebasis

- **Schema Repository:** `engine/schema/schema_repository.py` (aus Story 01) inkl. `link_specialization(schema_id, hyper_id)`.
- **Centroid-Helper:** `engine/schema/centroid.py::compute_centroid(vectors)` (aus Story 03).
- **Property Aggregator:** wiederverwendbar — Hyper-Schema bekommt union/generalisierte Properties.

## Akzeptanzkriterien

- [ ] Neue Datei `engine/consolidation/c3_schema_restructure.py`
- [ ] Funktion `find_hyper_schema_candidates(bank_id) -> list[tuple[Schema, Schema]]`: paarweise Schema-Cosine ≥ 0.7, mit mindestens einem systematisch abweichenden Property-Feld
- [ ] Funktion `create_hyper_schema(schemas: list[Schema]) -> HyperSchema`: Centroid = mean(schema_centroids), Properties = union (überlappende beibehalten, abweichende als Range/Liste), evidence_count = sum(schema.evidence_count)
- [ ] Edge `:SPECIALIZES` zwischen jedem Subschema und Hyper-Schema
- [ ] Logging: pro C3-Lauf gefundene Hyper-Schemas
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Kandidaten-Finder:** `find_hyper_schema_candidates()` läuft Pairwise über alle aktiven Schemas der Bank, Cosine via Qdrant-Search, Property-Diff prüfen.
- [ ] **T2 — Hyper-Schema-Erzeuger:** `create_hyper_schema(schemas)` baut Centroid + Property-Union. Description optional via gleichem `consolidation.schema_description`-Step.
- [ ] **T3 — Edge-Linking:** `link_specialization(subschema_id, hyper_id)` für jedes Subschema.
- [ ] **T4 — Konstanten:** `HYPER_SCHEMA_COHESION_THRESHOLD = 0.7` und `HYPER_SCHEMA_MIN_PROPERTY_DIFF = 1` (mind. 1 Property muss sich unterscheiden).
- [ ] **T5 — Pipeline-Integration:** Aufruf in `c3_orchestrator.py` (Story 14 ergänzt R5).
- [ ] **T6 — Unit-Tests:** (a) Zwei verwandte Schemas mit unterschiedlichem time_window → Hyper-Schema mit time_window=Union. (b) Zwei Schemas zu unähnlich → kein Hyper-Schema. (c) Edge-Anlage in Neo4j sauber.
