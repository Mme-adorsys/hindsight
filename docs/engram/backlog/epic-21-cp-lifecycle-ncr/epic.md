# Epic 21 — Control Plane: Engram Lifecycle & NCR Dashboard

> Der Operator sieht wohin die Engrams wandern und kann die Konsolidierung steuern.

## Ziel

Zwei neue Views im Control Plane: (1) **Engram Lifecycle** — Überblick über die Layer-Verteilung (Working Memory → Buffer → Neocortex) mit Statistiken und Strength-Distribution. (2) **NCR Dashboard** — Manuelle NCR-Auslösung, Run-History, Ergebnis-Übersicht (Decay, Strengthen, Schema-Compression). Beide Views benötigen neue API Endpoints und neue Sidebar-Einträge.

## Design-Entscheidungen

**Zwei Views, ein Epic:** Engram Lifecycle und NCR Dashboard sind inhaltlich eng verknüpft — der NCR ist der Prozess der Engrams durch die Layer bewegt. Ein Operator schaut sich zuerst die Layer-Verteilung an, dann den NCR um zu verstehen warum die Verteilung so aussieht. Deshalb gehören sie zusammen.

**Neue Sidebar-Items:** Dieses Epic fügt 2 neue Navigation-Items zur Sidebar hinzu: "Engrams" und "Consolidation". Die Sidebar wächst von 6 auf 8 Items (Schema kommt in Epic 22).

**NCR History Persistence:** Aktuell werden NCR-Reports nur als Return-Value geliefert. Sie müssen in einer neuen DB-Tabelle `ncr_runs` persistiert werden, damit der NCR Dashboard History anzeigen kann.

**Engram Stats Endpoint:** Die Layer-Verteilung wird über einen neuen Aggregations-Endpoint abgefragt, nicht über das Listen aller Engrams. Performance-relevant bei großen Banks.

## Bestehende Codebasis (Control Plane)

| Datei | Rolle |
|---|---|
| `src/components/sidebar.tsx` | Navigation — aktuell 6 Items |
| `src/app/banks/[bankId]/page.tsx` | Router — rendert View basierend auf `?view=` |
| `src/lib/api.ts` | ControlPlaneClient |

## Bestehende Codebasis (Dataplane API)

| Datei | Rolle |
|---|---|
| `hindsight_api/api/http.py` | `POST /ncr/trigger` existiert bereits, liefert Report |
| `hindsight_api/engine/ncr/ncr_orchestrator.py` | NCR Orchestrator — führt 4-Phase Pipeline aus |
| `hindsight_api/engine/engram_storage.py` | Qdrant Storage mit Layer/Strength Payload |

## Scope

- Neuer Dataplane Endpoint: `GET /engrams/stats` (Layer-Verteilung, Strength-Distribution)
- NCR History: DB-Tabelle `ncr_runs`, Persistierung bei jedem Run, `GET /ncr/history` Endpoint
- CP API Routes: `/api/engrams/stats`, `/api/ncr/trigger`, `/api/ncr/history`
- Neue Component: `engram-lifecycle-view.tsx`
- Neue Component: `ncr-dashboard-view.tsx`
- Sidebar: 2 neue Items (Engrams, Consolidation)
- Router: 2 neue Views

## Nicht in Scope

- Schema-Visualisierung (→ Epic 22)
- NCR Scheduling über UI (bleibt konfigurationsgesteuert)
- Qdrant/Neo4j direkte Integration (haben eigene UIs)

## Abhängigkeiten

- Epic 12 (Consolidation Pipeline) — NCR muss implementiert sein
- Epic 02 (Engram Data Model) — Layer-Felder müssen existieren
- Epic 19 (CP Engram Metadata) — empfohlen, damit Metadata-Spalten in der Memory-Tabelle bereits sichtbar sind

## Referenzen

- `concept.md` → Kapitel 12 (Consolidation Pipeline), Kapitel 3 (Storage-Architektur)
- `control-plane-extension-plan.md` → Story 2 + Story 3

## Stories

1. [Engram Layer Statistics API](story-01-engram-stats.md)
2. [Engram Lifecycle View](story-02-lifecycle-view.md)
3. [NCR History Persistence](story-03-ncr-history.md)
4. [NCR Dashboard View](story-04-ncr-dashboard.md)
