# Story 05 — C2 Cluster-Fingerprint-Persistierung (R2)

## User Story

Als C2-Phase soll ich Cluster-Kandidaten zwischen Zyklen über einen Fingerprint identifizieren, damit nur Cluster die ≥ 2 C2-Zyklen überlebt haben (R2 Maturation) zu Schema-Kandidaten werden.

## Kontext

R1 findet 1× Cluster, aber Einmal-Cluster sind oft Rauschen. R2 verlangt Persistenz: Ein Cluster muss in mindestens zwei aufeinanderfolgenden C2-Läufen erkennbar bleiben, bevor ein Schema entsteht. Dazu wird pro Cluster ein Fingerprint (Centroid-Vektor + dominante Tags) persistiert; im nächsten Lauf wird Cosine-Similarity gegen den Fingerprint gemessen — Match (≥ 0.85) zählt als Survival.

## Bestehende Codebasis

- **C2 Pattern Recognition:** `engine/consolidation/c2_pattern_recognition.py` (aus Story 04).
- **PostgreSQL:** neue Tabelle `c2_cluster_fingerprints` (in dieser Story angelegt).

## Akzeptanzkriterien

- [ ] Neue Tabelle `c2_cluster_fingerprints { id UUID, bank_id UUID, centroid VECTOR, dominant_tags JSONB, cycles_survived INT, created_at, last_seen_at }`
- [ ] Pro C2-Lauf: für jeden R1-Cluster wird gegen alle Fingerprints derselben Bank gematcht (Cosine ≥ 0.85)
- [ ] Match: `cycles_survived++`, `last_seen_at = now()`
- [ ] Kein Match: neuer Fingerprint angelegt, `cycles_survived = 1`
- [ ] Stale Fingerprints (`last_seen_at` > 7 Tage) werden gelöscht
- [ ] Cluster mit `cycles_survived >= 2` gelten als Schema-Kandidaten (Output für nachfolgende Stories)
- [ ] Unit-Tests + Integration-Test

## Tasks

- [ ] **T1 — Alembic-Migration `25_05_cluster_fingerprints`:** Tabelle anlegen mit pgvector-Index auf `centroid`.
- [ ] **T2 — Repository:** `engine/consolidation/cluster_fingerprint_repository.py` mit `match_or_create(bank_id, centroid, dominant_tags)`, `prune_stale(bank_id, max_age_days=7)`.
- [ ] **T3 — Integration in `c2_pattern_recognition.py`:** Nach R1-Cluster-Detection iteriert C2 die Kandidaten und ruft `match_or_create()` auf. Output: `MaturedClusterCandidate` mit Feld `cycles_survived`.
- [ ] **T4 — R2-Filter:** Funktion `filter_matured(candidates) -> list[MaturedClusterCandidate]` filtert auf `cycles_survived >= 2`.
- [ ] **T5 — Unit-Tests:** (a) Erstmaliger Cluster → cycles_survived=1, kein Schema-Kandidat. (b) Zweiter Lauf, Cluster wieder erkannt → cycles_survived=2, gilt als Kandidat. (c) Fingerprint stale → gelöscht.
