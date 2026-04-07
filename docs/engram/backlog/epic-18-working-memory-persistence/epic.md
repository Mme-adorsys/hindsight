# Epic 18 — Working Memory Persistence & Cache Layer

> Working Memory überlebt die Session. Der Cache wird geflushed, der Kontext bleibt warm.

## Ziel

Umstellung des Working Memory (Working Context, Epic 08) von "Session-Ende = Löschung" auf ein persistentes Modell mit vorgelagerter Cache-Schicht. Der Cache hält transiente Session-Daten (Episodic Buffer) und wird nach Session-Ende geflushed. Das Working Memory selbst (Goal Stack, Active Engrams, bestätigte Inferences) überlebt und steht der nächsten Session als warmer Kontext zur Verfügung.

## Design-Entscheidungen

**Neuroscience-Basis:** Der PFC löscht sich nicht nach jeder Task. Es gibt einen Residual-Kontext der in die nächste Aufgabe mitgenommen wird — **Priming**. Die nächste Session startet nicht bei Null, sondern mit dem Kontext der letzten Session. Über Zeit verblasst dieser Kontext (Decay), aber kurzfristig ist er sofort verfügbar.

**2-Schicht-Modell:**

```
┌─────────────────────────────────┐
│ Session Cache (transient)       │  ← Wird nach Session-Ende geflushed
│ - Episodic Buffer               │
│ - Pending Inferences            │
│ - Co-Activation Counts          │
└─────────────┬───────────────────┘
              │ flush()
┌─────────────▼───────────────────┐
│ Working Memory (persistent)     │  ← Überlebt Session-Ende
│ - Goal Stack (completed/active) │
│ - Active Engrams (3 Tiers)      │
│ - Confirmed Inferences          │
│ - Session History (letzte N)    │
└─────────────────────────────────┘
```

**Flush-Prozess (Session-Ende):**
1. Episodic Buffer → Retain Pipeline (wie bisher)
2. Pending Inferences: confirmed → Working Memory, rejected → verworfen, tentative → Working Memory mit niedrigem Confidence
3. Co-Activation Counts → Neo4j CO_ACTIVATED Links (wie bisher)
4. Cache wird geleert

**Persistence:**
- Working Memory wird in PostgreSQL gespeichert (pro Bank)
- Serialisierung als JSONB
- Loaded bei Session-Start, gespeichert bei Session-Ende und periodisch

**Offene Design-Fragen (werden in Diskussion mit Marcel geklärt):**
- **Lifetime:** Wie lange bleibt Working Memory warm? TTL-basiert? Unbegrenzt?
- **Scope:** Pro Bank? Pro Agent? Global?
- **Decay:** Sollen Active Engram Tiers zwischen Sessions decayen?

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/engine/session/working_context.py` — WorkingContext Klasse (Epic 08, noch nicht implementiert, aber im Concept definiert).
- `hindsight-api/hindsight_api/engine/session/session_manager.py` — SessionManager mit Session Lifecycle.
- `hindsight-api/hindsight_api/engine/memory_engine.py` — Session-Integration.

## Scope

- SessionCache Klasse (transiente Schicht)
- WorkingMemory Klasse (persistente Schicht)
- Flush-Prozess: Cache → Working Memory + Retain Pipeline
- PostgreSQL Persistenz für Working Memory (JSONB)
- Session-Start: Working Memory laden, Cache initialisieren
- Session-Ende: Flush + Working Memory speichern
- API: Working Memory Status abfragbar

## Nicht in Scope

- Working Context Implementierung selbst (→ Epic 08, muss zuerst fertig sein)
- Decay-Mechanik (→ Design-Entscheidung, dann hier oder separates Epic)
- Cross-Bank Working Memory Sharing (→ Epic 14)

## Abhängigkeiten

- **Epic 08** (Working Context) — wird um Persistence erweitert
- Epic 06 (Session Layer) — Session Lifecycle Integration
- Epic 01 (PostgreSQL) — Persistenz-Tabelle

## Referenzen

- `concept.md` → Abschnitt 9 (Working Context) — wird um Persistence und Cache Layer erweitert

## Stories

1. [Session Cache Layer](story-01-session-cache.md)
2. [Working Memory Persistence](story-02-wm-persistence.md)
3. [Flush-Prozess & Session Lifecycle](story-03-flush-lifecycle.md)
