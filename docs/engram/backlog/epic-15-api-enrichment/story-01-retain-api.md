# Story 01 — Retain API Erweiterung

## User Story

Als Caller will ich beim Retain neben Content auch Expectation, Outcome und Tags mitgeben können, damit das System reichhaltigere Engrams erzeugen und bessere Thalamus-Scores berechnen kann.

## Kontext

Der bisherige Retain-Endpunkt nimmt: content, context, timestamp, metadata, document_id, entities. Das reicht für einfache Fakten, aber nicht für Erfahrungen (Expectation→Outcome) oder für die objektive Thalamus-Score-Berechnung. Die neuen Felder sind optional — Backward Compatibility bleibt gewahrt.

Der Caller soll ermutigt werden, reichhaltigen Content zu schicken (Konversationen, Zusammenfassungen, Narrative). "Lieber mehr als weniger." Das System extrahiert budget-abhängig die Struktur.

## Bestehende Codebasis

- **MemoryItem:** `api/http.py` — Pydantic Model mit content, context, timestamp, metadata, document_id, entities.
- **RetainRequest:** `api/http.py` — items: list[MemoryItem], async_, mode.
- **RetainContentDict:** `engine/retain/types.py` — Internes Dict-Format für die Pipeline.

## Akzeptanzkriterien

- [ ] MemoryItem um `expectation: str | None`, `outcome: str | None`, `tags: list[str] | None` erweitert
- [ ] RetainRequest akzeptiert die neuen Felder ohne Breaking Change
- [ ] RetainContentDict transportiert expectation, outcome, tags durch die Pipeline
- [ ] Felder werden in ExtractedFact und ProcessedFact durchgereicht
- [ ] Tags werden bei Engram-Speicherung in engram_dictionary.tags gemerged (user-supplied + auto-extracted)
- [ ] Expectation und Outcome werden im Engram Dictionary als eigene Spalten gespeichert
- [ ] API-Dokumentation (OpenAPI Schema) zeigt die neuen Felder mit Beispielen
- [ ] Backward Compatibility: bestehende Calls ohne neue Felder funktionieren unverändert

## Tasks

- [ ] **T1 — MemoryItem erweitern:** `expectation: str | None = None`, `outcome: str | None = None`, `tags: list[str] | None = None` in MemoryItem. Field-Beschreibungen mit Beispielen. Validator: tags dürfen keine Leerzeichen, max 50 Zeichen pro Tag.
- [ ] **T2 — RetainContentDict erweitern:** expectation, outcome, tags in das interne Dict-Format aufnehmen. Mapping von MemoryItem → RetainContentDict in memory_engine.py anpassen.
- [ ] **T3 — ExtractedFact/ProcessedFact erweitern:** expectation und outcome als optionale Felder. Tags-Merge-Logik: user-supplied Tags + LLM-extracted Tags = finale Tags (dedupliziert).
- [ ] **T4 — Engram Dictionary Migration:** Alembic Migration für `expectation TEXT`, `outcome TEXT` Spalten in engram_dictionary. Nullable, kein Default.
- [ ] **T5 — Engram Storage Durchreichen:** EngramStorageService.create() nimmt expectation + outcome entgegen und speichert sie in PostgreSQL + als Qdrant Payload-Felder.
- [ ] **T6 — Tests:** Unit-Tests für neue Felder (Serialisierung, Validation). Integration-Test: Retain mit expectation+outcome → Engram korrekt gespeichert. Backward-Compatibility-Test: Retain ohne neue Felder funktioniert.
