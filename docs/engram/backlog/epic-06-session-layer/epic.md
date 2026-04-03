# Epic 06 — Session Layer

> Transientes Steuerungsobjekt: 4 Modi konfigurieren das gesamte Memory-Verhalten.

## Ziel

Die Session ist das zentrale Steuerungsobjekt des Memory-Systems. Sie lebt transient im Application Layer (nicht persistiert), wird bei Beginn einer Agent-Session erzeugt und bei Ende verworfen. Der Session Mode (Precision/Exploration/Analogy/Validation) konfiguriert alle nachgelagerten Systeme: Retain-Thresholds, Retrieval-Patterns, Traversal-Depth, Construction-Stil, Reconsolidation-Aggressivität.

Dual Control: Mode wird sowohl explizit (Agent/User) als auch automatisch (System-Signale wie Surprise, Prediction Error) gesetzt.

## Bestehende Codebasis (Hindsight)

- **MemoryEngine:** `hindsight_api/engine/memory_engine.py` — Zentrale Engine. Hat bereits `bank_id` und `llm_config` als Kontext. Kein Session-Konzept vorhanden.
- **Interface:** `hindsight_api/engine/interface.py` — `MemoryEngineInterface` ABC. Methoden: `retain_batch_async()`, `recall_async()`, `reflect_async()`. Kein Session-Parameter.
- **Bank Profile:** `hindsight_api/engine/retain/bank_utils.py` — `BankProfile` mit Disposition. Ähnliches Konzept, aber persistiert und bank-global statt session-transient.
- **Config:** `hindsight_api/config.py` — `HindsightConfig`. Thresholds und Weights hardcoded oder per Env-Var.

**Aus vorherigen Epics:**
- Epic 02: `Session` Dataclass mit `mode`, `task_context`, `current_expectation`, Enum `RetrievalMode`
- Epic 02: `Episode` Dataclass

## Scope

- Session-Objekt mit Mode-abhängiger Konfiguration
- Mode-spezifische Config-Profile (Thresholds, Weights, Traversal-Depth)
- Dual Control: Expliziter Mode-Set + Automatische Mode-Shifts
- Integration als optionaler Parameter in MemoryEngineInterface
- Session Lifecycle Management (Create, Update Mode, Get Config, End)

## Nicht in Scope

- Working Context (→ Epic 08)
- Prediction Error Detection (→ Epic 11)
- Reconsolidation-Steuerung (→ Epic 10)

## Abhängigkeiten

- Epic 02 (Engram Data Model) — Session, Episode, RetrievalMode Dataclasses

## Referenzen

- `concept.md` → Abschnitt 7 (Session Layer)
- `concept.md` → Abschnitt 8 (S5 — Session-Mode steuert Disposition)

## Stories

1. [Mode Configuration Profiles](story-01-mode-config.md)
2. [Session Lifecycle & Dual Control](story-02-lifecycle-dual-control.md)
3. [MemoryEngine Integration](story-03-engine-integration.md)
