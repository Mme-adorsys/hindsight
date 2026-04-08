# Story 01 — Memory Detail erweitern: Engram Metadata sichtbar machen

## User Story

Als Operator will ich in der Memory-Tabelle sehen, in welchem Layer ein Engram liegt, wie stark es ist und wie oft es abgerufen wurde, damit ich den Zustand meiner Memory Bank auf einen Blick einschätzen kann.

## Kontext

Die Engine speichert pro Engram: `strength` (0.0–1.0), `layer` (null/buffer/neocortex), `access_count`, `thalamus_scores` (4 Dimensionen + Overall). Die `data-view.tsx` zeigt aktuell nur: text, type, entities, context, timestamps. Der Operator hat keine Sicht auf die brain-inspirierten Metadaten.

## Bestehende Codebasis

- **Dataplane:** `GET /v1/default/banks/{bank_id}/memories/list` → `ListMemoryUnitsResponse` (http.py). Felder kommen aus Qdrant Payload via `EngramStorageService`.
- **CP Proxy:** `src/app/api/list/route.ts` → reicht Response durch.
- **CP Client:** `src/lib/api.ts` → untypisierte Response.
- **CP View:** `src/components/data-view.tsx` → Tabelle mit World/Experience/Opinion Tabs.

## Akzeptanzkriterien

- [x] Memory List Response enthält `strength`, `layer`, `access_count`, `thalamus_scores` pro Item
- [x] `data-view.tsx` zeigt Layer als farbcodierten Badge (grau=Working, gelb=Buffer, grün=Neocortex)
- [x] `data-view.tsx` zeigt Strength als Progress Bar (rot < 0.3, gelb 0.3–0.7, grün > 0.7)
- [x] `data-view.tsx` zeigt Access Count als Zähler
- [x] Memory Detail Panel zeigt Thalamus Scores als 4 horizontale Bars + Overall
- [x] Felder sind optional — ältere Engrams ohne Scores zeigen "N/A"
- [x] Kein Breaking Change in der API

## Tasks

- [x] **T1 — Dataplane: Memory List Response erweitern** — Umgesetzt via `admin_operations.py` (LEFT JOIN `engram_dictionary` in `list_memory_units()` und `get_graph_data()`). Felder `strength`, `layer`, `access_count`, `thalamus_scores` werden pro Item mitgeliefert. Additive Änderung, backward-compat für Legacy-Einträge ohne engram_dictionary Row.

- [x] **T2 — CP API Route erweitern** — `src/app/api/list/route.ts` ist Pass-Through via `hindsightClient.listMemories()` — keine Transformation nötig, Felder kommen automatisch durch.

- [x] **T3 — CP Client typesafe machen** — `src/lib/api.ts` mit `ThalamusScores` und `MemoryListItem` Interfaces erweitert (strength, layer, access_count, thalamus_scores als nullable Felder).

- [x] **T4 — data-view.tsx: Neue Spalten** — Layer-Badge (grau/gelb/grün), Strength-Bar (rot/gelb/grün), Access-Count Column in Table View.

- [x] **T5 — Memory Detail Panel: Thalamus Scores** — `memory-detail-panel.tsx` mit Engram-Section (Layer+Strength+Accesses) + 5 Thalamus-Score-Bars (Overall, Novelty, Surprise, Task Relevance, Emotional Valence) im inPanel Mode; "N/A" Fallback für Legacy-Einträge.
