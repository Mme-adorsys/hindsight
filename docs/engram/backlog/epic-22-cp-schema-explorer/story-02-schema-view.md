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

- [ ] Neuer Sidebar-Eintrag "Schemas" mit Icon (z.B. `Network` von lucide-react)
- [ ] `?view=schemas` zeigt den Schema Explorer
- [ ] Schema-Liste als Tabelle: Label, Member Count, Maturity Badge, Avg Strength, Last Activated
- [ ] Maturity-Badges farbcodiert: Emerging (grau), Stable (blau), Dominant (grün)
- [ ] Klick auf Schema → Detail-Panel mit Member-Engram-Liste
- [ ] Mini-Graph: Schema-Node in der Mitte, Member-Engrams als verbundene Nodes (Cytoscape)
- [ ] Leerer State: Freundliche Meldung "No schemas have emerged yet. Schemas form after multiple NCR cycles."
- [ ] Loading States und Error Handling

## Tasks

- [ ] **T1 — Sidebar erweitern** — In `sidebar.tsx` neues Nav-Item: `{ id: "schemas", label: "Schemas", icon: Network }`. NavItem Type erweitern. Position: nach "Consolidation", vor "Memory Bank".

- [ ] **T2 — Router erweitern** — In `page.tsx` neuen `{view === "schemas" && ...}` Block. Titel "Schema Explorer", Beschreibung über Schema Emergence, `<SchemaExplorerView />` einbinden.

- [ ] **T3 — Component: `schema-explorer-view.tsx` — Schema List** — Oberer Teil: Tabelle mit allen Schemas. Spalten: Label, Members (Count), Maturity (Badge), Avg Strength (Progress Bar), Last Activated (relative Time). Sortierbar nach jeder Spalte. Klick auf Row → setzt Selected Schema.

- [ ] **T4 — Component: Schema Detail Panel** — Unterer Teil oder Side-Panel: Zeigt den Selected Schema. Header: Label + Maturity + Stats. Member-Liste: Engram Text-Preview (truncated), Strength, Engram-ID. Klick auf Member → Link zur Memory-Tabelle (Deep Link mit Filter).

- [ ] **T5 — Component: Schema Mini-Graph** — Cytoscape-basierte Visualisierung im Detail-Panel. Layout: Concentric (Schema in Mitte, Members drumherum). Schema-Node: größer, farbig nach Maturity. Member-Nodes: Größe proportional zu Strength. Edges: `BELONGS_TO` Relationships. Interaktiv: Hover zeigt Tooltip mit Engram-Text.

- [ ] **T6 — Empty State & Edge Cases** — Keine Schemas: Freundliche Meldung + Hinweis auf NCR. Ein Schema ohne Members (edge case nach Decay): "This schema has no remaining members" + ggf. Delete-Hint. Viele Schemas (>50): Pagination oder Load-More Button.
