# Story 02 — Recall API Erweiterung

## User Story

Als Caller will ich beim Recall eine Expectation und Tags mitgeben können, damit das System Surprise-Scoring bei Retrieval anwenden und gezielt nach getaggten Engrams filtern kann.

## Kontext

Der bisherige Recall-Endpunkt nutzt: query, types, budget, max_tokens, trace, mode. Die neuen Felder ermöglichen:
- **Expectation:** "Ich erwarte X" — Engrams die davon abweichen bekommen einen Surprise-Boost im Scoring
- **Tags:** Filter auf bestimmte Engram-Tags (ergänzt/ersetzt die bisherigen `types`)
- **question statt query:** Semantisch klarer. query bleibt als Alias für Backward Compatibility.

## Bestehende Codebasis

- **RecallRequest:** `api/http.py` — query, types, budget, max_tokens, trace, query_timestamp, include, mode.
- **RecallResult:** `api/http.py` — id, text, type, entities, context, occurred_start/end, mentioned_at, document_id, metadata, chunk_id.
- **RecallResponse:** `api/http.py` — results, trace, entities, chunks.

## Akzeptanzkriterien

- [ ] RecallRequest um `expectation: str | None`, `tags: list[str] | None`, `question: str | None` erweitert
- [ ] `question` als primäres Feld, `query` als Alias — beide akzeptiert, `question` hat Vorrang
- [ ] Expectation wird an Thalamus-Scoring im Retrieval-Pfad weitergereicht
- [ ] Tags-Filter: Engrams müssen ALLE angegebenen Tags haben (AND-Logik)
- [ ] RecallResult um `tags: list[str] | None`, `expectation: str | None`, `outcome: str | None` erweitert (Engram-Felder zurückgeben)
- [ ] Backward Compatibility: bestehende Calls mit `query` funktionieren unverändert
- [ ] API-Dokumentation aktualisiert

## Tasks

- [ ] **T1 — RecallRequest erweitern:** `question: str | None = None`, `expectation: str | None = None`, `tags: list[str] | None = None`. Validator: wenn sowohl query als auch question gesetzt → question hat Vorrang. Mindestens eines muss gesetzt sein.
- [ ] **T2 — Tag-Filter Logik:** In der Retrieval Pipeline (engram_dictionary.filter_entries oder Qdrant-Query) Tags als AND-Filter implementieren. PostgreSQL: `tags @> ARRAY[...]`. Qdrant: payload filter mit `must` conditions.
- [ ] **T3 — Expectation Durchreichen:** Expectation in den Retrieval-Scoring-Pfad einbauen. Wird an Thalamus-Score-Berechnung bei Retrieval übergeben (Epic 16 nutzt das).
- [ ] **T4 — RecallResult erweitern:** tags, expectation, outcome aus dem Engram Dictionary in die Response aufnehmen. Nur befüllt wenn im Engram vorhanden.
- [ ] **T5 — Tests:** Unit-Tests für question/query Alias-Logik. Tag-Filter Tests (AND, leere Tags, nicht-existierende Tags). Backward-Compatibility-Test. Expectation-Durchreichen verifizieren.
