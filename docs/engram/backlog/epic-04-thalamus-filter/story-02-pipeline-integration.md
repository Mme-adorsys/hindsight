# Story 02 — Retain Pipeline Integration

## User Story

Als System soll der Thalamus Filter vor der Retain Pipeline geschaltet sein, damit irrelevante Episoden verworfen werden bevor sie teure Verarbeitung durchlaufen.

## Kontext

Aktuell geht jeder Input direkt in die Retain Pipeline (`retain_batch_async` → Orchestrator). Der Thalamus Filter wird als Gate davor geschaltet: Score berechnen → unter Threshold verwerfen → darüber mit Thalamus Scores angereichert an die Pipeline weiterreichen.

## Bestehende Codebasis

- **Einstiegspunkt:** `hindsight_api/engine/memory_engine.py` → `retain_batch_async(content, ...)`. Hier wird der Filter eingeklinkt.
- **Orchestrator:** `hindsight_api/engine/retain/orchestrator.py` — Bekommt angereicherte Daten (mit Thalamus Scores).
- **Fact Extraction:** `hindsight_api/engine/retain/fact_extraction.py` — ExtractedFact hat jetzt `thalamus_scores` Feld (aus Epic 02).

## Akzeptanzkriterien

- [ ] Thalamus Filter wird in `retain_batch_async` VOR dem Orchestrator aufgerufen
- [ ] Unter Threshold: Episode wird verworfen, Logging "dropped by thalamus"
- [ ] Über Threshold: Thalamus Scores werden an die Pipeline durchgereicht
- [ ] Thalamus Scores landen in ExtractedFact.thalamus_scores
- [ ] Wenn keine Session übergeben wird → Default-Session (Precision Mode)
- [ ] Performance: Thalamus Scoring darf die Retain Pipeline nicht signifikant verlangsamen

## Tasks

- [ ] **T1 — ThalamusFilter in MemoryEngine integrieren:** In `memory_engine.py` den ThalamusFilter als Dependency injizieren (neben Qdrant Client, Embedding Provider). Initialisierung in `__init__` oder Factory.
- [ ] **T2 — Gate in retain_batch_async:** Vor dem Orchestrator-Aufruf: `scores = await thalamus.score(content, session)`. Wenn `scores.overall < threshold_for_mode(session.mode)` → Log "Thalamus: dropped (score={scores.overall}, threshold={threshold})" → Return ohne Verarbeitung.
- [ ] **T3 — Scores durchreichen:** Wenn Score über Threshold: Thalamus Scores als Teil des Retain-Kontexts an den Orchestrator übergeben. Orchestrator reicht sie an `fact_extraction.py` weiter. In `fact_extraction.py`: LLM-extrahierte Thalamus Scores werden mit den Gate-Scores verglichen/ergänzt — die Gate-Scores (heuristisch) sind Basis, LLM kann verfeinern.
- [ ] **T4 — Metriken/Logging:** Strukturiertes Logging: Anzahl gefilterte vs durchgelassene Episoden pro Session. Durchschnittlicher Thalamus Score. Verteilung der Scores über die 4 Dimensionen. Optional: Prometheus Metrics wenn vorhanden.
- [ ] **T5 — Integration Test:** Episode mit hoher Novelty → durchgelassen, Thalamus Scores im Engram. Episode mit niedriger Novelty (Duplikat) → gefiltert. Mode wechseln → Threshold ändert sich.
