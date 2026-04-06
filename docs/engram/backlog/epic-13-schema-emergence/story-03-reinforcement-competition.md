# Story 03 — Reinforcement & Competition (R4 + R5)

## User Story

Als System sollen neue Engrams bestehende Schemas stärken (R4), und schwache Schemas die nicht verstärkt werden sterben (R5).

## Kontext

R4 — Neues Engram das zu einem bestehenden Schema-Pattern passt stärkt das Schema und erzeugt eine neue Schema-Verbindung. **Läuft inkrementell bei Retain** (nicht nur im NCR). R5 — Schwache Schemas die über mehrere NCR-Zyklen nicht verstärkt werden sterben. Verhindert Schema-Inflation.

## Bestehende Codebasis

- **Schema-Fit Check:** `retain/schema_links.py` (aus Epic 05) — `check_schema_fit_batch()` vorbereitet. Wird hier aktiviert.
- **Schema Nodes:** Neo4j (aus Story 02) — Schema-Nodes mit Strength.
- **NCR Phase 3:** `consolidation/ncr_orchestrator.py` — Ruft SchemaProcessor auf.

## Akzeptanzkriterien

- [x] R4 bei Retain: Neues Engram → Schema-Fit Check → bei Match: Schema-Strength +0.05, neuer Schema-Link
- [x] R4 Threshold: Cosine Similarity ≥ 0.7 zwischen Engram-Embedding und Schema-Embedding
- [x] R5 im NCR: Schemas mit Strength < 0.1 UND nicht verstärkt seit 5 NCR-Zyklen → gelöscht
- [x] Schema-Deletion: Node + alle Schema-Links entfernt. Member-Engrams bleiben.
- [x] Schema-Strength Tracking: `last_reinforced_at` Timestamp

## Tasks

- [x] **T1 — R4 Retain Integration:** In `retain/schema_links.py`: `check_schema_fit_batch()` aktivieren. Qdrant Similarity Search gegen Schema-Embeddings. Bei Match (≥ 0.7): Neo4j Schema-Link erstellen + Schema-Node Strength += 0.05 + `last_reinforced_at = now`.
- [x] **T2 — R5 Schema Death:** In NCR Phase 3: Alle Schema-Nodes laden. Prüfe: `strength < 0.1 AND (now - last_reinforced_at).days > 5 * ncr_interval_days`. Wenn beide: Schema-Node + alle SCHEMA-Relationships löschen. Neo4j + Qdrant + Dictionary aufräumen.
- [x] **T3 — Schema Strength Decay:** Im NCR (parallel zu Engram-Decay): Schema-Strength decayed mit 0.95 pro Zyklus (langsamer als Engrams, da Schemas stabiler sind). R4-Reinforcement wirkt dem entgegen.
- [x] **T4 — Metrics:** Tracking: Anzahl aktiver Schemas, durchschnittliche Schema-Strength, Schema-Creation-Rate, Schema-Death-Rate. Geloggt im NCRReport.
- [x] **T5 — Unit Tests:** R4: Neues Engram stärkt existierendes Schema. R4: Kein Match → kein Schema-Link. R5: Schwaches Schema wird gelöscht. R5: Starkes Schema überlebt. Schema Decay über Zyklen.
