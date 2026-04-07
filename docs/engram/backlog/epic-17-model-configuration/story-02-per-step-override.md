# Story 02 — Per-Step Override & Bank-Konfiguration

## User Story

Als Betreiber will ich für einzelne Pipeline-Schritte das Modell überschreiben und diese Konfiguration pro Bank persistieren, damit ich z.B. für eine kritische Bank Opus für Entity Disambiguation nutzen kann während der Rest auf Medium Budget läuft.

## Kontext

Layer 2 der Konfiguration: Per-Step Overrides auf Bank-Ebene. Die Bank-Konfiguration wird in PostgreSQL persistiert und bei jedem Request geladen (cached). API-Parameter pro Request können die Bank-Konfiguration temporär überschreiben.

**Prioritätsreihenfolge (höchste zuerst):**
1. API-Parameter (pro Request)
2. Bank-Konfiguration (persistent)
3. Environment Variables
4. Budget-Profil Default

## Bestehende Codebasis

- **Config:** `config.py` — get_subtask_llm_provider, get_subtask_llm_model (Environment-Variable basiert).
- **Memory Engine:** `engine/memory_engine.py` — Bank-basierte Operationen.

## Akzeptanzkriterien

- [ ] Bank-Model-Config Tabelle in PostgreSQL: bank_id, budget_profile, step_overrides (JSONB)
- [ ] Alembic Migration für neue Tabelle
- [ ] CRUD für Bank-Model-Config (get, set, update)
- [ ] Caching: Bank-Config wird pro Request geladen, max 5 Minuten cached
- [ ] Prioritätsreihenfolge: Request > Bank > Env > Profile Default
- [ ] Admin-API Endpunkt: GET/PUT /v1/banks/{bank_id}/model-config
- [ ] Default: Neue Banks starten mit MID_BUDGET, keine Overrides

## Tasks

- [ ] **T1 — Bank-Model-Config Tabelle:** Alembic Migration: `bank_model_config` Tabelle mit bank_id (PK, FK), budget_profile (VARCHAR, DEFAULT 'mid'), step_overrides (JSONB, DEFAULT '{}').
- [ ] **T2 — BankModelConfig Repository:** CRUD-Klasse: get_config(bank_id) → BudgetProfile mit Overrides angewendet. set_config(bank_id, profile, overrides). Cache mit TTL 5 Minuten (dict-basiert, kein Redis).
- [ ] **T3 — Resolve-Chain:** In resolve_llm_config(): 1) Check Request-Parameter Override, 2) Check Bank-Config Override für diesen Step, 3) Check Env-Var für diesen Step, 4) Fallback auf Budget-Profil Default.
- [ ] **T4 — Admin API:** GET /v1/banks/{bank_id}/model-config → aktuelle Konfiguration mit effektivem Modell pro Step. PUT /v1/banks/{bank_id}/model-config → budget_profile und/oder step_overrides setzen. Validation: nur gültige PipelineStep Keys und ModelTier Values.
- [ ] **T5 — Tests:** Prioritätsreihenfolge vollständig testen (4 Ebenen). Cache-Invalidierung nach PUT. Default-Verhalten für neue Banks. Invalid Override Keys/Values abfangen.
