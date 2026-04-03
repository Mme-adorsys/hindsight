# Story 01 — Co-Activation Tracking & Link Creation

## User Story

Als System soll ich tracken welche Engrams wiederholt zusammen abgerufen werden, und bei Schwellwert-Überschreitung co_activated Links erstellen.

## Kontext

Biologisch: Synapsen die wiederholt gleichzeitig feuern bilden stärkere Verbindungen (Hebb'sche Regel). Im System: Wenn Engram A und Engram B bei mehreren Recall-Operationen gemeinsam im Ergebnis-Set auftauchen, entsteht ein co_activated Link. Initial schwach (low weight), wird stärker mit jeder Co-Activation.

## Bestehende Codebasis

- **Working Context:** `session/working_context.py` (aus Epic 08) — Active Engrams in 3 Tiers.
- **co_activation Helper:** `retain/link_creation.py` (aus Epic 05) — `create_co_activation_link(neo4j, from_id, to_id, weight)` vorbereitet.
- **recall_async:** `memory_engine.py` — Liefert ScoredResult Liste.
- **Neo4j Client:** `engine/neo4j_client.py` (aus Epic 01) — Für Link-Erstellung.

## Akzeptanzkriterien

- [ ] Nach jedem Recall: Co-Activation Counter für Engram-Paare im Working Context aktualisieren
- [ ] Bei Counter ≥ 3 (konfigurierbar): co_activated Link in Neo4j erstellen
- [ ] Link-Weight basierend auf Co-Activation Häufigkeit (mehr = stärker)
- [ ] Bestehende co_activated Links werden verstärkt (MERGE mit Weight-Update)
- [ ] Co-Activation nur für Focus + Supporting Tier (Peripheral zu schwach)

## Tasks

- [ ] **T1 — CoActivationTracker:** Neues Modul `engine/session/co_activation_tracker.py`. Klasse `CoActivationTracker` mit In-Memory Counter: `dict[tuple[str, str], int]` (Engram-ID Paar → Count). Symmetrisch: (A,B) == (B,A). Methode `track_recall(engram_ids: list[str])`: Für jedes Paar in der Liste → Counter +1.
- [ ] **T2 — Threshold-basierte Link Creation:** `CoActivationTracker.flush_to_neo4j(neo4j_client, threshold=3)`. Alle Paare mit Count ≥ Threshold → `create_co_activation_link()`. Weight = `min(count / 10, 1.0)` (normalisiert). MERGE: Wenn Link existiert → Weight aufaddieren.
- [ ] **T3 — Integration in recall_async:** In `memory_engine.py`: Nach Retrieval-Ergebnis → Engram-IDs aus Focus + Supporting Tier extrahieren → `co_activation_tracker.track_recall(ids)`. Tracker lebt im Working Context (pro Session).
- [ ] **T4 — Periodic Flush:** Tracker flusht nicht nach jedem Recall (zu viele DB-Writes), sondern periodisch: Alle N Recalls oder bei Session-Ende. Default N=5. Konfigurierbar über ModeConfig.
- [ ] **T5 — Unit Tests:** Counter-Inkrement bei wiederholtem Recall. Threshold: Link erst bei Count ≥ 3. Weight-Normalisierung. MERGE: Bestehender Link wird stärker. Symmetrie: (A,B) == (B,A). Flush bei Session-Ende.
