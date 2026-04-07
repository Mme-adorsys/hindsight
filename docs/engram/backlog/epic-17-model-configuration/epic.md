# Epic 17 — Konfigurierbare Modell-Zuweisung

> Jeder Pipeline-Schritt bekommt sein eigenes Modell. Budget-Tiers geben Empfehlungen.

## Ziel

Erweiterung des LLM Routing (Epic 03) um per-Schritt Modellkonfiguration und Budget-Tier-Profile. Jeder Pipeline-Schritt (R0 Sequence Analysis, R1 Fact Extraction, R4 Entity Disambiguation, Reflect, etc.) kann ein eigenes Modell zugewiesen bekommen. Drei vordefinierte Budget-Profile (Low, Medium, High) liefern sinnvolle Defaults. Einzelne Schritte können überschrieben werden.

## Design-Entscheidungen

**2-Layer-Konfiguration:**
1. **Budget-Profil (Layer 1):** Low / Medium / High — setzt Defaults für alle Schritte
2. **Per-Step Override (Layer 2):** Einzelne Schritte können vom Budget-Profil abweichen

**Budget-Profile (Empfehlungen):**

| Schritt | Low | Medium | High |
|---------|-----|--------|------|
| R0 Sequence Analysis | SMALL (Haiku) | MEDIUM (Sonnet) | LARGE (Opus) |
| R1 Fact Extraction | SMALL | SMALL | MEDIUM |
| R4 Entity Disambiguation | SMALL | MEDIUM | LARGE |
| Thalamus Valence | — (entfällt mit E16) | — | — |
| Reflect | SMALL | MEDIUM | LARGE |
| Constructive Memory | MEDIUM | MEDIUM | LARGE |
| Schema Compression | SMALL | MEDIUM | LARGE |

**Konfigurationsebene:** Per Bank. Jede Memory Bank hat ein Budget-Profil + optionale Per-Step Overrides. Default: Medium.

**Konfigurationsquelle:**
1. API-Parameter (pro Request, höchste Priorität)
2. Bank-Konfiguration (persistent, mittlere Priorität)
3. Environment Variables (global, niedrigste Priorität)

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/engine/llm_routing.py` — ModelTier Enum, TASK_TIER_MAPPING, PROVIDER_TIER_MODELS, resolve_llm_config(), LLMRegistry.
- `hindsight-api/hindsight_api/config.py` — Config mit get_subtask_llm_provider/model.
- `hindsight-api/hindsight_api/api/http.py` — RetainRequest.mode, Budget Enum.

## Scope

- Budget-Profil Dataclass mit Tier-Mapping pro Schritt
- 3 vordefinierte Profile (LOW, MID, HIGH) als Frozen Defaults
- Per-Step Override Mechanismus
- Bank-Level Konfiguration (persistiert in PostgreSQL)
- API-Parameter für Budget-Profil pro Request
- LLMRegistry Erweiterung: resolve mit Budget-Profil statt nur Task-Tier

## Nicht in Scope

- Dynamisches Routing basierend auf Latenz/Kosten (bleibt rule-based)
- Provider-Wechsel zur Laufzeit (bleibt konfiguriert)
- Cost-Tracking / Budget-Limits (späteres Feature)

## Abhängigkeiten

- Epic 03 (LLM Routing) — wird erweitert
- Epic 15 (API Enrichment) — R0 nutzt Budget-Profil

## Referenzen

- `concept.md` → Abschnitt 16 (LLM Routing) — wird um Budget-Profile erweitert

## Stories

1. [Budget-Profile & Defaults](story-01-budget-profiles.md)
2. [Per-Step Override & Bank-Konfiguration](story-02-per-step-override.md)
3. [API & Pipeline Integration](story-03-api-integration.md)
