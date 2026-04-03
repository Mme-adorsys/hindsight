# Epic 09 — Weak Connections & Synaptic Tagging

> Co-Activation Tracking, Association Windows, mode-abhängiges Traversal.

## Ziel

Schwache Verbindungen sind ein eigener Informationsträger — nicht "fast gelöschte" Links, sondern Brücken für assoziatives Denken, Schema-Erkennung und Serendipität. Epic 09 implementiert die Erzeugung und Nutzung dieser Weak Connections: co_activated Links bei Recall (Engrams die wiederholt zusammen abgerufen werden), temporal_proximity Links bei Retain (bereits in Epic 05 vorbereitet), und mode-abhängiges Traversal (Exploration folgt Weak Links, Precision ignoriert sie).

## Bestehende Codebasis

- **Neo4j Relationships:** CO_ACTIVATED, TEMPORAL_PROXIMITY (aus Epic 01) — Types definiert.
- **co_activation Helper:** `link_creation.py` (aus Epic 05) — `create_co_activation_link()` Interface vorbereitet.
- **Temporal Proximity Links:** `link_creation.py` (aus Epic 05) — Erzeugt bei Retain.
- **EngramRetriever:** `search/engram_retrieval.py` (aus Epic 07) — Neo4j Traversal.
- **ModeConfig:** `session/mode_config.py` (aus Epic 06) — `weak_link_policy: ignore/follow/prefer`.
- **Working Context:** `session/working_context.py` (aus Epic 08) — Active Engrams.

## Scope

- Co-Activation Tracking bei jedem Recall
- Co-Activation Link Erstellung bei Schwellwert-Überschreitung
- Mode-abhängiges Weak-Link Traversal im EngramRetriever
- Association Window im Working Context (STC-Mechanismus)

## Nicht in Scope

- Schema Formation (→ Epic 13) — nutzt Weak Links, erstellt sie aber nicht
- Consolidation Decay von Weak Links (→ Epic 12)

## Abhängigkeiten

- Epic 01 (Neo4j) — Relationship-Types
- Epic 05 (Retain Pipeline) — temporal_proximity Links, co_activation Helper
- Epic 06 (Session Layer) — ModeConfig.weak_link_policy
- Epic 07 (EngramRetriever) — Traversal-Logik
- Epic 08 (Working Context) — Active Engrams für Co-Activation Tracking

## Stories

1. [Co-Activation Tracking & Link Creation](story-01-co-activation.md)
2. [Mode-aware Weak-Link Traversal](story-02-weak-link-traversal.md)
3. [Association Window (STC)](story-03-association-window.md)
