# Epic 20 — Control Plane: Bank Profile & System Configuration

> Der Operator sieht welches Model, welcher Provider und welche Infrastruktur hinter der Bank arbeitet.

## Ziel

Erweiterung der bestehenden Bank Profile View um System-Konfigurationsdaten. Der Operator soll auf einen Blick sehen: welcher LLM Provider und welches Model genutzt wird, wie das Tier-Routing konfiguriert ist, welche Embedding- und Reranker-Modelle aktiv sind, ob alle Datenbank-Verbindungen stehen, und wie der NCR konfiguriert ist. Dafür wird ein neuer Dataplane-Endpoint (`/config`) benötigt.

## Design-Entscheidungen

**Erweiterung, kein neues View:** Die System-Konfiguration gehört logisch ins Bank Profile — es ist Information über die Bank und ihre Umgebung. Eine neue Section "System Configuration" wird unterhalb der bestehenden Stats eingefügt.

**Neuer API Endpoint:** Die Config-Daten sind aktuell nur in Environment Variables und der `Config` Klasse verfügbar. Ein neuer Endpoint `GET /v1/default/config` exponiert sie read-only. Kein Write-Access — Konfiguration bleibt über Env-Vars und config.py.

## Bestehende Codebasis (Control Plane)

| Datei | Rolle |
|---|---|
| `src/components/bank-profile-view.tsx` | Bank Profile: Name, Disposition, Background, Stats, Operations |
| `src/lib/api.ts` | ControlPlaneClient — `getBankProfile()`, `getBankStats()` |

## Bestehende Codebasis (Dataplane API)

| Datei | Rolle |
|---|---|
| `hindsight_api/config.py` | Config Klasse mit allen Settings |
| `hindsight_api/engine/llm_routing.py` | ModelTier, PROVIDER_TIER_MODELS, resolve_llm_config() |
| `hindsight_api/api/http.py` | Bestehende Endpoints |

## Scope

- Neuer Dataplane Endpoint: `GET /v1/default/config`
- CP API Route: `/api/config`
- Bank Profile View um "System Configuration" Section erweitern
- LLM Config Cards (Provider, Model, Tier-Routing)
- Infrastructure Status Badges (DB Connections)
- NCR Config Anzeige

## Nicht in Scope

- Config-Änderung über das UI (bleibt Env-Vars)
- Per-Bank Model Override UI (→ Epic 17 muss zuerst existieren)
- Health-Checks mit Latenz-Messung (wäre nett, aber out of scope)

## Abhängigkeiten

- Epic 03 (LLM Routing) — Tier-Routing muss konfiguriert sein
- Epic 19 (CP Engram Metadata) — empfohlen als Vorgänger, nicht streng erforderlich

## Referenzen

- `concept.md` → Kapitel 16 (LLM Routing)
- `control-plane-extension-plan.md` → Story 6

## Stories

1. [System Config API Endpoint](story-01-config-endpoint.md)
2. [Bank Profile View erweitern](story-02-profile-extension.md)
