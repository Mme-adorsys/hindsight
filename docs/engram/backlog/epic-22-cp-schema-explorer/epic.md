# Epic 22 — Control Plane: Schema Explorer

> Der Operator sieht welche Wissens-Cluster emergiert sind und wie sie zusammenhängen.

## Ziel

Neues View im Control Plane: **Schema Explorer** — zeigt die Meta-Engrams (Schemas) die durch die Game-of-Life Regeln aus häufig co-aktivierten Engrams emergiert sind. Der Operator kann Schemas browsen, ihre Member-Engrams einsehen und die Schema-Verbindungen als Mini-Graph visualisieren. Dafür werden neue API Endpoints in der Dataplane benötigt.

## Design-Entscheidungen

**Graph-Visualisierung mit Cytoscape:** Das CP hat bereits `cytoscape` als Dependency (für den bestehenden Graph View in Bank Profile). Der Schema-Graph kann dieselbe Library nutzen — keine neue Dependency nötig.

**Schema Maturity Levels:** Schemas haben 3 Maturity-Stufen:
- **Emerging** — Cluster erkannt, aber noch nicht K NCR-Zyklen überlebt
- **Stable** — K NCR-Zyklen überlebt, aktiv genutzt
- **Dominant** — Stark, viele Members, häufig aktiviert

**Neuer Sidebar-Item:** "Schemas" als letztes Nav-Item. Die Sidebar hat nach diesem Epic 9 Items total.

## Bestehende Codebasis (Control Plane)

| Datei | Rolle |
|---|---|
| `src/components/sidebar.tsx` | Navigation — nach Epic 21 bereits 8 Items |
| `src/app/banks/[bankId]/page.tsx` | Router |
| `src/lib/api.ts` | ControlPlaneClient |
| `package.json` | `cytoscape` bereits als Dependency |

## Bestehende Codebasis (Dataplane API)

| Datei | Rolle |
|---|---|
| `hindsight_api/engine/ncr/engram_schema_processor.py` | Schema Processing — erstellt Schema-Nodes in Neo4j |
| `hindsight_api/engine/engram_storage.py` | Storage Layer |
| Neo4j | Schema-Nodes mit Relationships zu Member-Engrams |

## Scope

- Neuer Dataplane Endpoint: `GET /schemas` (Schema List)
- Neuer Dataplane Endpoint: `GET /schemas/{schema_id}` (Schema Detail mit Members)
- CP API Routes: `/api/schemas`, `/api/schemas/[schemaId]`
- Neue Component: `schema-explorer-view.tsx`
- Sidebar: 1 neues Item (Schemas)
- Router: 1 neues View
- Mini-Graph für Schema-Member Verbindungen (Cytoscape)

## Nicht in Scope

- Schema-Editing über UI (Schemas emergieren automatisch)
- Cross-Bank Schema Vergleich (→ Multi-Bank Feature)
- Schema-Merge UI (bleibt Engine-intern)

## Abhängigkeiten

- Epic 13 (Schema Emergence) — Schemas müssen existieren
- Epic 21 (CP Lifecycle & NCR) — empfohlen, da NCR die Schemas erzeugt
- Epic 01 (Neo4j) — Schema-Nodes liegen in Neo4j

## Referenzen

- `concept.md` → Kapitel 13 (Schema Emergence)
- `control-plane-extension-plan.md` → Story 5

## Stories

1. [Schema API Endpoints](story-01-schema-api.md)
2. [Schema Explorer View](story-02-schema-view.md)
