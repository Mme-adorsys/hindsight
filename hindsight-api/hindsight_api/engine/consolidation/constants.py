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
