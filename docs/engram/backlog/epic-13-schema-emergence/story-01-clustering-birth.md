# Story 01 — Clustering & Birth (R1)

## User Story

Als System soll NCR Phase 3 Cluster von Engrams erkennen die gemeinsame Nachbarn teilen und daraus Schema-Kandidaten erzeugen.

## Kontext

R1 — Wenn 3+ Engrams M+ gemeinsame Nachbarn teilen (über shared Entities und semantische Ähnlichkeit), bilden sie einen Cluster-Kandidaten. Das ist der "Birth"-Schritt: Ein potenzielles Schema wird erkannt, aber noch nicht als Schema instanziiert (→ R2 Maturation entscheidet).

## Bestehende Codebasis

- **Neo4j Client:** `engine/neo4j_client.py` (aus Epic 01) — Cypher Queries.
- **Engram Dictionary:** Neocortex-Layer Engrams als Basis für Clustering.

## Akzeptanzkriterien

- [x] Clustering-Algorithmus findet Engram-Gruppen mit M+ gemeinsamen Nachbarn
- [x] Minimale Cluster-Größe: 3 Engrams (konfigurierbar)
- [x] Minimale gemeinsame Nachbarn: 2 (konfigurierbar, M-Parameter)
- [x] Cluster-Kandidaten werden als temporäre Neo4j-Nodes mit type='cluster_candidate' gespeichert
- [x] Existierende Cluster-Kandidaten werden aktualisiert (nicht dupliziert)

## Tasks

- [x] **T1 — Community Detection Cypher:** Cypher-Query der Engram-Cluster identifiziert: `MATCH (a:Engram)-[r]-(shared)-[r2]-(b:Engram) WHERE a <> b AND a.layer='neocortex' AND b.layer='neocortex'`. Gruppiert nach shared Entities + semantische Nachbarschaft. Returns: Cluster-Sets (list of Engram-ID Sets).
- [x] **T2 — Cluster Validation:** Für jeden erkannten Cluster: Prüfe Mindest-Kriterien (≥ 3 Engrams, ≥ M gemeinsame Nachbarn). Berechne Cluster-Cohesion: Durchschnittliche Pairwise Similarity der Cluster-Mitglieder.
- [x] **T3 — Cluster-Candidate Node:** In Neo4j: `CREATE (c:ClusterCandidate {id, member_ids, cohesion, created_at, ncr_cycles_survived: 0})`. MERGE: Wenn Cluster mit ≥ 80% Member-Overlap existiert → Update statt Create.
- [x] **T4 — Deduplication:** Overlapping Clusters: Wenn Cluster A und B ≥ 80% Members teilen → Merge zu einem Cluster. Der größere/kohäsivere gewinnt.
- [x] **T5 — Unit Tests:** Cluster-Erkennung mit bekanntem Graph. Mindest-Kriterien filtern kleine Cluster. Deduplication merged overlapping Clusters. Existierende Kandidaten werden updated.
