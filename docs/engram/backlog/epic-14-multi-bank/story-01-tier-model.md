# Story 01 — 3-Tier Bank Model (B1)

## User Story

Als System brauche ich eine klare Tier-Zuordnung für Banks, damit Isolationsregeln und Consolidation-Pfade definiert sind.

## Kontext

Jede Bank hat einen Tier: Session (Tier 1), Dictionary (Tier 2), oder Shared (Tier 3). Tier bestimmt: Welche Datenbank, welche Zugriffsregeln, welcher Consolidation-Pfad. Agents können nur ihre eigene Session + Dictionary Bank lesen, und die Shared Bank. Cross-Agent Read geht ausschließlich über Shared.

## Bestehende Codebasis

- **banks Tabelle:** PostgreSQL — `id, name, created_at`. Kein Tier-Feld.
- **BankProfile:** `retain/bank_utils.py` — Pro-Bank Metadata.

## Akzeptanzkriterien

- [ ] `banks` Tabelle um `tier: Literal['session', 'dictionary', 'shared']` erweitert
- [ ] Jeder Agent hat genau 1 Session Bank + 1 Dictionary Bank
- [ ] Genau 1 Shared Bank existiert (global)
- [ ] Zugriffsregeln: Agent → eigene Session + eigene Dictionary + Shared. Keine Cross-Agent Reads.
- [ ] Bank-Erstellung: Bei Agent-Registration automatisch Session + Dictionary Bank anlegen

## Tasks

- [ ] **T1 — Alembic Migration:** `tier` Column auf `banks` Tabelle. Default: 'session' (backward compat). Enum Type in PostgreSQL.
- [ ] **T2 — BankTier Enum:** `engine/models.py` oder neues Modul: `BankTier(Enum): SESSION, DICTIONARY, SHARED`.
- [ ] **T3 — BankProfile Extension:** `tier: BankTier` zu BankProfile hinzufügen. `get_bank_profile()` lädt Tier.
- [ ] **T4 — Bank Factory:** `create_agent_banks(agent_id) → tuple[session_bank_id, dictionary_bank_id]`. Erzeugt Session + Dictionary Bank für einen neuen Agent. Shared Bank wird bei System-Init einmalig erzeugt (`ensure_shared_bank()`).
- [ ] **T5 — Access Control:** Middleware oder Helper: `verify_bank_access(agent_id, bank_id) → bool`. Agent darf nur eigene Banks + Shared lesen. Shared ist read-only für Agents (Writes nur durch Consolidation).
- [ ] **T6 — Unit Tests:** Migration läuft sauber. Bank-Erstellung erzeugt korrekte Tiers. Access Control: Agent kann eigene Bank lesen, nicht fremde. Shared Bank existiert und ist lesbar.
