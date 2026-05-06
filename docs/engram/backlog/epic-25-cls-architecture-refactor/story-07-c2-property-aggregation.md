# Story 07 — C2 Property-Aggregation aus getaggten Engrams

## User Story

Als C2-Phase soll ich aus den Tags der Cluster-Engrams statistisch Schema-Properties (Modus für kategoriale Felder, Mittelwert für numerische, Range für temporale) berechnen, damit die Schema-Properties deterministisch und LLM-frei entstehen.

## Kontext

Wenn die Engrams beim Retain ordentlich getaggt wurden (Activity, Mood, Duration, Time, Participants etc.), reicht in C2 reine Statistik. Kein LLM-Call hier — das Schema "erbt" seine Properties aus den Cluster-Mitgliedern via Aggregation. Die generierte Description (Story 08) ist die einzige LLM-getriebene Stelle in C2.

## Bestehende Codebasis

- **Engram-Tags:** `engram.tags: list[str]` aus Retain Pipeline. Strukturierte Form ggf. `tags: dict[str, Any]` falls Epic 16 (API & Retain Enrichment) bereits durch ist.
- **Engram Repository:** `engine/engram_repository.py::get_engrams_by_ids(ids)`.

## Akzeptanzkriterien

- [ ] Neue Funktion `aggregate_properties(engrams) -> dict[str, Any]`
- [ ] Aggregations-Regeln:
  - Kategoriale Tags (z.B. activity, mood) → Modus + Modus-Häufigkeit (`{value: "coffee", confidence: 0.92}`)
  - Numerische Tags (z.B. duration_minutes) → mean, median, range
  - Temporale Tags (z.B. event_time) → range (min, max)
  - Listen-Tags (z.B. participants[]) → mode der Länge
- [ ] Property-Schlüssel werden aus den Engram-Tag-Schlüsseln abgeleitet (keine Whitelist)
- [ ] Output enthält `evidence_count = len(engrams)` als Audit-Hilfe
- [ ] Unit-Tests mit syntethischen Engrams

## Tasks

- [ ] **T1 — Aggregations-Helper:** `engine/consolidation/property_aggregator.py` mit Funktion `aggregate_properties(engrams)`.
- [ ] **T2 — Tag-Type-Detection:** Auto-Detection des Tag-Typs (string → kategorial, int/float → numerisch, ISO-Timestamp → temporal). Fallback: kategorial.
- [ ] **T3 — Modus-Konfidenz:** Bei kategorialen Werten Anteil des Modus (z.B. 7 von 8 Engrams = 0.875) als Konfidenz-Wert mitliefern.
- [ ] **T4 — Pipeline-Integration:** In `c2_pattern_recognition.py` nach Match-Schritt → für jeden Creation-Pfad-Kandidaten `aggregate_properties()` aufrufen.
- [ ] **T5 — Unit-Tests:** (a) 8 Coffee-Engrams mit Tags → Properties stimmen. (b) Mixed Tags → unterschiedliche Aggregations-Regeln. (c) Edge: alle leer → leere Dict.
