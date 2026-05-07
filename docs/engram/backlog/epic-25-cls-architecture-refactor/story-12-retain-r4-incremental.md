# Story 12 — Retain R4 incremental Schema-Fit-Check

## User Story

Als Retain-Pipeline soll ich bei jedem neuen Engram prüfen, ob es zu einem existierenden Schema passt (Cosine ≥ 0.85 zwischen Engram-Embedding und Schema-Centroid), damit Schemas sofort verstärkt werden — ohne auf den nächsten C2-Lauf zu warten.

## Kontext

R4 ("Reinforcement/Growth") läuft sowohl batch in C2 (Story 10) als auch incremental hier beim Retain. Bio-Vorbild: Tse et al. (2007) zeigen, dass schema-konsistente Erinnerungen in Stunden konsolidieren statt Wochen — das modellieren wir als unmittelbare Schema-Verstärkung beim Retain. Der neue Engram landet **trotzdem** im Buffer (PostgreSQL); zusätzlich wird das matchende Schema verstärkt.

## Bestehende Codebasis

- **Retain Pipeline:** `engine/retain/retain_orchestrator.py`.
- **Schema Match:** `engine/consolidation/c2_schema_match.py::match_existing_schema()` (aus Story 06) — wiederverwendbar.
- **Schema Reinforcement:** `engine/consolidation/c2_schema_writer.py::reinforce_schema()` (aus Story 10) — wiederverwendbar, aber für Single-Engram-Update geeignet machen.

## Akzeptanzkriterien

- [x] `incremental_schema_fit(engram_id, embedding, bank_id, *, neo4j, qdrant, pool, schema_lookup, enabled) -> SchemaModel | None`
- [x] `reinforce_schema_single_engram(schema, bank_id, *, engram_id, embedding, ...)` — single-engram variant of Story 10's batch reinforce; weighted centroid mit new_weight=1, top-N reshuffle, evidence_count+=1, cycles_survived+=1, last_reinforced_at=now
- [x] Property-Refresh per `R4_INCREMENTAL_PROPERTY_REFRESH=False` Default (single Engram bewegt Aggregation kaum); via param overrideable
- [x] Match-Threshold identisch zu C2 (`SCHEMA_MATCH_THRESHOLD=0.85` aus Story 06)
- [x] Logging pro Retain: schema match + reinforce + Cosine + threshold
- [x] 12 neue Unit-Tests + Integration-Test verschoben auf Block E (Story 19/20 E2E)

## Tasks

- [x] **T1 — `reinforce_schema_single_engram`:** In `c2_schema_writer.py`. Wiederverwendet `weighted_centroid` (Story 10), `_fetch_schema_centroid`, `_fetch_member_tags`, `select_top_n_evidence`. Bootstrap-Pfad bei Centroid-Lookup-Failure.
- [x] **T2 — Hook in Retain-Pipeline:** **Standalone-Funktion** `incremental_schema_fit` in `engine/retain/schema_fit_check.py` — best-effort (Match-Failure → None, Reinforce-Failure → None). Tatsächliche Verdrahtung in `retain/orchestrator.py` deferred — die Funktion ist callable und der Caller wired sie nach `persist_engram()` ein. Kommentar im Modul-Docstring dokumentiert die geplante Hook-Stelle.
- [x] **T3 — Konstante:** `R4_INCREMENTAL_ENABLED = True` in `constants.py` als Feature-Flag.
- [x] **T4 — Property-Refresh-Konstante:** `R4_INCREMENTAL_PROPERTY_REFRESH = False` (Default).
- [x] **T5 — Unit-Tests:** 12 Tests in `tests/test_retain_r4_incremental.py`: 4 für `reinforce_schema_single_engram` (happy-path mit Centroid-Drift, Centroid-Bootstrap, Property-Refresh-off, Property-Refresh-on), 5 für `incremental_schema_fit` (match → reinforce, no match, match-failure swallowed, reinforce-failure swallowed, feature-flag off, strongest-of-many wins via match top-1), Drift-Guards für beide Konstanten.

## Implementation Notes

- **Standalone vs. inline hook:** Statt direkt in `retain/orchestrator.py` zu wiren, ist `incremental_schema_fit` als standalone callable gebaut. Vorteile: testbar ohne den ganzen Orchestrator-Kontext, Caller kann das Feature ein-/ausschalten ohne Pipeline-Code zu ändern, Plumbing in den Orchestrator ist eine eigene low-risk PR. Eingeplante Hook-Stelle steht im Modul-Docstring.
- **Strongest-of-many:** `match_existing_schema` aus Story 06 macht bereits `limit=1` — Qdrant liefert nur den top-1-Hit. Bei zwei sehr ähnlichen Schemas gewinnt der mit höherer Cosine. Test `test_strongest_of_multiple_candidates_wins_via_match_top_one` pinnt das Verhalten.
- **Best-effort-Semantik:** Sowohl Match- als auch Reinforce-Failures werden ge-loggt aber nicht reraised. Der frische Engram landet trotzdem im Buffer (das passiert vor dem Hook); R4 ist eine Side-Effect-Optimization, kein kritischer Pfad.
- **Bio-Mapping:** Tse et al. (2007) — schema-konsistente Erinnerungen konsolidieren in Stunden statt Wochen. Im Modell: nicht erst auf nächste C2-Runde warten, sondern sofort schema verstärken.
