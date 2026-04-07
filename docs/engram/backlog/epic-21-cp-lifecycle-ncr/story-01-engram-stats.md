# Story 01 — Engram Layer Statistics API

## User Story

Als Control Plane will ich aggregierte Statistiken über die Layer-Verteilung und Strength-Distribution einer Bank abfragen können, damit ich dem Operator einen Lifecycle-Überblick geben kann ohne alle Engrams einzeln zu laden.

## Kontext

Die Engine speichert pro Engram ein `layer` Feld (null=Working Memory, "buffer", "neocortex") und ein `strength` Feld (0.0–1.0) in Qdrant. Um eine Layer-Verteilung anzuzeigen, braucht man eine Aggregation — nicht die volle Liste. Bei Banks mit Tausenden Engrams wäre ein Full-List-Call zu langsam.

## Bestehende Codebasis

- **Qdrant Storage:** `hindsight_api/engine/engram_storage.py` — `EngramStorageService` mit Qdrant-Client. Layer und Strength sind Payload-Felder.
- **HTTP API:** `hindsight_api/api/http.py` — Router. Stats-Endpoint existiert nur als `getBankStats()` (Memory Count, etc.).

## Akzeptanzkriterien

- [ ] `GET /v1/default/banks/{bank_id}/engrams/stats` liefert Layer-Counts, Avg-Strength pro Layer, Strength-Distribution
- [ ] Response ist performant (Aggregation, nicht Full-List)
- [ ] Leere Bank liefert Null-Counts, keine Fehler
- [ ] Response-Schema ist dokumentiert

## Tasks

- [ ] **T1 — Qdrant Aggregation Query** — In `EngramStorageService` eine Methode `get_layer_statistics(bank_id)` implementieren. Nutzt Qdrant Scroll/Filter um Layer-Counts zu ermitteln. Für Strength-Distribution: 3 Qdrant Filter-Queries (strength < 0.3, 0.3–0.7, > 0.7) oder ein Scroll mit Client-Side Aggregation (je nach Qdrant-API-Support für Aggregation).

- [ ] **T2 — Dataplane Endpoint** — Neuer Route Handler `GET /v1/default/banks/{bank_id}/engrams/stats` in `http.py`. Response-Model `EngramStatsResponse`: `layers` (working_memory, buffer, neocortex — je count + avg_strength), `total`, `strength_distribution` (weak, moderate, strong counts).

- [ ] **T3 — CP API Route** — Neue Route `src/app/api/engrams/stats/route.ts`. GET-Handler mit `bank_id` Parameter. Proxy zur Dataplane.

- [ ] **T4 — CP Client erweitern** — In `src/lib/api.ts` neue Methode `getEngramStats(bankId: string)` mit typed Response.
