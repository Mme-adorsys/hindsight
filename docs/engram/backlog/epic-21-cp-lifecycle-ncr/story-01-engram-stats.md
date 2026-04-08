# Story 01 — Engram Layer Statistics API

## User Story

Als Control Plane will ich aggregierte Statistiken über die Layer-Verteilung und Strength-Distribution einer Bank abfragen können, damit ich dem Operator einen Lifecycle-Überblick geben kann ohne alle Engrams einzeln zu laden.

## Kontext

Die Engine speichert pro Engram ein `layer` Feld (null=Working Memory, "buffer", "neocortex") und ein `strength` Feld (0.0–1.0) in Qdrant. Um eine Layer-Verteilung anzuzeigen, braucht man eine Aggregation — nicht die volle Liste. Bei Banks mit Tausenden Engrams wäre ein Full-List-Call zu langsam.

## Bestehende Codebasis

- **Qdrant Storage:** `hindsight_api/engine/engram_storage.py` — `EngramStorageService` mit Qdrant-Client. Layer und Strength sind Payload-Felder.
- **HTTP API:** `hindsight_api/api/http.py` — Router. Stats-Endpoint existiert nur als `getBankStats()` (Memory Count, etc.).

## Akzeptanzkriterien

- [x] `GET /v1/default/banks/{bank_id}/engrams/stats` liefert Layer-Counts, Avg-Strength pro Layer, Strength-Distribution
- [x] Response ist performant (Aggregation, nicht Full-List)
- [x] Leere Bank liefert Null-Counts, keine Fehler
- [x] Response-Schema ist dokumentiert

## Tasks

- [x] **T1 — PostgreSQL Aggregation Query** — *Abweichung vom Story-Text (mit User-Zustimmung):* Statt Qdrant Scroll/Filter wird `engram_dictionary` in PostgreSQL aggregiert. `AdminOperations.get_engram_stats()` nutzt zwei `GROUP BY`-Queries (Layer mit `COALESCE(layer, 'working_memory')`, Strength-Buckets via `CASE`). Indices `idx_engram_dictionary_bank_layer_status` und `idx_engram_dictionary_bank_strength` bestehen bereits — konstante Kosten unabhängig von der Bank-Größe. Folgt dem `get_bank_stats()`-Pattern. Nur `status = 'active'` zählt; archivierte/decayed Engrams werden ausgeschlossen.

- [x] **T2 — Dataplane Endpoint** — `GET /v1/default/banks/{bank_id}/engrams/stats` in `http.py` (nach `/stats`-Endpoint). Pydantic Response-Model `EngramStatsResponse` mit `EngramLayerStats` für `working_memory`/`buffer`/`neocortex` + `total` + `strength_distribution` (weak/moderate/strong). `MemoryEngine.get_engram_stats()` delegiert zu `AdminOperations`.

- [x] **T3 — CP API Route** — `src/app/api/engrams/stats/route.ts`. GET-Handler mit `bank_id` Query-Parameter. Direkter `fetch()` zur Dataplane (wie `/api/config/route.ts`, um SDK-Regeneration zu vermeiden).

- [x] **T4 — CP Client erweitern** — `src/lib/api.ts`: `EngramLayerStats` + `EngramStatsResponse` Interfaces, Methode `getEngramStats(bankId)` mit `cache: "no-store"`.
