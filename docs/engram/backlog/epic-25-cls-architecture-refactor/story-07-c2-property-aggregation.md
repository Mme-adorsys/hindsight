# Story 07 — C2 Property-Aggregation aus getaggten Engrams

## User Story

Als C2-Phase soll ich aus den Tags der Cluster-Engrams statistisch Schema-Properties (Modus für kategoriale Felder, Mittelwert für numerische, Range für temporale) berechnen, damit die Schema-Properties deterministisch und LLM-frei entstehen.

## Kontext

Wenn die Engrams beim Retain ordentlich getaggt wurden (Activity, Mood, Duration, Time, Participants etc.), reicht in C2 reine Statistik. Kein LLM-Call hier — das Schema "erbt" seine Properties aus den Cluster-Mitgliedern via Aggregation. Die generierte Description (Story 08) ist die einzige LLM-getriebene Stelle in C2.

## Bestehende Codebasis

- **Engram-Tags:** `engram.tags: list[str]` aus Retain Pipeline. Strukturierte Form ggf. `tags: dict[str, Any]` falls Epic 16 (API & Retain Enrichment) bereits durch ist.
- **Engram Repository:** `engine/engram_repository.py::get_engrams_by_ids(ids)`.

## Akzeptanzkriterien

- [x] `aggregate_properties(member_tags: list[list[str]]) -> dict[str, Any]` — sync, deterministisch, kein LLM
- [x] Aggregations-Regeln:
  - Kategoriale Tags → `{type: "categorical", value, count, confidence}` (mode-Anteil über `evidence_count`)
  - Numerische Tags → `{type: "numeric", mean, median, min, max, count}`
  - Temporale Tags → `{type: "temporal", min_iso, max_iso, count}`
  - Bag-of-Tags-Fallback (unparseable Strings ohne `:`) → unter Pseudo-Key `_keywords` als kategoriale Aggregation
- [x] Property-Schlüssel aus Tag-Namespace (`activity:coffee` → key `activity`); keine Whitelist
- [x] Output enthält `evidence_count` (Anzahl Engrams im Cluster)
- [x] Unit-Tests mit synthetischen Tag-Bundles

## Tasks

- [x] **T1 — Aggregations-Helper:** `engine/consolidation/property_aggregator.py` mit `aggregate_properties(member_tags)` + `_split_tag` + Type-Specific-Helpers (`_summarise_numeric`, `_summarise_temporal`, `_summarise_categorical`).
- [x] **T2 — Tag-Type-Detection:** `_try_parse_numeric` (alle Werte als `float()` parsbar → numeric, int/float-Subtype erkannt aus `.`/`e` im Original), `_try_parse_temporal` (alle Werte als `datetime.fromisoformat` parsbar → temporal). Bei einem Mixed-Type-Wert: Fallback auf categorical. Unparseable Strings ohne `:` landen in Pseudo-Bucket `_keywords`.
- [x] **T3 — Modus-Konfidenz:** `confidence = mode_count / evidence_count` (nicht `mode_count / total_tag_occurrences`) — interpretierbar als "Anteil der Engrams die diese Property tragen". Kann > 1.0 sein wenn ein Engram dasselbe Tag mehrfach trägt; raw `count` ebenfalls exposed.
- [x] **T4 — Pipeline-Integration:** `MaturedClusterCandidate.member_tags` Feld hinzugefügt (durchgereicht aus `ClusterCandidate.member_tags`); neue `CreationPayload(cluster, properties)` frozen dataclass und `prepare_creation_payloads(creation: tuple[UnmatchedForCreation,...]) -> tuple[CreationPayload,...]` in `c2_pattern_recognition.py`. Sync — pure Tag-Rollup, kein I/O.
- [x] **T5 — Unit-Tests:** 19 Tests in `test_property_aggregator.py` (5 split-tag, 5 coffee-bundle Canonical-Beispiel mit allen 4 Aggregations-Modi, 5 Type-Detection-Edges, 4 Edge-Cases inkl. empty input + confidence-Normierung) + 3 Tests in `test_c2_pattern_recognition.py` für `prepare_creation_payloads`.

## Implementation Notes

- **Tag-Convention dual-Modus:** Engram-Tags sind im Repo aktuell flat `list[str]`. `_split_tag` parst `key:value` wenn vorhanden (mit Whitespace-Trim und ISO-Timestamp-Schutz: nur erstes `:` splittet), sonst Bucket `_keywords`. Damit funktioniert die Aggregation auch wenn keine namespaced Tags geschrieben werden.
- **Type-Detection-Reihenfolge:** numeric vor temporal vor categorical. Numerische Detection greift, wenn **alle** Werte als `float` parsbar sind (zumindest 1 Wert mit `.`/`e` → float-Subtype, sonst int). Temporal nur wenn **alle** Werte ISO-8601 (mit oder ohne Timezone) parsbar sind. Sonst kategorial.
- **Konfidenz-Semantik:** `confidence = mode_count / evidence_count` ist absichtlich nicht auf [0, 1] geclamped — wenn das gleiche Tag pro Engram mehrfach auftritt, kann der Wert > 1 werden. Das ist Audit-Information; Story 09 (Schema-Persistierung) entscheidet, ob es in der Schema-Description als "n von m" ausgegeben wird.
- **Naming-Abweichung:** Story spricht von `engine/engram_repository.py::get_engrams_by_ids` — existiert nicht; wir reichen die Tags durch `MaturedClusterCandidate.member_tags` aus den schon-vorhandenen `filter_entries`-Daten in Story 04 durch.
