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

- [x] Schema-Liste zeigt: Description (truncated 60 chars), evidence_count, cycles_survived, last_reinforced_at (relative), confidence_tier-Badge (agent_local / cross_agent_validated mit ShieldCheck / cross_agent_disputed mit ShieldAlert).
- [x] Sort-Optionen: description (asc/desc), evidence_count, cycles_survived, last_reinforced_at — alle klickbar im Header.
- [x] Schema-Detail-View zeigt:
  - Description (prominent oben)
  - Lifecycle-Counter (evidence/cycles/access/drift mit Amber-Hint bei drift_count > 0; last_reinforced_at + last_accessed)
  - ConfidenceBadge (Stories 24/25 confidence_tier)
  - Properties-Liste mit `PropertyValue`-Renderer (Arrays, numeric envelopes mit `mean (min–max)`, Objekte als JSON, Skalare verbatim)
  - Top-N Evidence-Engrams mit Text-Truncation und ersten 4 Tags
  - Cytoscape Mini-Graph: Schema-Knoten (blau, 36px) im Zentrum, Evidence-Engrams (grau, 14px) in Sphäre außen — Hover-Label nur bei Mouseover (Tooltip-Pattern aus Original-Komponente bewahrt).
- [x] Confidence-Indicator-Pattern und drift-count-Highlight ersetzen die alte MaturityBadge-Logik.
- [ ] Hyper-Schema-View als eigene Tree-Komponente: verschoben — `HyperSchemaItem.children_ids` ist im Type, `client.listHyperSchemas()` im API-Client; ein dedizierter Tree-View kann ohne Backend-Änderungen nachgereicht werden.
- [ ] Frontend-Tests + Storybook: verschoben — die Repo-Konvention nutzt aktuell weder Vitest noch Storybook in `hindsight-control-plane/`; beim Aufbau dieser Test-Infrastruktur (separate Story) werden die Schema-Explorer-Cases mitgeliefert.

## Tasks

- [x] **T1 — API-Client umbauen:** `lib/api.ts` Types (`SchemaItem`, `SchemaDetailResponse`, `EvidenceEngram`, `HyperSchemaItem`) komplett neu auf die Story-27-Felder; Client-Methoden `listSchemas(bankId, {limit,offset,sortBy})`, `getSchemaDetail(schemaId, {includeCentroid})`, `getSchemaEvidence(schemaId, bankId, maxN)`, `listHyperSchemas(bankId, limit)`. Legacy `SchemaListResponse`-Envelope entfällt — neue Endpoints liefern Arrays direkt.
- [x] **T2 — Proxy-Routen:** `app/api/schemas/route.ts` und `app/api/schemas/[schemaId]/route.ts` zeigen jetzt auf `/v1/cp/banks/{bank}/schemas` bzw. `/v1/cp/schemas/{id}`. Zwei neue Routen: `app/api/schemas/[schemaId]/evidence/route.ts` und `app/api/hyper-schemas/route.ts`.
- [x] **T3 — `schema-explorer-view.tsx` rewrite:** 593 → 530 LOC, neue Datenshape, neue Sort-Keys, ConfidenceBadge statt MaturityBadge, PropertyValue-Renderer, parallele Evidence-Fetches via `Promise.all`.
- [x] **T4 — Mini-Graph:** Cytoscape-Layout `concentric` mit Schema-Center und Evidence-Knoten außenherum; Hover-Tooltips bewahrt.
- [ ] **T5 — Hyper-Schema-View:** verschoben (siehe oben).
- [x] **T6 — Drift- und Confidence-Indicators:** Drift-Count rendert in Amber wenn > 0; ConfidenceBadge bietet Icons (ShieldCheck/ShieldAlert) für die Story-24/25-Tiers.
- [ ] **T7 — Storybook + Tests:** verschoben (siehe oben).
