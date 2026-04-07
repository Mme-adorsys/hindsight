# Story 02 — Write Conflict Resolution (B2)

## User Story

Als System soll bei der Promotion von Engrams in die Shared Bank erkannt werden ob Konflikte mit bestehenden Engrams existieren, und diese aufgelöst werden.

## Kontext

B2 — Wenn ein Agent-Engram in die Shared Bank promoviert wird und dort ein semantisch ähnliches Engram existiert (Similarity ≥ 0.85): (1) Kein Widerspruch → Merge (stärkeres wird Basis), (2) Widerspruch → Höherer Score gewinnt, schwächeres bekommt Contradiction-Link, (3) Gleicher Score → Neueres gewinnt.

## Bestehende Codebasis

- **Qdrant Client:** `engine/qdrant_client.py` — Similarity Search für Conflict Detection.
- **Neo4j Relationships:** CONTRADICTION (aus Epic 01) — Link-Type definiert.
- **Engram Dictionary:** `engine/engram_repository.py` — FullEngram mit Strength, Scores.

## Akzeptanzkriterien

- [x] Conflict Detection: Qdrant Similarity ≥ 0.85 gegen Shared Bank vor Write
- [x] No Conflict → normaler Write
- [x] No Contradiction → Merge: Stärkeres Engram wird Basis, schwächeres liefert Kontext-Update
- [x] Contradiction → Höherer Thalamus-Score gewinnt, Contradiction-Link zum Verlierer
- [x] Same Score → Neueres gewinnt (Recency)
- [x] Keine Information geht verloren (schwächeres Engram bleibt mit Link)

## Tasks

- [x] **T1 — Conflict Detection:** `engine/consolidation/conflict_resolution.py`. Funktion `detect_conflicts(qdrant_client, candidate_embedding, shared_bank_id, threshold=0.85) → list[ConflictCandidate]`. Qdrant Query gegen Shared Bank.
- [x] **T2 — Contradiction Detection:** LLM-Call (Small-Tier): "Do these two statements contradict each other? A: {candidate}. B: {existing}. Answer: yes/no." Wenn ja → Contradiction. Wenn nein → Merge-Kandidat.
- [x] **T3 — Merge Logic:** `merge_engrams(stronger: FullEngram, weaker: FullEngram) → FullEngram`. Stärkeres Engram: Content bleibt, Tags werden vereinigt, Strength = max(both). Schwächeres: Wird als "merged_into" markiert, behält ID aber bekommt `merged_into_id` Referenz.
- [x] **T4 — Contradiction Link:** Bei Widerspruch: Stärkeres Engram bleibt in Shared Bank. Schwächeres: Bleibt in Agent-Dictionary (wird nicht promoted). Neo4j: `CONTRADICTION` Relationship zwischen beiden mit `description` Property (was widerspricht sich).
- [x] **T5 — Score Comparison:** `resolve_conflict(candidate, existing) → Resolution`. Vergleich: `candidate.thalamus_scores.overall vs. existing.thalamus_scores.overall`. Höherer gewinnt. Gleich → Neuerer gewinnt. Resolution: MERGE, REPLACE, KEEP_EXISTING, CONTRADICTION_LINK.
- [x] **T6 — Unit Tests:** Keine Konflikte bei Similarity < 0.85. Merge bei ähnlich ohne Widerspruch. Contradiction-Link bei Widerspruch. Score-Vergleich: Höherer gewinnt. Gleich → Neuerer gewinnt.
