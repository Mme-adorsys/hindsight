# Story 02 — Semantic Trigger & Qdrant Integration (RF2 + RF3)

## User Story

Als System soll Reconsolidation durch Semantic Similarity getriggert werden (nicht nur exakten Entity-Match), und dafür Qdrant statt 12 SQL-Queries nutzen.

## Kontext

Hindsight triggert Opinion Reinforcement bei exaktem Entity-Match → 12 parallele SQL-Queries (4 Methoden × 3 fact_types). Wir nutzen stattdessen Qdrant für einen einzelnen Similarity-Check: Cosine Similarity ≥ 0.6 reicht als Trigger. Das ist semantisch reicher (findet auch reformulierte Inhalte) und computationally günstiger.

## Bestehende Codebasis

- **reflect_async:** `memory_engine.py` — Entity-Match basierter Trigger.
- **Qdrant Client:** `engine/qdrant_client.py` (aus Epic 01) — Similarity Search.
- **Engram Dictionary:** `engine/engram_repository.py` (aus Epic 01) — FullEngram Lookup.

## Akzeptanzkriterien

- [x] Reconsolidation-Trigger: Qdrant Similarity ≥ 0.6 ODER Entity-Match (beide gelten)
- [x] Ein Qdrant-Query statt 12 SQL-Queries für Kandidaten-Suche
- [x] Kandidaten werden aus Qdrant geholt, dann gegen Priority Queue gefiltert
- [x] Messbare Latenz-Verbesserung gegenüber Hindsight (12 Queries → 1)

## Tasks

- [x] **T1 — Qdrant Similarity Trigger:** Neue Funktion `find_reconsolidation_candidates(qdrant_client, query_embedding, threshold=0.6, limit=50) → list[CandidateEngram]`. Qdrant Similarity Search mit Score-Threshold. Return: Engram-IDs + Similarity Scores.
- [x] **T2 — Entity-Match als Fallback:** Bestehender Entity-Match bleibt als sekundärer Trigger. Merge: Qdrant-Kandidaten ∪ Entity-Match-Kandidaten (dedupliziert). Qdrant-Treffer bekommen Score-Bonus.
- [x] **T3 — reflect_async umstellen:** In `memory_engine.py`: Alten 12-Query Retrieval durch `find_reconsolidation_candidates()` ersetzen. Kandidaten → Priority Queue (Story 01) → Top-N → LLM Reconsolidation.
- [x] **T4 — Reconsolidation LLM Prompt:** LLM-Prompt erweitern: Nicht nur "update opinion" sondern generisch "evaluate and update this memory given new context". Input: Engram Content + Neue Information + Similarity Score. Output: Updated Content + Action (confirm/modify/flag_contradiction).
- [x] **T5 — Unit Tests:** Qdrant Trigger bei Similarity ≥ 0.6. Kein Trigger bei < 0.6. Entity-Match Fallback funktioniert. Merge dedupliziert korrekt. LLM Prompt korrekt formatiert.
