# Story 06 — C2 Schema-Fingerprint-Match (Cosine ≥ 0.85)

## User Story

Als C2-Phase soll ich vor jeder Schema-Erzeugung prüfen, ob bereits ein passendes Schema existiert (Cosine ≥ 0.85 zwischen Cluster-Centroid und Schema-Centroid), damit existierende Schemas verstärkt statt Duplikate angelegt werden.

## Kontext

Wenn ein gereifter Cluster (Story 05) als Schema-Kandidat identifiziert ist, muss zwischen "neues Schema erzeugen" (Story 09) und "bestehendes Schema verstärken" (Story 10) entschieden werden. Diese Story liefert den Match-Schritt: Qdrant-Search mit `kind="schema"` gegen den Cluster-Centroid; Treffer ≥ 0.85 → Reinforcement-Pfad, sonst → Erzeugungs-Pfad.

## Bestehende Codebasis

- **Qdrant Client:** `engine/qdrant_client.py::search(query_vector, kind="schema", limit=1)` (aus Story 03).
- **Schema Repository:** `engine/schema/schema_repository.py::get_schema(id)` (aus Story 01).

## Akzeptanzkriterien

- [ ] Neue Funktion `match_existing_schema(cluster_centroid, bank_id) -> tuple[Optional[Schema], float]`
- [ ] Qdrant-Search mit `kind="schema"`, `limit=1`, Filter auf bank_id
- [ ] Bei Cosine ≥ 0.85 → returned Schema-Objekt + Cosine-Score
- [ ] Bei Cosine < 0.85 oder kein Treffer → returned (None, best_score)
- [ ] In `c2_pattern_recognition.py` wird der Match-Schritt vor jeder Schema-Erzeugung aufgerufen
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Match-Funktion:** `engine/consolidation/c2_schema_match.py::match_existing_schema(centroid, bank_id, threshold=0.85)`.
- [ ] **T2 — Konstanten:** `SCHEMA_MATCH_THRESHOLD = 0.85` als config-konstante in `engine/consolidation/constants.py`.
- [ ] **T3 — Pipeline-Integration:** In `c2_pattern_recognition.py` nach R2-Filter: für jeden Kandidaten `match_existing_schema()` aufrufen, Ergebnis in zwei Buckets sortieren (`reinforcement` vs `creation`).
- [ ] **T4 — Logging:** Pro C2-Lauf zählen: matches (Reinforcement), no-matches (Creation).
- [ ] **T5 — Unit-Tests:** (a) Cluster ähnlich zu existierendem Schema → Match. (b) Cluster unähnlich → kein Match. (c) Bank-Isolation: Schema in Bank A wird nicht gegen Cluster in Bank B gematcht.
