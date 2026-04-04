# Story 03 — Score-aware Deduplication (R3)

## User Story

Als System soll bei Duplikaterkennung der höher bewertete Fakt gewinnen, damit nicht zufällig der erste Treffer überlebt sondern der qualitativ bessere.

## Kontext

Hindsight's Deduplication erkennt Duplikate per semantischer Ähnlichkeit in einem 24h-Fenster. Bei einem Match wird der neue Fakt verworfen. Das ist zu simpel: Ein neuer Fakt mit höherem Thalamus Score (z.B. weil er in einem relevanteren Kontext aufgenommen wurde) sollte den schwächeren ersetzen können. Außerdem nutzen wir jetzt Engram Strength als weiteres Signal.

## Bestehende Codebasis

- **Deduplication:** `hindsight_api/engine/retain/deduplication.py` — `check_duplicates_batch(conn, bank_id, facts, duplicate_checker_fn)`. Gruppiert Facts in 12h-Buckets, ruft `duplicate_checker_fn` pro Bucket auf. Gibt `list[bool]` zurück (ist Duplikat ja/nein).
- **Filter:** `deduplication.py` → `filter_duplicates(facts, is_duplicate_flags)` — Entfernt Duplikate aus der Liste.
- **Duplicate Checker:** Wird extern injiziert (typisch: semantische Ähnlichkeit + Zeitfenster in der DB). Definiert in `memory_engine.py`.
- **ThalamusScores:** Jetzt auf jedem `ExtractedFact`/`ProcessedFact` (aus Story 01).
- **Engram Dictionary:** `hindsight_api/engine/engram_repository.py` (aus Epic 01) — hat `strength` Feld.

## Akzeptanzkriterien

- [ ] Bei Duplikaterkennung wird der Thalamus Overall Score verglichen
- [ ] Höherer Score gewinnt: Neuer Fakt mit höherem Score ersetzt den bestehenden
- [ ] Bei gleichem Score: Neuer Fakt gewinnt (frischere Information bevorzugt)
- [ ] Strength des bestehenden Engrams fließt als Bonus in den Vergleich ein
- [ ] Bestehende Duplikaterkennung (Similarity + Zeitfenster) bleibt Basis
- [ ] Logging: "Thalamus dedup: replacing existing (score={old} < {new})" oder "kept existing (score={old} >= {new})"

## Tasks

- [x] **T1 — DuplicateResult Dataclass:** In `deduplication.py` neue Dataclass `DuplicateResult` statt nur `bool`. Felder: `is_duplicate: bool`, `existing_unit_id: str | None`, `existing_score: float | None`, `similarity: float`. Wird vom `duplicate_checker_fn` zurückgegeben.
- [x] **T2 — Score-aware Vergleich:** In `deduplication.py` neue Funktion `resolve_duplicate(new_fact: ProcessedFact, dup_result: DuplicateResult) → DuplicateResolution`. Logik: `new_score = new_fact.thalamus_scores.overall if new_fact.thalamus_scores else 0.0`. `existing_score = dup_result.existing_score + strength_bonus` (Strength des bestehenden Engrams als Bonus, z.B. `strength * 0.1`). Wenn `new_score > existing_score` → Resolution.REPLACE. Wenn `new_score == existing_score` → Resolution.REPLACE (frischer bevorzugt). Sonst → Resolution.DROP.
- [x] **T3 — Replacement-Flow:** In `deduplication.py`: Bei Resolution.REPLACE: Bestehenden Fakt markieren für Update statt Insert. Der Orchestrator muss den bestehenden Engram updaten (Embedding, Text, Scores) statt einen neuen zu erstellen. Neue Funktion `resolve_duplicates_batch(facts, dup_results) → tuple[list[ProcessedFact], list[ReplacementAction]]`. Return: gefilterte Facts (neue + zu ersetzende) und eine Liste von Replacement-Actions.
- [x] **T4 — Orchestrator Integration:** In `orchestrator.py`: Nach `check_duplicates_batch()` statt `filter_duplicates()` jetzt `resolve_duplicates_batch()` aufrufen. Für REPLACE-Actions: Bestehenden Engram in Dictionary + Qdrant updaten statt neuen Insert. Für DROP-Actions: Wie bisher verwerfen.
- [x] **T5 — duplicate_checker_fn erweitern:** In `memory_engine.py`: Der injizierte `duplicate_checker_fn` muss jetzt `DuplicateResult` statt `bool` zurückgeben. Dazu: Auch den Thalamus Overall Score des bestehenden Engrams aus der Dictionary-Tabelle laden (JOIN). Bestehende Strength aus Dictionary laden.
- [x] **T6 — Unit Tests:** Score-Vergleich: Neuer Fakt mit höherem Score ersetzt. Gleicher Score → Neuer gewinnt. Niedrigerer Score → Bestehender bleibt. Strength-Bonus wirkt korrekt. Ohne Thalamus Scores → Fallback auf altes Verhalten (Drop).
