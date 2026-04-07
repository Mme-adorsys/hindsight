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

- [ ] Memory List Response enthält `strength`, `layer`, `access_count`, `thalamus_scores` pro Item
- [ ] `data-view.tsx` zeigt Layer als farbcodierten Badge (grau=Working, gelb=Buffer, grün=Neocortex)
- [ ] `data-view.tsx` zeigt Strength als Progress Bar (rot < 0.3, gelb 0.3–0.7, grün > 0.7)
- [ ] `data-view.tsx` zeigt Access Count als Zähler
- [ ] Memory Detail Panel zeigt Thalamus Scores als 4 horizontale Bars + Overall
- [ ] Felder sind optional — ältere Engrams ohne Scores zeigen "N/A"
- [ ] Kein Breaking Change in der API

## Tasks

- [ ] **T1 — Dataplane: Memory List Response erweitern** — `ListMemoryUnitsResponse` in `http.py` um `strength: float | None`, `layer: str | None`, `access_count: int | None`, `thalamus_scores: object | None` erweitern. Daten aus Qdrant Payload mappen. Sicherstellen dass der `EngramStorageService` diese Felder beim List-Call mitliefert.

- [ ] **T2 — CP API Route erweitern** — `src/app/api/list/route.ts`: Die erweiterten Felder durchreichen. Keine Transformation nötig, nur sicherstellen dass die Felder im Proxy nicht verloren gehen.

- [ ] **T3 — CP Client typesafe machen** — In `src/lib/api.ts` ein `MemoryListItem` Interface definieren mit allen Feldern (bestehende + neue). `ThalamusScoresDisplay` Interface für die 5 Score-Dimensionen. Response-Type der `listMemories()`-Methode anpassen.

- [ ] **T4 — data-view.tsx: Neue Spalten** — Layer-Badge Component (3 Farben), Strength-ProgressBar Component (3 Farbstufen), Access-Count Anzeige. Spalten sind responsive — auf kleinen Screens können Layer und Strength als Icons dargestellt werden.

- [ ] **T5 — Memory Detail Panel: Thalamus Scores** — Im Detail-Panel (Click auf Memory) die 4+1 Thalamus-Scores als horizontale Bars anzeigen. Jeder Bar zeigt: Label, Wert (0.0–1.0), farbcodierte Füllung. Fallback "N/A" wenn `thalamus_scores` null ist.
