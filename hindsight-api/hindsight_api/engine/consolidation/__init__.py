"""Consolidation Pipeline — Epic 25 (3-phase CLS shape).

Modules:

- consolidation1:        C1 — Working Memory → Buffer (post-session promotion)
- c2_pattern_recognition: C2 — HDBSCAN cluster detection, fingerprint maturation,
                              R4 partition (reinforcement vs creation)
- c2_schema_writer:      C2 — saga-pattern persist (Neo4j+Qdrant) for new schemas
                              and weighted-centroid reinforcement of existing ones
- c2_decay:              C2 — buffer-engram decay re-evaluation
                              (composite = thalamus_overall × decay)
- c3_schema_restructure: C3 — R3 hyper-schema bildung + R5 schema death
- ncr_orchestrator:      Glues C1/C2/C3 (+ optional shared promotion) per bank
                              with advisory locking and persists ncr_runs rows

The legacy 5-phase shape (DecayProcessor / StrengthenProcessor /
SchemaProcessor / EngramSchemaProcessor) was retired in Epic 25 Story 18
once buffer→neocortex promotion ceased to exist (Story 02).
"""
