# Story 02 — Mode-aware MPFP Patterns (S2)

## User Story

Als System sollen die MPFP-Patterns konfigurierbar pro Session-Mode sein, damit Precision-Retrieval andere Graph-Traversal-Pfade nutzt als Exploration-Retrieval.

## Kontext

Hindsight hat 7 hardcoded MPFP-Patterns (5 semantic-seeded + 2 temporal-seeded). Alle Queries nutzen identische Patterns. Wir machen die Pattern-Sets mode-abhängig: Precision → kurze, hochpräzise Patterns. Exploration → lange, niedrigschwellige Patterns. Analogy → Schema-Link Patterns. Validation → Causal + Contradiction Patterns.

## Bestehende Codebasis

- **MPFPConfig:** `search/mpfp_retrieval.py` — Definiert Patterns als Listen von Edge-Types. Aktuell hardcoded.
- **mpfp_traverse_async:** `search/mpfp_retrieval.py` — Traversiert entlang Patterns mit Threshold Pruning und Top-k Normalisierung.
- **ModeConfig:** `engine/session/mode_config.py` (aus Epic 06) — Mode → Config Resolution.
- **Neo4j Relationship-Types:** SEMANTIC, TEMPORAL, ENTITY, CAUSAL, CO_ACTIVATED, TEMPORAL_PROXIMITY, SCHEMA, CONTRADICTION (aus Epic 01).

## Akzeptanzkriterien

- [x] Jeder Mode hat ein eigenes MPFP Pattern-Set
- [x] Precision: Kurze Patterns (1-2 Hops), hohe Thresholds
- [x] Exploration: Lange Patterns (2-3 Hops), niedrige Thresholds, inkl. co_activated + temporal_proximity
- [x] Analogy: Schema-Link Patterns (schema→entity, schema→semantic)
- [x] Validation: Causal + Contradiction Patterns (causes→entity, contradiction→semantic)
- [x] Pattern-Sets per Config überladbar (nicht nur hardcoded)
- [x] Bestehende BFS-Retrieval bleibt unverändert (nur MPFP bekommt Mode-Awareness)

## Tasks

- [x] **T1 — MPFPPatternSet Dataclass:** In `mpfp_retrieval.py` oder neuem Modul: `MPFPPatternSet` mit `semantic_patterns: list[list[str]]`, `temporal_patterns: list[list[str]]`, `threshold: float`, `top_k: int`. Immutable.
- [x] **T2 — Mode Pattern Registry:** `MODE_PATTERNS: dict[RetrievalMode, MPFPPatternSet]` mit: **Precision** → kurze Patterns: `[["semantic"], ["entity", "semantic"]]`, threshold=0.01, top_k=10. **Exploration** → lange Patterns: `[["semantic", "semantic"], ["entity", "temporal"], ["co_activated", "semantic"], ["temporal_proximity", "entity"]]`, threshold=0.0001, top_k=30. **Analogy** → `[["schema", "entity"], ["schema", "semantic"], ["semantic", "schema"]]`, threshold=0.001, top_k=20. **Validation** → `[["causes", "entity"], ["caused_by", "semantic"], ["contradiction", "semantic"]]`, threshold=0.001, top_k=20.
- [x] **T3 — MPFPGraphRetriever Mode-aware:** `retrieve()` Signatur um `mode: RetrievalMode` Parameter erweitern. Pattern-Set aus Registry laden statt hardcoded. Threshold und Top-k aus Pattern-Set.
- [x] **T4 — retrieve_parallel Integration:** In `retrieval.py`: Mode an `MPFPGraphRetriever.retrieve()` durchreichen. Mode kommt aus ModeConfig (via Session Layer, Epic 06).
- [x] **T5 — Unit Tests:** Pattern-Sets pro Mode validieren. MPFP Traversal mit Precision-Patterns (kurz, wenig Ergebnisse) vs. Exploration-Patterns (lang, viele Ergebnisse). Neue Edge-Types (co_activated, schema, contradiction) in Patterns.
