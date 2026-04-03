# Epic 08 — Working Context

> Transientes PFC-Äquivalent: Goal Stack, Active Engrams (3 Tiers), Episodic Buffer, Inference Layer.

## Ziel

Der Working Context ist der Workspace während laufender Agent-Tasks. Er hält den aktiven Kontext zusammen — welche Engrams gerade relevant sind, welche Ziele verfolgt werden, welche Episoden in dieser Session stattgefunden haben, und welche Inferenzen laufen. Transient: Wird bei Session-Start erzeugt, bei Session-Ende verworfen. Relevante Inhalte fließen über Retain in Engrams.

## Bestehende Codebasis

- **Session:** `engine/session/session_manager.py` (aus Epic 06) — Session mit Episode Buffer.
- **recall_async:** `engine/memory_engine.py` — Retrieval liefert Ergebnisse, aber es gibt keinen "aktiven Kontext" der über Queries hinweg persistiert.
- **Episode:** `engine/retain/types.py` (aus Epic 02) — Episode Dataclass.

## Scope

- WorkingContext Datenstruktur (Goal Stack, 3-Tier Active Engrams, Episodic Buffer, Inference Layer)
- Population aus Retrieval-Ergebnissen
- Tiering-Logik (Focus → Supporting → Peripheral)
- Decay / Refresh innerhalb einer Session
- Lifecycle (Create, Populate, Update, Flush)

## Nicht in Scope

- Constructive Memory Inference (→ Epic 11)
- Prediction Error Detection (→ Epic 11)
- Session Persistence (Working Context ist transient by design)

## Abhängigkeiten

- Epic 06 (Session Layer) — Session als Container

## Stories

1. [WorkingContext Data Structure](story-01-data-structure.md)
2. [Population & Tiering](story-02-population-tiering.md)
3. [Lifecycle & Decay](story-03-lifecycle-decay.md)
