# Story 01 — ExtractedFact/ProcessedFact Extension (R1)

## User Story

Als System brauche ich Tags und Thalamus Scores als Felder in ExtractedFact und ProcessedFact, damit die nachfolgenden Pipeline-Schritte diese Informationen nutzen können.

## Kontext

Hindsight's `ExtractedFact` hat ein rigides `fact_type: Literal["world", "experience", "opinion"]`. Wir ersetzen das durch ein flexibles `tags: List[str]` System. Zusätzlich fließen die Thalamus Scores (aus Epic 04) als Metadaten durch die gesamte Pipeline. Die bestehenden `fact_type` Felder bleiben als Fallback erhalten (backward compat), werden aber durch Tags abgelöst.

## Bestehende Codebasis

- **ExtractedFact:** `hindsight_api/engine/retain/types.py` — Dataclass mit `fact_text`, `fact_type`, `entities`, `occurred_start/end`, `where`, `causal_relations`, `content_index`, `chunk_index`, `context`, `mentioned_at`, `metadata`.
- **ProcessedFact:** `hindsight_api/engine/retain/types.py` — Erweitert ExtractedFact um `embedding: list[float]`.
- **Fact Extraction:** `hindsight_api/engine/retain/fact_extraction.py` — `extract_facts_from_contents()` erstellt ExtractedFact. LLM-Prompt liefert `fact_type`.
- **Fact Storage:** `hindsight_api/engine/retain/fact_storage.py` — `insert_facts_batch()`. Schreibt `fact_type` in `memory_units.fact_type`.
- **ThalamusScores:** `hindsight_api/engine/retain/types.py` (aus Epic 02) — Dataclass mit novelty, surprise, task_relevance, emotional_valence, overall.

## Akzeptanzkriterien

- [ ] ExtractedFact hat neues Feld `tags: list[str]` (default: leere Liste)
- [ ] ExtractedFact hat neues Feld `thalamus_scores: ThalamusScores | None` (default: None)
- [ ] ProcessedFact erbt beide neuen Felder
- [ ] Fact Extraction Prompt generiert Tags statt nur fact_type
- [ ] fact_type wird aus Tags abgeleitet (backward compat): wenn "opinion" in tags → fact_type="opinion", etc.
- [ ] Thalamus Scores aus dem Gate (Epic 04) werden in ExtractedFact eingefügt
- [ ] insert_facts_batch schreibt Tags als JSONB in die Dictionary-Tabelle (nicht in memory_units)
- [ ] Bestehende Pipeline funktioniert weiterhin ohne Tags (leere Liste = kein Filter)

## Tasks

- [ ] **T1 — ExtractedFact erweitern:** In `hindsight_api/engine/retain/types.py`: `tags: list[str] = field(default_factory=list)` und `thalamus_scores: ThalamusScores | None = None` hinzufügen. Beide optional mit Defaults für backward compat.
- [ ] **T2 — Fact Extraction Prompt anpassen:** In `fact_extraction.py`: LLM-Prompt erweitern. Statt nur `fact_type` auch `tags` extrahieren lassen. Tags sind frei-text Labels wie "personal", "technical", "goal", "preference", "location", "temporal", "causal". Der Prompt soll 1-5 Tags pro Fakt generieren. `fact_type` wird weiterhin als erstes Tag abgeleitet (world/experience/opinion).
- [ ] **T3 — Parsing der Tags:** In `fact_extraction.py`: Response-Parsing erweitern um `tags` Feld. Fallback: Wenn LLM keine Tags liefert → `[fact_type]` als einziger Tag.
- [ ] **T4 — Thalamus Scores durchreichen:** In `orchestrator.py`: Die Thalamus Scores, die der Gate-Filter (Epic 04) berechnet hat, in jede `ExtractedFact` Instanz einfügen. Scores kommen als Parameter aus `retain_batch_async` → `orchestrator.retain_batch()`. Alle Facts eines Batches bekommen dieselben Thalamus Scores (da sie aus derselben Episode stammen).
- [ ] **T5 — Storage-Erweiterung:** In `fact_storage.py`: Tags und Thalamus Scores in die Engram Dictionary Tabelle (aus Epic 01) schreiben, NICHT in `memory_units`. Die `memory_units` Tabelle behält `fact_type` für backward compat. Die Dictionary-Tabelle hat die erweiterten Felder (tags als JSONB, thalamus_scores als JSONB).
- [ ] **T6 — Unit Tests:** Tags-Extraktion mit bekanntem Input. Thalamus Score Propagation durch Pipeline. fact_type Ableitung aus Tags. Fallback bei fehlenden Tags.
