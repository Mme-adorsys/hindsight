# Story 16 — Recall Top-N Evidence-Auflösung

## User Story

Als Recall-Pipeline soll ich bei einem Schema-Treffer optional die Top-N Evidence-Engrams (über `evidence_engram_ids`) aus PostgreSQL laden, damit Reflect/Constructive Memory konkrete Beispiele zur Schema-Allgemeinheit liefern kann.

## Kontext

Schemas sind das "Allgemeinwissen" — wenn der User fragt "erzähl mir was über Coffee-Meetings", liefert das Schema die Generalisierung. Aber konkrete Beispiele machen die Antwort nützlicher. Über das `evidence_engram_ids`-Property (UUID-Array) können wir mit einem `WHERE id IN (...)` SQL-Query die Top-N stärksten Belege nachladen.

## Bestehende Codebasis

- **HybridRetriever:** `engine/retrieval/hybrid_retriever.py` (aus Story 15) — liefert RetrievalHit mit Schema-Daten inkl. evidence_engram_ids.
- **Engram Repository:** `engine/engram_repository.py::get_engrams_by_ids(ids: list[UUID])`.
- **Recall Orchestrator:** ruft Reflect/Constructive Memory mit dem Retrieval-Payload auf.

## Akzeptanzkriterien

- [ ] Neue Funktion `resolve_schema_evidence(hit: RetrievalHit, max_n: int = 3) -> list[Engram]`
- [ ] Lädt aus PostgreSQL die Top-N (max_n) der `evidence_engram_ids`
- [ ] Aufruf optional steuerbar (Mode-abhängig — siehe Story 17)
- [ ] Engram-Status-Filter: nur aktive Engrams (`status='active'`), archivierte werden übersprungen
- [ ] Wenn weniger als max_n aktive Engrams existieren → returned was da ist
- [ ] Wenn `evidence_engram_ids` leer → returned []
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — `resolve_schema_evidence()`:** In `engine/retrieval/evidence_resolver.py`.
- [ ] **T2 — Repository-Erweiterung:** `engram_repository.py::get_engrams_by_ids(ids, only_active=True)` mit `WHERE id IN (...) AND status='active'` (Index nutzen).
- [ ] **T3 — Recall-Orchestrator integrieren:** Bei Schema-Treffern wird `resolve_schema_evidence()` aufgerufen (default), bevor Reflect-Pipeline angesteuert wird. Output-Payload enthält Schema + Top-N Evidence.
- [ ] **T4 — Konstante:** `RECALL_DEFAULT_EVIDENCE_N = 3` in `constants.py` (separater Wert von Schema-eigenem TOP_N=5 — wir laden default nur 3 statt alle 5 Evidence-IDs für Recall-Performance).
- [ ] **T5 — Unit-Tests:** (a) Schema mit 5 evidence_ids → 3 aktive Engrams returned. (b) Schema mit 5 ids, davon 4 archived → 1 Engram returned. (c) Schema mit leerer Liste → leere Liste.
