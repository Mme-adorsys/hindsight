# Epic 19 — Control Plane: Engram Metadata & Session Modes

> Das CP zeigt was die Engine weiß: Thalamus-Scores, Engram-Stärke, Layer — und der Operator kann den Session Mode wählen.

## Ziel

Erweiterung der bestehenden Control Plane Views um die brain-inspirierten Engram-Metadaten. Die Engine speichert bereits Thalamus Scores, Strength, Layer, Access Count — aber das CP zeigt sie nicht. Parallel dazu wird der Session Mode Selector in Recall und Reflect eingebaut, damit der Operator die 4 Modi (Precision, Exploration, Analogy, Validation) aus dem UI steuern kann.

## Design-Entscheidungen

**Scope:** Nur bestehende Views erweitern, kein neues Routing, keine neuen Sidebar-Items. Das minimiert Risiko und liefert sofort sichtbaren Mehrwert.

**Engram Metadata in Memory-Tabelle:** Die `data-view.tsx` bekommt neue Spalten für Layer (Badge), Strength (Progress Bar), Access Count. Das Memory-Detail-Panel bekommt Thalamus-Scores als 4 horizontale Bars oder Radar-Chart.

**Session Mode Selector:** Ein shared Component (`session-mode-selector.tsx`) das in `search-debug-view.tsx` (Recall) und `think-view.tsx` (Reflect) eingebettet wird. Die API akzeptiert `mode` bereits — es fehlt nur das UI.

## Bestehende Codebasis (Control Plane)

| Datei | Rolle |
|---|---|
| `src/components/data-view.tsx` | Memory-Tabelle (World/Experience/Opinion) |
| `src/components/search-debug-view.tsx` | Recall Analyzer UI |
| `src/components/think-view.tsx` | Reflect UI |
| `src/lib/api.ts` | ControlPlaneClient — alle API-Aufrufe |
| `src/app/api/list/route.ts` | Proxy für Memory List |
| `src/app/api/recall/route.ts` | Proxy für Recall |
| `src/app/api/reflect/route.ts` | Proxy für Reflect |

## Bestehende Codebasis (Dataplane API)

| Datei | Rolle |
|---|---|
| `hindsight_api/api/http.py` | `ListMemoryUnitsResponse` — aktuell ohne Strength/Layer/Thalamus |
| `hindsight_api/engine/engram_types.py` | `ThalamusScores` Dataclass |
| `hindsight_api/engine/engram_storage.py` | Qdrant Payload enthält Strength, Layer, Thalamus Scores |

## Scope

- Memory List API Response um Engram-Felder erweitern (Strength, Layer, Access Count, Thalamus Scores)
- `data-view.tsx` um neue Spalten erweitern
- Memory Detail Panel um Thalamus-Score-Visualisierung erweitern
- Shared Session Mode Selector Component
- Recall View um Mode Selector erweitern
- Reflect View um Mode Selector erweitern
- CP API Routes um `mode` Parameter erweitern

## Nicht in Scope

- Neue Sidebar-Items (→ Epic 21, 23)
- Neue API Endpoints (→ Epic 21, 23)
- Engram Lifecycle Übersicht (→ Epic 21)
- Schema Visualisierung (→ Epic 22)

## Abhängigkeiten

- Epic 02 (Engram Data Model) — Engram-Felder müssen existieren
- Epic 04 (Thalamus Filter) — Thalamus Scores müssen berechnet werden
- Epic 06 (Session Layer) — Session Modes müssen in der Engine implementiert sein

## Referenzen

- `concept.md` → Kapitel 4 (Engram Data Model), Kapitel 5 (Thalamus Filter), Kapitel 7 (Session Layer)
- `control-plane-extension-plan.md` → Story 1 + Story 4

## Stories

1. [Memory Detail erweitern — Engram Metadata sichtbar](story-01-memory-detail.md)
2. [Session Mode Selector — Recall und Reflect](story-02-session-mode.md)
