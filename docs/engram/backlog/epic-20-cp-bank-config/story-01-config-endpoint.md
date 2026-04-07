# Story 01 — System Config API Endpoint

## User Story

Als Control Plane will ich die aktuelle System-Konfiguration der Dataplane abfragen können (LLM Provider, Tier-Routing, DB-Status, NCR-Config), damit ich sie dem Operator anzeigen kann.

## Kontext

Die Konfiguration liegt aktuell nur in `config.py` (Environment Variables) und `llm_routing.py` (Tier-Mappings). Es gibt keinen API-Endpoint der diese Informationen exponiert. Der Operator muss aktuell in die Config-Dateien schauen oder die Env-Vars prüfen. Ein read-only Endpoint löst das.

## Bestehende Codebasis

- **Config:** `hindsight_api/config.py` — `Config` Klasse mit `get_subtask_llm_provider()`, `get_subtask_llm_model()`, Embedding-Config, Reranker-Config, NCR-Config.
- **LLM Routing:** `hindsight_api/engine/llm_routing.py` — `ModelTier` Enum, `PROVIDER_TIER_MODELS` Dict, `resolve_llm_config()`.
- **HTTP API:** `hindsight_api/api/http.py` — Router für alle Endpoints.

## Akzeptanzkriterien

- [ ] `GET /v1/default/config` liefert LLM, Embedding, Reranker, DB-Status, NCR Config
- [ ] Response ist JSON mit klarer Struktur (siehe Response-Schema)
- [ ] DB-Status wird live geprüft (nicht nur "configured" sondern "connected/disconnected")
- [ ] Keine Secrets in der Response (keine API Keys, keine Connection Strings)
- [ ] Endpoint ist read-only, kein Authentication nötig (lokaler Betrieb)

## Tasks

- [ ] **T1 — Dataplane Endpoint implementieren** — Neuer Route Handler `GET /v1/default/config` in `http.py`. Sammelt Konfiguration aus `Config`, `LLMRegistry`, und prüft DB-Connectivity (PostgreSQL ping, Qdrant health, Neo4j verify_connectivity). Response-Pydantic-Model `SystemConfigResponse` mit Feldern: `llm` (provider, model, tier_routing), `embeddings` (provider, model), `reranker` (provider, model), `database` (postgres: status, qdrant: status, neo4j: status), `ncr` (enabled, interval_hours).

- [ ] **T2 — CP API Route** — Neue Route `src/app/api/config/route.ts`. GET-Handler der zur Dataplane proxied. Caching: `no-store` (Config kann sich ändern).

- [ ] **T3 — CP Client erweitern** — In `src/lib/api.ts` neue Methode `getSystemConfig()` mit typed Response. Interface `SystemConfig` mit allen Feldern.

- [ ] **T4 — Tests** — Unit-Test: Config Endpoint liefert erwartete Struktur. Integration-Test: DB-Status spiegelt tatsächlichen Zustand wider (Mock eine DB als disconnected).
