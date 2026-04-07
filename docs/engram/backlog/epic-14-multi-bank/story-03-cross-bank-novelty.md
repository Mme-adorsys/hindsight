# Story 03 — Cross-Bank Novelty & Promotion (B3 + B5)

## User Story

Als System sollen Engrams basierend auf Novelty und Triggern von Agent Dictionary in die Shared Bank promoviert werden.

## Kontext

B3 — Zwei-Stufen Novelty Check: (1) Qdrant Similarity gegen Shared Bank (≥ 0.85?), (2) Wenn Match → Reinforce statt neues Engram. Wenn kein Match → neues Shared-Engram. B5 — Drei Trigger-Typen: NCR-basiert (Hauptkanal), Cross-Agent Convergence, Schema-Kandidat. Alle im NCR-Kontext.

## Bestehende Codebasis

- **NCR Orchestrator:** `consolidation/ncr_orchestrator.py` — Batch-Prozess.
- **Conflict Resolution:** `consolidation/conflict_resolution.py` (aus Story 02).
- **Qdrant Client:** `engine/qdrant_client.py` — Similarity Search.

## Akzeptanzkriterien

- [x] NCR-Trigger: Neocortex-Engrams mit Strength ≥ 0.6 werden als Promotion-Kandidaten geprüft
- [x] Novelty Check: Qdrant Similarity gegen Shared Bank
- [x] Match (≥ 0.85) → Reinforce: Strength erhöhen + Access Count
- [x] No Match → Promote: Neues Engram in Shared Bank (via Conflict Resolution)
- [x] Cross-Agent Convergence: ≥ 2 Agents mit ähnlichen Engrams → erhöhte Promotion-Priorität
- [x] Schema-Kandidat: Engram Teil eines Schemas → automatischer Shared-Kandidat

## Tasks

- [x] **T1 — Promotion Candidates:** `engine/consolidation/multi_bank_promoter.py`. Funktion `find_promotion_candidates(engram_repo, bank_id, strength_threshold=0.6) → list[FullEngram]`. Filtert Neocortex-Engrams mit Strength ≥ Threshold.
- [x] **T2 — Novelty Check:** `check_novelty(qdrant_client, candidate: FullEngram, shared_bank_id) → NoveltyResult`. Result: `NOVEL` (kein Match → promote), `REDUNDANT` (Match → reinforce), `CONTRADICTORY` (Match + Widerspruch → B2 Logic).
- [x] **T3 — Promote Flow:** Bei NOVEL: Engram in Shared Bank erstellen (Dictionary + Qdrant + Neo4j). Links kopieren oder als CROSS_BANK Reference erstellen. Initiale Shared-Strength = 0.3 (niedriger als Agent-Dictionary Strength).
- [x] **T4 — Reinforce Flow:** Bei REDUNDANT: Bestehendes Shared-Engram stärken. `strength += 0.05`. `access_count += 1`. Kein neues Engram.
- [x] **T5 — Cross-Agent Convergence Trigger:** Im NCR: Für jeden Promotion-Kandidaten prüfe ob andere Agents ähnliche Engrams haben (Qdrant Cross-Bank Query). Wenn ≥ 2 Agents: Promotion-Priority erhöhen (Strength-Threshold auf 0.4 senken).
- [x] **T6 — Schema-Trigger:** Engrams die Teil eines Schema-Clusters sind (SCHEMA Relationship in Neo4j): Automatisch als Promotion-Kandidat markiert, unabhängig von Strength.
- [x] **T7 — NCR Integration:** In NCR Orchestrator: Nach Phase 3 (Schema) → Promotion Phase. Ruft `multi_bank_promoter.promote_batch()` auf.
- [x] **T8 — Unit Tests:** Novelty: Novel → Promote. Redundant → Reinforce. Conflict → B2. Cross-Agent Convergence senkt Threshold. Schema-Trigger funktioniert. NCR Integration läuft Promotion nach Schema Phase.
