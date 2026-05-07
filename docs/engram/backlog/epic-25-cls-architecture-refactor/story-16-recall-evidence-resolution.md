# Story 16 — Recall Top-N Evidence-Auflösung

## User Story

Als Recall-Pipeline soll ich bei einem Schema-Treffer optional die Top-N Evidence-Engrams (über `evidence_engram_ids`) aus PostgreSQL laden, damit Reflect/Constructive Memory konkrete Beispiele zur Schema-Allgemeinheit liefern kann.

## Kontext

Schemas sind das "Allgemeinwissen" — wenn der User fragt "erzähl mir was über Coffee-Meetings", liefert das Schema die Generalisierung. Aber konkrete Beispiele machen die Antwort nützlicher. Über das `evidence_engram_ids`-Property (UUID-Array) können wir mit einem `WHERE id IN (...)` SQL-Query die Top-N stärksten Belege nachladen.

## Bestehende Codebasis

- **HybridRetriever:** `engine/retrieval/hybrid_retriever.py` (aus Story 15) — liefert RetrievalHit mit Schema-Daten inkl. evidence_engram_ids.
- **Engram Repository:** `engine/engram_repository.py::get_engrams_by_ids(ids: list[UUID])`.
- **Recall Orchestrator:** ruft Reflect/Constructive Memory mit dem Retrieval-Payload auf.

## Akzeptanzkriterien

- [x] `resolve_schema_evidence(hit, *, pool, bank_id, max_n=RECALL_DEFAULT_EVIDENCE_N) -> list[EvidenceEngram]` in `engine/search/evidence_resolver.py` (Pfad-Abweichung: `search/` analog zu Story 15).
- [x] Lädt aus PostgreSQL via SQL `mu ⨝ ed WHERE id = ANY($1::uuid[]) AND status='active'`
- [x] Aufruf optional steuerbar via `max_n`-Parameter; Story 17 wird modeabhängig drosseln/abschalten.
- [x] Status-Filter server-side (`ed.status = 'active'`); archivierte Engrams werden niemals returned.
- [x] Weniger als max_n aktive Engrams → kürzere Liste, kein Padding.
- [x] Leere `evidence_engram_ids` → `[]` ohne DB-Roundtrip.
- [x] `EvidenceResolverError` bei `kind="engram"`-Hit (Wiring-Bug nicht stillschweigend schlucken).
- [x] 9 Unit-Tests; Integration-Test verschoben auf Block E (Story 19/20 E2E).

## Tasks

- [x] **T1 — `resolve_schema_evidence()`:** In `engine/search/evidence_resolver.py` mit `EvidenceEngram` Pydantic-Modell (slim — id/text/fact_type/context/strength/tags). Order-Preservation by candidate index — C2 hat schon nach Strength-Desc sortiert.
- [x] **T2 — Batch-Fetch-Helper:** `fetch_active_engrams_by_ids(pool, ids, bank_id) -> dict[UUID, dict]` ebenfalls in `evidence_resolver.py` (statt eigenem `engram_repository.py` — Modul existiert nicht, und `engram_dictionary.py` nutzt kein `fq_table`). Returns dict für O(1)-Reorder.
- [x] **T3 — Compose-Helper:** `resolve_all_schema_evidence(hits, *, pool, bank_id, max_n)` walked die HybridRetriever-Liste durch — Schema-Hits bekommen Evidence, Engram-Hits paarweise `[]`. Volle Orchestrator-Pipeline-Routung folgt mit Story 18 (braucht S17 Mode-Gewichtung).
- [x] **T4 — Konstante:** `RECALL_DEFAULT_EVIDENCE_N = 3` in `engine/consolidation/constants.py` mit Begründung als Comment-Doku (write-time TOP_N=5 für Audit-Trail, recall-time 3 für Budget). Drift-Guard in Tests pinnt N < SCHEMA_TOP_N.
- [x] **T5 — Unit-Tests:** 9 Tests in `tests/test_evidence_resolver.py` — Drift-Guard, default-N happy path, archived-filtered, leere-Liste-short-circuit, max_n-Cap, max_n=0-short-circuit, Order-Preservation, kind=engram-raises, Compose-Helper Schema+Engram-Mix.
