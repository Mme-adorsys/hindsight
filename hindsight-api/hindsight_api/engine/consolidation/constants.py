"""Shared constants for the consolidation pipeline (Epic 25).

These are the few magic numbers that the concept document fixes verbatim.
Centralising them here lets pipeline modules (c2_pattern_recognition,
c2_schema_match, c3_schema_restructure, …) share a single source of truth
and lets the test suite use a drift-guard parametrize against the concept.
"""

from __future__ import annotations

# concept §13 R4 — when a fresh cluster centroid (or a fresh engram, in the
# inkremental retain-time variant) lands within this cosine of an existing
# schema centroid, we **reinforce** that schema instead of creating a new
# one. Same numeric value as MATCH_COSINE_THRESHOLD in
# cluster_fingerprint_repository, but the mechanism it gates is different
# (cluster→schema rather than cluster→cluster), so the constants stay
# separate. Drift guard in tests/test_c2_schema_match.py.
SCHEMA_MATCH_THRESHOLD: float = 0.85

# concept §4.2 — schema.evidence_engram_ids is the Top-N strongest cluster
# members (Indexing Theory à la Teyler & DiScenna 1986). N=5 keeps the cortex
# pointer-array small enough to round-trip cheaply and avoids stamping every
# noisy member into the schema's audit trail. Drift guard in
# tests/test_c2_schema_writer.py.
SCHEMA_TOP_N_EVIDENCE: int = 5

# concept §13 — buffer engrams whose composite score (thalamus_overall × decay)
# falls below this threshold get archived during the C2 decay-reevaluation
# pass (Story 11). Below 0.05 the engram is effectively forgotten — its
# vector-search recall would be drowned out by stronger neighbours anyway.
# Drift guard in tests/test_c2_decay.py.
BUFFER_ARCHIVE_COMPOSITE_THRESHOLD: float = 0.05

# concept §13 R4 (incremental, Story 12). When a freshly retained engram lands
# within SCHEMA_MATCH_THRESHOLD cosine of an existing schema centroid, R4 fires
# immediately at retain time instead of waiting for the next C2 batch run
# (Tse et al. 2007 — schema-consistent consolidation in hours, not weeks).
R4_INCREMENTAL_ENABLED: bool = True

# Whether the incremental R4 reinforcement re-aggregates the schema's
# properties on every single-engram hit. False by default — one new engram
# barely shifts a multi-engram aggregation, and the property re-aggregation
# costs an extra PG round-trip. Batch R4 in C2 (Story 10) refreshes properly.
R4_INCREMENTAL_PROPERTY_REFRESH: bool = False

# concept §13 R3 (schema → hyper-schema, Story 13). Two schemas with
# centroid-cosine ≥ this threshold AND systematic property differences
# get subsumed under a HyperSchema node linked via :SPECIALIZES.
HYPER_SCHEMA_COHESION_THRESHOLD: float = 0.7

# Minimum number of property keys whose values differ for an R3 pair to
# qualify. concept §13 says "systematisch unterschiedliche Property-Werte"
# — at least one differing key keeps us out of pure-duplicate territory.
HYPER_SCHEMA_MIN_PROPERTY_DIFF: int = 1

# concept §13 R5 — Schema Death gate. A schema gets archived when both:
#   - cycles_since_last_reinforced > R5_K_CYCLES, AND
#   - evidence_count < R5_EVIDENCE_THRESHOLD
# Concept-default is K=4 cycles + threshold=5 (≈ 28 days at 7-day C3 cadence).
# We ship a more conservative bootstrap pair so a dim early-life bank
# doesn't kill schemas that are simply rarely triggered. Once a system has
# dense banks the values can be tightened toward the concept default.
R5_K_CYCLES: int = 8
R5_EVIDENCE_THRESHOLD: int = 3

# concept §13 frequency table: C3 runs every 7 days. Used by R5 to convert
# cycles-without-reinforcement into a wallclock window.
C3_CYCLE_PERIOD_DAYS: int = 7

# Story 16 — Recall Top-N Evidence-Auflösung. When a Schema hit comes back
# from the HybridRetriever, this many evidence_engram_ids (preserved in the
# strength-descending order C2 baked in via `select_top_n_evidence`) get
# resolved to full engram rows for the Reflect/Constructive payload.
# Lower than SCHEMA_TOP_N_EVIDENCE=5 on purpose — at write time we record 5
# pointers as audit trail; at recall time 3 are usually enough for the LLM
# to ground its answer, and saving two rows per schema hit adds up across
# the per-recall budget. Drift guard in tests/test_evidence_resolver.py.
RECALL_DEFAULT_EVIDENCE_N: int = 3

# Story 21 — Schema reconsolidation centroid drift. When a schema hit
# triggers Validation-mode reconsolidation with a prediction error, the
# centroid is nudged towards the query embedding by this fraction:
#     new = normalise((1 - α) · old + α · query)
# α=0.05 keeps a single recall from yanking the centroid more than ~3°,
# matching the engram-side reconsolidation conservatism (concept §10).
SCHEMA_CENTROID_DRIFT_ALPHA: float = 0.05
