# Story 02 — Schema Explorer View

## User Story

Als Operator will ich sehen welche Wissens-Cluster (Schemas) in meiner Memory Bank emergiert sind, ihre Mitglieder einsehen und die Verbindungen als Graph visualisieren können, damit ich verstehe welches strukturierte Wissen das System aufgebaut hat.

## Kontext

Schemas sind das höchste Abstraktionslevel im Engram-Modell — sie repräsentieren wiederkehrende Muster die aus vielen Einzelerfahrungen destilliert wurden (analog zu Neocortex-Repräsentationen im Gehirn). Der Operator sollte sehen welche Schemas existieren, wie reif sie sind (emerging → stable → dominant) und welche Engrams zu einem Schema gehören.

## Bestehende Codebasis

- **CP Sidebar:** `src/components/sidebar.tsx` — nach Epic 21 bereits 8 Items.
- **CP Router:** `src/app/banks/[bankId]/page.tsx` — muss `"schemas"` View hinzufügen.
- **Cytoscape:** Bereits als Dependency vorhanden. Wird im Graph View (Bank Profile) genutzt.
- **CP Client:** `src/lib/api.ts` — `listSchemas()` und `getSchema()` aus Story 01.

## Akzeptanzkriterien

- [x] Neuer Sidebar-Eintrag "Schemas" mit `Network` Icon von lucide-react
- [x] `?view=schemas` zeigt den Schema Explorer
- [x] Schema-Liste als sortierbare Tabelle: Label, Members, Maturity Badge, Avg Strength (Progress-Bar), Last Activated (relative Time)
- [x] Maturity-Badges farbcodiert: Emerging (slate/grau), Stable (blau), Dominant (emerald/grün)
- [x] Klick auf Schema → Detail-Panel mit Member-Engram-Liste (text_preview, strength, engram_id)
- [x] Mini-Graph: Schema-Node zentral+größer (farbig nach Maturity), Member-Nodes proportional zu Strength, Cytoscape concentric Layout, Hover-Tooltips
- [x] Leerer State: Network-Icon + "No schemas have emerged yet" + NCR-Erklärung
- [x] Loading States (Skeleton-Pulse), Error State (rote Card mit Retry), Detail Loading (Spinner)

## Tasks

- [x] **T1 — Sidebar erweitern** — `sidebar.tsx`: NavItem Type um `"schemas"` erweitert (multi-line union), `Network` Icon importiert, Nav-Item zwischen "Consolidation" und "Memory Bank" eingefügt.

- [x] **T2 — Router erweitern** — `page.tsx`: NavItem Type aligned, `SchemaExplorerView` Import, `{view === "schemas" && ...}` Block mit Titel "Schema Explorer" und Beschreibung über Schema Emergence durch NCR.

- [x] **T3 — Component: Schema List** — `schema-explorer-view.tsx` mit `useBank()` + `useState`. Sortierbare Tabelle (5 Spalten) mit `SortableHeader`-Subkomponente, `compareSchemas()`-Helper. Selected-Highlight via border-l-2. Auto-Refresh 30s + manueller Refresh-Button.

- [x] **T4 — Component: Schema Detail Panel** — Card unter der Tabelle mit Schema-Header (Label + MaturityBadge), Member-Liste links (text_preview, strength %, truncated engram_id), Mini-Graph rechts. 2-Spalten-Layout (lg:grid-cols-2). Loading-Spinner während detail-fetch.

- [x] **T5 — Component: Schema Mini-Graph** — `SchemaGraph` Subkomponente mit Cytoscape direct (kein fcose nötig). Concentric Layout: Schema-Node level=2 (zentral, größer, farbig nach Maturity via MATURITY_COLORS), Member-Nodes level=1 mit `mapData(strength, 0, 1, 16, 36)` für proportionale Größe. Edges Member→Schema (entspricht :SCHEMA-Relationship). Hover zeigt text_preview. Cleanup via cy.destroy() im useEffect-return.

- [x] **T6 — Empty State & Edge Cases** — Keine Schemas: Network-Icon + "No schemas have emerged yet" + NCR-Erklärung. Schema ohne Members: "This schema has no remaining members — they may have decayed" statt Mini-Graph. Loading-Skeleton (3 pulse Bars). Error-State: rote Card mit Retry-Button.
