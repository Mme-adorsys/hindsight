# Story 05 — C2 Cluster-Fingerprint-Persistierung (R2)

## User Story

Als C2-Phase soll ich Cluster-Kandidaten zwischen Zyklen über einen Fingerprint identifizieren, damit nur Cluster die ≥ 2 C2-Zyklen überlebt haben (R2 Maturation) zu Schema-Kandidaten werden.

## Kontext

R1 findet 1× Cluster, aber Einmal-Cluster sind oft Rauschen. R2 verlangt Persistenz: Ein Cluster muss in mindestens zwei aufeinanderfolgenden C2-Läufen erkennbar bleiben, bevor ein Schema entsteht. Dazu wird pro Cluster ein Fingerprint (Centroid-Vektor + dominante Tags) persistiert; im nächsten Lauf wird Cosine-Similarity gegen den Fingerprint gemessen — Match (≥ 0.85) zählt als Survival.

## Bestehende Codebasis

- **C2 Pattern Recognition:** `engine/consolidation/c2_pattern_recognition.py` (aus Story 04).
- **PostgreSQL:** neue Tabelle `c2_cluster_fingerprints` (in dieser Story angelegt).

## Akzeptanzkriterien

- [x] Tabelle `c2_cluster_fingerprints {id UUID, bank_id, centroid VECTOR(384), dominant_tags JSONB, cycles_survived INT, created_at, last_seen_at}` mit HNSW-Cosine-Index
- [x] Pro Lauf: `match_or_create` matched gegen alle bank-eigenen Fingerprints via `<=>`-Operator (cosine distance), Match-Schwelle ≥ 0.85
- [x] Match: `cycles_survived = cycles_survived + 1`, `last_seen_at = now()`
- [x] Kein Match: INSERT mit `cycles_survived = 1`
- [x] Stale Fingerprints (`last_seen_at < now() - max_age_days * interval '1 day'`) werden via `prune_stale` gelöscht (Default 7 Tage)
- [x] Cluster mit `cycles_survived >= 2` (`MATURATION_MIN_CYCLES`) gelten als Schema-Kandidaten — `filter_matured` extrahiert sie
- [x] Unit-Tests (Repository + Pipeline-Integration); Integration-Test verschoben auf Block E (Story 19/20 Knowledge-Evolution-E2E, Test-Policy "ab Epic 05 Integration" greift bereits dort)

## Tasks

- [x] **T1 — Alembic-Migration `e25c2fingerprint_c2_cluster_fingerprints.py`** (chained auf `e25a02layer`): Tabelle mit pgvector-`vector(384)`-Spalte, HNSW-Index `vector_cosine_ops`, Bank-FK CASCADE, Multi-Tenant via `target_schema`.
- [x] **T2 — Repository:** `engine/consolidation/cluster_fingerprint_repository.py` mit `match_or_create(pool, bank_id, centroid, dominant_tags, threshold=MATCH_COSINE_THRESHOLD)` (asyncpg-Pattern via `acquire_with_retry`, pgvector-Literal `'[v1,v2,...]'::vector` weil asyncpg keinen pgvector-Codec hat), `prune_stale(pool, bank_id, max_age_days=7)`. Plus `FingerprintMatch` Outcome-Dataclass und Drift-Guard-Konstanten `MATCH_COSINE_THRESHOLD=0.85`, `DEFAULT_STALE_MAX_AGE_DAYS=7`.
- [x] **T3 — Integration in `c2_pattern_recognition.py`:** `ClusterCandidate` um `member_tags: tuple[tuple[str,...],...]` erweitert (default leeres Tupel = backwards-kompatibel). Neuer `MaturedClusterCandidate` mit `cycles_survived`, `fingerprint_id`, `matched_existing`, `is_mature`-Property. Neue async `mature_clusters(candidates, bank_id, pool)` Pipeline: pro Kandidat `compute_centroid` (aus Story 03) + `_dominant_tags` Top-5 → `match_or_create`. Plus SQLAlchemy-Klasse `C2ClusterFingerprint` in `models.py` (Vector(384) + JSONB + HNSW).
- [x] **T4 — R2-Filter:** `filter_matured(matured) -> list[MaturedClusterCandidate]` mit `MATURATION_MIN_CYCLES=2` Drift-Guard.
- [x] **T5 — Unit-Tests:** 9 Repository-Tests (`test_cluster_fingerprint_repository.py`: match-incrementiert, below-threshold-creates, empty-table, explicit-threshold-override, prune-stale-count, drift-guard) + 7 Pipeline-Tests (in `test_c2_pattern_recognition.py`: `_dominant_tags` top-N, mature_clusters first-run cycles=1, second-run cycles=2 mature, filter_matured cutoff, centroid L2-normalisiert nach mature, MATURATION_MIN_CYCLES drift-guard).

## Implementation Notes

- **DB-Choice:** PostgreSQL statt Neo4j für Fingerprints — transienter Working-State (Buffer-Side, hippocampal-scratch), nicht Cortex. pgvector `<=>` liefert cosine distance ohne Qdrant-Roundtrip.
- **pgvector-Literal-Encoding:** asyncpg hat keinen pgvector-Codec; `_format_vector_literal` serialisiert `[v1,v2,...]::vector` als String-Cast. Migration nutzt denselben Trick (`ALTER TABLE ... USING centroid::vector`) weil SQLAlchemy-Core kein nativer Vector-DDL ohne pgvector-Plugin-Import.
- **Threshold-Konsistenz:** `MATCH_COSINE_THRESHOLD = 0.85` ist derselbe Wert wie der R4 inkrementelle Schema-Fit-Check (Story 12) — Drift-Guard-Test verhindert versehentliche Divergenz.
- **Naming:** Story-Tabellenname `c2_cluster_fingerprints` (nicht legacy `cluster_candidates` aus Epic 13) markiert die Refactor-Grenze.
