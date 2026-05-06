# Story 28 — Control Plane: Schema-Explorer Frontend-Adaption

## User Story

Als Control-Plane-Nutzer soll ich Schemas mit ihren neuen Feldern (Description, Properties, Evidence-Engrams, Hyper-Schema-Hierarchie) sehen und durchforsten können, damit die brain-inspirierte Architektur sichtbar wird.

## Kontext

Das Frontend des Schema-Explorers (Epic 22) zeigt Schemas heute als "Engrams mit layer=neocortex". Mit Epic 25 sind Schemas eigene Entitäten — die Detail-View, die Liste und die Mini-Graph-Visualisierung müssen umgebaut werden. Daten kommen über die Backend-Endpoints aus Story 27.

## Bestehende Codebasis

- **Control Plane Frontend:** `controlplane-ui/src/views/SchemaExplorer.tsx` (aus Epic 22).
- **Schema-Listen-Komponente:** `SchemaList.tsx`, `SchemaDetail.tsx`, `SchemaMiniGraph.tsx`.
- **API-Client:** `controlplane-ui/src/api/cp.ts` — wird auf neue Endpoints (Story 27) umgebogen.

## Akzeptanzkriterien

- [ ] Schema-Liste zeigt: Description (truncated), evidence_count, last_reinforced_at, cycles_survived, status-Badge
- [ ] Sort-Optionen: by evidence_count desc, by last_reinforced_at desc, by description alphabetisch
- [ ] Schema-Detail-View zeigt:
  - Description (Klartext, prominent)
  - Properties (Key-Value-Liste, je nach Tag-Type unterschiedlich gerendert: kategorial mit Modus + Konfidenz, numerisch mit Range, etc.)
  - Top-N Evidence-Engrams mit Content (klickbar → Engram-Detail)
  - Hyper-Schema-Parent-Link (falls vorhanden)
  - Drift-Stats: drift_count, last_drifted_at (aus Story 22)
  - Cross-Agent-Confidence-Tier (für Shared-Schemas, aus Stories 24+25)
- [ ] Hyper-Schema-View: Tree der spezialisierten Sub-Schemas
- [ ] Mini-Graph: Cytoscape-Komponente zeigt Schema + Top-N Evidence-Engrams als Knoten, edges zwischen Schema und Engrams (logisch über UUID-Array, gerendert als visuelle Edges)
- [ ] Frontend-Tests + Storybook

## Tasks

- [ ] **T1 — API-Client umbauen:** `cp.ts::listSchemas`, `getSchema(id)`, `getEvidence(id)`, `listHyperSchemas` auf neue Endpoints (Story 27).
- [ ] **T2 — `SchemaList.tsx` umbauen:** Neue Spalten, neue Sort-Optionen, Status-Badge (active/archived/cross_agent_validated/cross_agent_disputed).
- [ ] **T3 — `SchemaDetail.tsx` umbauen:** Description prominent, Properties-Renderer pro Tag-Type, Evidence-Engrams-Liste, Hyper-Schema-Link.
- [ ] **T4 — `SchemaMiniGraph.tsx` umbauen:** Cytoscape-Layout: Schema-Zentrum, Evidence-Engrams in Sphäre drumherum, Hyper-Schema oberhalb falls vorhanden.
- [ ] **T5 — Hyper-Schema-View:** Neue Komponente `HyperSchemaTree.tsx` mit Tree-View und expand/collapse.
- [ ] **T6 — Drift- und Confidence-Indicators:** Visuelle Hints für drift_count > 0 und confidence_tier-Werte.
- [ ] **T7 — Storybook + Tests:** Story-Cases für Schema-Detail mit allen Feldern + Edge-Cases (Schema ohne Evidence, Hyper-Schema mit 0 Children, Disputed-Schema mit conflicting Properties).
