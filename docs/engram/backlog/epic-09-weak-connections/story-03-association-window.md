# Story 03 — Association Window (STC)

## User Story

Als System soll ein Synaptic Tagging & Capture (STC) Mechanismus Engrams die zeitlich nah beieinander im Working Context aktiv sind, schwach verknüpfen.

## Kontext

Biologisch: Synaptic Tagging & Capture (Frey & Morris, 1997) — Synapsen die innerhalb eines Zeitfensters aktiviert werden können gemeinsam konsolidiert werden, auch wenn sie inhaltlich nicht verwandt sind. Im System: Wenn Engram A und Engram B innerhalb eines kurzen Zeitfensters im Working Context aktiv sind (beide im Focus oder Supporting Tier), wird ein temporal_proximity Link mit niedrigem Weight erstellt.

Unterschied zu temporal_proximity bei Retain (Epic 05): Dort werden neue Engrams verknüpft die zeitlich nah erstellt wurden. Hier werden bestehende Engrams verknüpft die zeitlich nah aktiviert (abgerufen) werden.

## Bestehende Codebasis

- **Working Context:** `session/working_context.py` (aus Epic 08) — Active Engrams mit `activated_at` Timestamp.
- **temporal_proximity Links:** Bereits als Neo4j Relationship-Type definiert (Epic 01).
- **CoActivationTracker:** `session/co_activation_tracker.py` (aus Story 01) — Ähnliches Pattern.

## Akzeptanzkriterien

- [x] Association Window: Engrams die innerhalb von T Minuten (default: 5) gleichzeitig im Focus/Supporting Tier sind
- [x] temporal_proximity Links werden mit niedrigem Weight (0.1) erstellt
- [x] Wiederholte Co-Aktivierung im Window stärkt den Link
- [x] Nur intra-Session (Window wird bei Session-Ende geschlossen)
- [x] Distinct von Co-Activation (Story 01): STC basiert auf Zeitfenster im Working Context, Co-Activation auf wiederholtem Recall-Ergebnis

## Tasks

- [x] **T1 — AssociationWindow Klasse:** In `session/working_context.py` oder eigenes Modul: `AssociationWindow(window_minutes=5)`. Tracked welche Engrams im Zeitfenster aktiv sind. Sliding Window über `activated_at` der EngramRefs im Working Context.
- [x] **T2 — Window Check:** `AssociationWindow.check_associations(active_engrams: ActiveEngrams) → list[tuple[str, str]]`. Prüft alle Focus + Supporting Engrams: Wenn `activated_at` Differenz ≤ window_minutes → Association Paar. Dedupliziert (kein Paar doppelt).
- [x] **T3 — Link Creation:** Für jedes Paar: temporal_proximity Link in Neo4j mit Weight 0.1. MERGE: Wenn Link existiert → `weight = min(weight + 0.05, 0.5)` (stärkt, aber capped — soll schwach bleiben).
- [x] **T4 — Periodic Check:** Association Window wird periodisch geprüft (nicht bei jedem Recall, sondern alle M Minuten oder bei jedem N-ten Recall). Konfigurierbar. Flush bei Session-Ende zusammen mit CoActivationTracker.
- [x] **T5 — Unit Tests:** Association erkannt bei Engrams im gleichen Zeitfenster. Keine Association bei Engrams außerhalb des Fensters. Weight-Stärkung bei wiederholter Association. Weight-Cap bei 0.5. Distinctness von Co-Activation.
