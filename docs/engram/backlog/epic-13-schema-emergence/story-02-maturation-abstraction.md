# Story 02 — Maturation & Abstraction (R2 + R3)

## User Story

Als System sollen Cluster-Kandidaten erst nach K NCR-Zyklen zu echten Schemas reifen, und dann gemeinsame Properties als Schema-Properties extrahiert werden.

## Kontext

R2 — Ein Cluster wird erst zum Schema-Kandidaten nachdem er K NCR-Zyklen überlebt hat ohne zu decayen. Einmalige Cluster werden ignoriert — nur wiederkehrende Muster werden zu Schemas. R3 — Gemeinsame Properties der Cluster-Engrams werden extrahiert und als Schema-Properties codiert. Das Schema abstrahiert das Gemeinsame.

## Akzeptanzkriterien

- [ ] Cluster-Kandidaten: ncr_cycles_survived wird bei jedem NCR +1
- [ ] Maturation-Threshold: K=3 NCR-Zyklen (konfigurierbar)
- [ ] Gereifte Cluster → Schema-Node in Neo4j (type='schema')
- [ ] Schema-Properties: Gemeinsame Tags, Entities, abstrahierter Content
- [ ] LLM extrahiert die Abstraktion (Medium-Tier): "Was haben diese Engrams gemeinsam?"
- [ ] Schema-Links zu allen Member-Engrams (SCHEMA Relationship)

## Tasks

- [ ] **T1 — Maturation Check:** Für alle ClusterCandidate Nodes: `ncr_cycles_survived += 1`. Wenn ≥ K → Candidate wird zur Maturation weitergereicht. Wenn Cluster-Cohesion gesunken (Members archived) → Reset ncr_cycles_survived.
- [ ] **T2 — Abstraction via LLM:** LLM-Call (Medium-Tier): Input: Content aller Member-Engrams. Prompt: "Identifiziere das gemeinsame Muster/Thema dieser Fakten. Abstrahiere eine allgemeine Aussage." Output: Schema-Content (abstrahierte Beschreibung) + Schema-Tags.
- [ ] **T3 — Schema-Node Creation:** Neo4j: `CREATE (s:Schema {id, content, tags, strength: 0.3, created_at, type: 'schema'})`. Für jedes Member-Engram: `CREATE (e)-[:SCHEMA {weight: 1.0}]->(s)`. Schema-Embedding in Qdrant (aus abstrahiertem Content). Dictionary-Eintrag mit layer='neocortex'.
- [ ] **T4 — ClusterCandidate Cleanup:** Nach Schema-Creation: ClusterCandidate Node löschen. Members behalten ihre eigenen Nodes + Links — Schema ist zusätzliche Abstraktion, nicht Ersatz.
- [ ] **T5 — Unit Tests:** Maturation nach K Zyklen. Keine Maturation bei < K. Abstraktion erzeugt sinnvollen Schema-Content (LLM Mock). Schema-Links zu allen Members. Cleanup entfernt Candidate.
