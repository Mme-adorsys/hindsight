# Epic 07 — Search & Retrieval Extension

> S1-S6: Mode-aware Retrieval, EngramRetriever, erweiterte Scoring-Formel.

## Ziel

Hindsight's Retrieval Pipeline basiert auf 4-Way Parallel Retrieval (Semantic + BM25 + Graph + Temporal), RRF Fusion, Cross-Encoder Reranking, und MMR Diversification — alles gegen PostgreSQL+pgvector. Epic 07 erweitert die Pipeline um: Tags statt fact_type Filter, mode-aware MPFP Patterns, Thalamus-Score Pre-Filter + Scoring, Strength-modulierte Recency, Session-Mode Steuerung, und den neuen EngramRetriever der Neo4j + Qdrant orchestriert.

## Bestehende Codebasis (Hindsight)

**Search Pipeline:**
- `engine/search/retrieval.py` — 4-Way Parallel Orchestration: `retrieve_parallel()`. Semantic (pgvector), BM25 (tsquery), Graph (MPFP/BFS), Temporal. Alles via `asyncio.gather()`.
- `engine/search/graph_retrieval.py` — `GraphRetriever` ABC mit `retrieve()`. Implementierungen: `BFSGraphRetriever` (spreading activation), `MPFPGraphRetriever`.
- `engine/search/mpfp_retrieval.py` — Meta-Path Forward Push. 7 Patterns (5 semantic + 2 temporal). Lazy Edge Loading. `mpfp_traverse_async()`.
- `engine/search/scoring.py` — `calculate_recency_weight()` (365-Tage Half-Life), `calculate_frequency_weight()`, `calculate_temporal_proximity()`.
- `engine/search/fusion.py` — `reciprocal_rank_fusion()` (RRF k=60).
- `engine/search/reranking.py` — `CrossEncoderReranker.rerank()`.
- `engine/search/types.py` — `RetrievalResult`, `MergedCandidate`, `ScoredResult`.
- `engine/memory_engine.py` — `recall_async()` (line ~1330). Iteriert über fact_types, ruft `retrieve_parallel()` pro Type.

**Aus vorherigen Epics:**
- Epic 01: Qdrant Client, Neo4j Client, Engram Dictionary
- Epic 02: FullEngram mit Tags, Strength, ThalamusScores
- Epic 06: SessionManager, ModeConfig, ScoringWeights

## Scope

- S1: Tags-basierte Filterung statt fact_type
- S2: Mode-aware MPFP Patterns (konfigurierbar pro Mode)
- S3: Thalamus-Score Pre-Filter + gewichtete Scoring-Formel
- S4: Strength-modulierte Recency-Decay
- S5: Session-Mode steuert Retrieval (statt Disposition)
- S6: EngramRetriever (Neo4j + Qdrant, gleiches GraphRetriever-Interface)

## Nicht in Scope

- Constructive Memory (→ Epic 11)
- Working Context Population (→ Epic 08)
- Co-Activation Link Creation bei Recall (→ Epic 09)

## Abhängigkeiten

- Epic 01 (Hybrid Storage) — Qdrant + Neo4j verfügbar
- Epic 02 (Engram Model) — FullEngram, Tags, Strength
- Epic 06 (Session Layer) — ModeConfig, ScoringWeights

## Stories

1. [Tags-basierte Filterung](story-01-tag-filter.md) (S1)
2. [Mode-aware MPFP Patterns](story-02-mode-aware-mpfp.md) (S2)
3. [Extended Scoring Formula](story-03-extended-scoring.md) (S3 + S4)
4. [EngramRetriever](story-04-engram-retriever.md) (S6)
5. [Session-Mode Routing](story-05-session-mode-routing.md) (S5)
6. [Retrieval Integration Tests](story-06-retrieval-tests.md) (Milestone Validation)
