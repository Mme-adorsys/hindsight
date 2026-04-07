# Story 02 — Thalamus Integration mit erweiterten Feldern

## User Story

Als System will ich dass der Thalamus Filter die neuen API-Felder (Expectation, Outcome, Context) direkt nutzt statt auf Session-Attribute angewiesen zu sein, damit die Score-Berechnung zuverlässiger und caller-gesteuert funktioniert.

## Kontext

Bisher liest der Thalamus seine Inputs aus der Session (session.current_expectation, session.task_context). Mit Epic 15 kommen diese Werte direkt vom Caller pro Item. Das ist präziser: jedes Item hat seinen eigenen Context und seine eigene Expectation, nicht eine Session-weite Erwartung.

Die Session bleibt relevant für die Mode-abhängige Gewichtung und Thresholds — aber die Score-Inputs kommen jetzt item-level.

## Bestehende Codebasis

- **Retain Orchestrator:** `engine/retain/orchestrator.py` — Thalamus-Aufruf im Retain-Flow.
- **Memory Engine:** `engine/memory_engine.py` — retain_batch_async() Einstiegspunkt.
- **RetainContentDict:** `engine/retain/types.py` — Transportiert expectation, outcome, context durch Pipeline.

## Akzeptanzkriterien

- [x] Thalamus score() akzeptiert item-level Felder (content, context, expectation, outcome) statt nur Session
- [x] Session wird nur noch für Mode (Gewichtung/Threshold) genutzt, nicht für Score-Inputs
- [x] Orchestrator übergibt RetainContentDict Felder an Thalamus
- [x] Fallback-Hierarchie: Item-Feld > Session-Feld > Neutral-Default
- [x] Retain-Flow: Thalamus erhält Felder aus MemoryItem ODER aus R0-Output (StructuredUnit)
- [x] Integration-Tests: Retain mit Expectation+Outcome → Thalamus-Scores korrekt berechnet

## Tasks

- [x] **T1 — Orchestrator Anpassung:** Thalamus-Aufruf in orchestrator.py: statt `thalamus.score(text, session)` → `thalamus.score(content=item.content, context=item.context, expectation=item.expectation, outcome=item.outcome, session=session, bank_id=bank_id)`.
- [x] **T2 — Fallback-Hierarchie:** In ThalamusFilter.score(): Context = item.context OR session.task_context OR None. Expectation = item.expectation OR session.current_expectation OR None. Outcome = item.outcome OR None (kein Session-Fallback). Dokumentation der Hierarchie.
- [x] **T3 — R0-Integration:** Wenn R0 StructuredUnits produziert (EXPERIENCE-Typ): expectation und outcome aus der StructuredUnit an Thalamus weitergeben. Wenn R0 FACT produziert: kein expectation/outcome → Thalamus nutzt Fallbacks.
- [x] **T4 — Tests:** Integration-Test: Retain mit explizitem Expectation+Outcome → Surprise korrekt berechnet. Integration-Test: Retain ohne Expectation → Surprise = 0.5 (Fallback). Fallback-Hierarchie Test: Item-Feld überschreibt Session-Feld.
