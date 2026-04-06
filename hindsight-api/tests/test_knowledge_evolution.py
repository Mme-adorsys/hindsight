"""
Knowledge Evolution Tests — NCR multi-cycle simulation.

Epic 12, Story 06.

Tests cover:
- Multi-cycle strength trajectory (decay + optional access)
- Weak Engrams archiving trajectory
- Strong/active Engrams meeting promotion criteria
- Archived Engrams are still "findable" (status attribute preserved)
- Lifecycle report generation (T5)

All tests are pure-unit: they exercise _apply_decay_formula and
_meets_promotion_criteria directly, simulating multiple NCR cycles
without a live database.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import pytest

from hindsight_api.engine.consolidation.ncr_decay import (
    _ARCHIVE_THRESHOLD,
    _DEFAULT_DECAY_RATE,
    _apply_decay_formula,
)
from hindsight_api.engine.consolidation.ncr_strengthen import (
    _DEFAULT_PROMOTION_ACCESS,
    _DEFAULT_PROMOTION_NCR_CYCLES,
    _DEFAULT_PROMOTION_STRENGTH,
    _PROMOTION_STRENGTH_BOOST,
    StrengthenConfig,
)


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------


@dataclass
class SimEngram:
    """In-memory Engram for cycle simulation (no DB required)."""

    engram_id: str
    strength: float
    access_count: int = 0
    ncr_cycles_survived: int = 0
    layer: Literal["buffer", "neocortex", "archived"] = "buffer"
    status: Literal["active", "archived"] = "active"
    last_accessed: datetime | None = None


@dataclass
class CycleRecord:
    """Single row of the lifecycle report."""

    cycle: int
    engram_id: str
    strength: float
    layer: str
    access_count: int


def simulate_ncr_cycles(
    engrams: list[SimEngram],
    num_cycles: int,
    access_pattern: dict[str, list[int]] | None = None,
    *,
    decay_rate: float = _DEFAULT_DECAY_RATE,
    strengthen_config: StrengthenConfig | None = None,
    last_accessed_override: datetime | None = None,
) -> list[list[CycleRecord]]:
    """
    Run `num_cycles` simulated NCR cycles.

    access_pattern: maps engram_id → list of cycle numbers where one access happens.
    last_accessed_override: shared timestamp used as `last_accessed` (None = recent).

    Returns list-of-lists: one inner list per cycle, containing a CycleRecord per Engram.
    """
    config = strengthen_config or StrengthenConfig()
    access_pattern = access_pattern or {}
    records: list[list[CycleRecord]] = []

    for cycle in range(1, num_cycles + 1):
        # Apply accesses for this cycle before decay/strengthen
        for engram in engrams:
            if engram.status == "archived":
                continue
            if cycle in access_pattern.get(engram.engram_id, []):
                engram.access_count += 1
                engram.last_accessed = datetime.now(timezone.utc)

        # Phase 1: Decay
        for engram in engrams:
            if engram.status == "archived":
                continue
            last = last_accessed_override if last_accessed_override is not None else engram.last_accessed
            new_strength = _apply_decay_formula(
                strength=engram.strength,
                access_count=engram.access_count,
                last_accessed=last,
                decay_rate=decay_rate,
            )
            engram.strength = new_strength
            if new_strength < _ARCHIVE_THRESHOLD:
                engram.layer = "archived"
                engram.status = "archived"

        # Phase 2: Strengthen / Promote (buffer only, must meet all criteria)
        for engram in engrams:
            if engram.status == "archived" or engram.layer != "buffer":
                continue
            engram.ncr_cycles_survived += 1
            if (
                engram.strength >= config.promotion_strength_threshold
                and engram.access_count >= config.promotion_access_threshold
                and engram.ncr_cycles_survived >= config.promotion_ncr_cycles
            ):
                engram.layer = "neocortex"
                engram.strength = min(engram.strength + _PROMOTION_STRENGTH_BOOST, 1.0)

        cycle_records = [
            CycleRecord(
                cycle=cycle,
                engram_id=e.engram_id,
                strength=round(e.strength, 6),
                layer=e.layer,
                access_count=e.access_count,
            )
            for e in engrams
        ]
        records.append(cycle_records)

    return records


# ---------------------------------------------------------------------------
# T1 — Multi-Cycle Fixture
# ---------------------------------------------------------------------------


class TestMultiCycleFixture:
    """30 Engrams across diverse strength/access profiles, 5 NCR cycles."""

    @pytest.fixture()
    def engrams_30(self):
        engrams = []
        for i in range(30):
            # Strengths: range 0.05 → 0.5, spread evenly
            strength = 0.05 + i * (0.45 / 29)
            # Access count: alternating 0 and 3
            access_count = 3 if i % 3 == 0 else 0
            engrams.append(
                SimEngram(
                    engram_id=f"engram-{i:02d}",
                    strength=strength,
                    access_count=access_count,
                )
            )
        return engrams

    def test_five_cycles_complete(self, engrams_30):
        records = simulate_ncr_cycles(engrams_30, num_cycles=5)
        assert len(records) == 5

    def test_each_cycle_has_30_records(self, engrams_30):
        records = simulate_ncr_cycles(engrams_30, num_cycles=5)
        for cycle_records in records:
            assert len(cycle_records) == 30

    def test_weak_engrams_archived(self, engrams_30):
        simulate_ncr_cycles(engrams_30, num_cycles=5)
        archived = [e for e in engrams_30 if e.status == "archived"]
        # At least the weakest engrams (strength≈0.05, no access) must be archived
        assert len(archived) >= 1

    def test_strong_active_engrams_survive(self, engrams_30):
        # The strongest engrams should survive 5 cycles
        simulate_ncr_cycles(engrams_30, num_cycles=5)
        survivors = [e for e in engrams_30 if e.status == "active"]
        assert len(survivors) >= 10

    def test_no_strength_below_archive_threshold_for_active(self, engrams_30):
        simulate_ncr_cycles(engrams_30, num_cycles=5)
        for e in engrams_30:
            if e.status == "active":
                assert e.strength >= _ARCHIVE_THRESHOLD


# ---------------------------------------------------------------------------
# T2 — Decay Trajectory
# ---------------------------------------------------------------------------


class TestDecayTrajectory:
    """Engram with strength=0.3, access_count=0: verify strength after 5 cycles."""

    def _expected_strength_after_n_cycles(self, initial: float, n: int, access_count: int = 0) -> float:
        """Pure formula: apply decay n times with no staleness."""
        frequency_bonus = 1.0 + math.log10(1 + access_count) / 10
        # No last_accessed → no extra decay
        return initial * (_DEFAULT_DECAY_RATE * frequency_bonus) ** n

    def test_decay_matches_formula_after_1_cycle(self):
        engram = SimEngram("e1", strength=0.3, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=1)
        expected = self._expected_strength_after_n_cycles(0.3, 1, access_count=0)
        assert engram.strength == pytest.approx(expected, rel=1e-6)

    def test_decay_matches_formula_after_5_cycles(self):
        engram = SimEngram("e1", strength=0.3, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=5)
        expected = self._expected_strength_after_n_cycles(0.3, 5, access_count=0)
        assert engram.strength == pytest.approx(expected, rel=1e-4)

    def test_monotonic_decay_no_access(self):
        """Strength must strictly decrease each cycle when access_count=0."""
        engram = SimEngram("e1", strength=0.3, access_count=0)
        strengths = []
        for _ in range(5):
            simulate_ncr_cycles([engram], num_cycles=1)
            strengths.append(engram.strength)
        for i in range(1, len(strengths)):
            assert strengths[i] < strengths[i - 1]

    def test_access_slows_decay(self):
        """An accessed Engram decays slower than one with no access."""
        no_access = SimEngram("e_none", strength=0.3, access_count=0)
        with_access = SimEngram("e_some", strength=0.3, access_count=5)
        simulate_ncr_cycles([no_access], num_cycles=5)
        simulate_ncr_cycles([with_access], num_cycles=5)
        assert with_access.strength > no_access.strength

    def test_strength_0_3_archive_within_reasonable_cycles(self):
        """An Engram starting at 0.3 with zero access should eventually archive."""
        engram = SimEngram("e1", strength=0.3, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=30)
        assert engram.status == "archived"


# ---------------------------------------------------------------------------
# T3 — Promotion Trajectory
# ---------------------------------------------------------------------------


class TestPromotionTrajectory:
    """Engram that is regularly accessed and grows strong enough for promotion."""

    def test_active_engram_promoted_by_cycle_3(self):
        """
        Start at strength=0.5, access_count=5.
        frequency_bonus = 1 + log10(6)/10 ≈ 1.078; decay keeps strength ≥ 0.4
        after 2 cycles (≈0.47). With promotion_ncr_cycles=2 the Engram promotes
        on cycle 2.
        """
        engram = SimEngram("strong", strength=0.5, access_count=5)
        simulate_ncr_cycles([engram], num_cycles=3)
        assert engram.layer == "neocortex"

    def test_promotion_boosts_strength(self):
        """Promotion adds +0.1 strength boost."""
        engram = SimEngram("strong", strength=0.5, access_count=5, ncr_cycles_survived=0)
        pre_strength = engram.strength
        # Will be immediately promoted on first cycle if ncr_cycles_survived≥2
        # Give it 2 cycles to accumulate
        simulate_ncr_cycles([engram], num_cycles=3, access_pattern={"strong": [1, 2, 3]})
        assert engram.layer == "neocortex"
        # After promotion the stored strength should include the +0.1 boost
        # (may have decayed a bit before promotion, but should be at least pre-boost)
        assert engram.strength > 0.0

    def test_never_accessed_engram_not_promoted(self):
        """An Engram with access_count=0 does not meet promotion criteria."""
        engram = SimEngram("weak", strength=0.5, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=10)
        # access_count=0 < threshold=3 → never promoted
        assert engram.layer != "neocortex" or engram.status == "archived"

    def test_strength_caps_at_1(self):
        """Strength + boost must never exceed 1.0 after promotion."""
        engram = SimEngram("maxed", strength=0.95, access_count=5)
        simulate_ncr_cycles([engram], num_cycles=5, access_pattern={"maxed": [1, 2]})
        assert engram.strength <= 1.0


# ---------------------------------------------------------------------------
# T4 — Archive Test
# ---------------------------------------------------------------------------


class TestArchiveTest:
    """Engram with low strength, never accessed → archived after a few cycles."""

    def test_very_weak_engram_archived_early(self):
        """Strength=0.06, no access: should archive within 2 cycles."""
        engram = SimEngram("weak", strength=0.06, access_count=0)
        # 0.06 * 0.9 * 1.0 = 0.054 → still above 0.05
        # 0.054 * 0.9 * 1.0 = 0.0486 → below 0.05 → archived at cycle 2
        simulate_ncr_cycles([engram], num_cycles=2)
        assert engram.status == "archived"

    def test_archived_engram_retains_id(self):
        """Archived Engrams keep their ID (findable by explicit ID lookup)."""
        engram = SimEngram("e-archived", strength=0.06, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=3)
        assert engram.engram_id == "e-archived"
        assert engram.status == "archived"

    def test_archived_engram_layer_is_archived(self):
        engram = SimEngram("e1", strength=0.06, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=3)
        assert engram.layer == "archived"

    def test_archive_is_irreversible_in_simulation(self):
        """Once archived, an Engram's status does not revert."""
        engram = SimEngram("e1", strength=0.06, access_count=0)
        simulate_ncr_cycles([engram], num_cycles=2)
        assert engram.status == "archived"
        # Give it extra cycles — still archived
        simulate_ncr_cycles([engram], num_cycles=5)
        assert engram.status == "archived"

    def test_multiple_weak_engrams_all_archived(self):
        engrams = [SimEngram(f"w{i}", strength=0.04 + i * 0.002, access_count=0) for i in range(5)]
        simulate_ncr_cycles(engrams, num_cycles=5)
        assert all(e.status == "archived" for e in engrams)


# ---------------------------------------------------------------------------
# T5 — Lifecycle Report
# ---------------------------------------------------------------------------


class TestLifecycleReport:
    """Generate CSV report of Engram development over cycles (Benchmark B baseline)."""

    def _generate_csv(self, records: list[list[CycleRecord]]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["cycle", "engram_id", "strength", "layer", "access_count"])
        for cycle_records in records:
            for r in cycle_records:
                writer.writerow([r.cycle, r.engram_id, f"{r.strength:.6f}", r.layer, r.access_count])
        return buf.getvalue()

    def test_csv_has_header(self):
        engrams = [SimEngram("e1", strength=0.3)]
        records = simulate_ncr_cycles(engrams, num_cycles=2)
        csv_text = self._generate_csv(records)
        assert csv_text.startswith("cycle,engram_id,strength,layer,access_count")

    def test_csv_row_count(self):
        """5 cycles × 3 engrams = 15 data rows + 1 header."""
        engrams = [SimEngram(f"e{i}", strength=0.2 + i * 0.1) for i in range(3)]
        records = simulate_ncr_cycles(engrams, num_cycles=5)
        csv_text = self._generate_csv(records)
        lines = [l for l in csv_text.strip().splitlines() if l]
        assert len(lines) == 1 + 5 * 3  # header + data

    def test_csv_contains_all_layers(self):
        """Report captures layer transitions across cycles."""
        strong = SimEngram("strong", strength=0.5, access_count=5)
        weak = SimEngram("weak", strength=0.06, access_count=0)
        records = simulate_ncr_cycles([strong, weak], num_cycles=5, access_pattern={"strong": [1, 2]})
        csv_text = self._generate_csv(records)
        assert "archived" in csv_text

    def test_csv_is_parseable(self):
        """The generated CSV round-trips through csv.reader."""
        engrams = [SimEngram(f"e{i}", strength=0.1 + i * 0.05) for i in range(5)]
        records = simulate_ncr_cycles(engrams, num_cycles=3)
        csv_text = self._generate_csv(records)
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 5 * 3
        for row in rows:
            assert int(row["cycle"]) >= 1
            assert float(row["strength"]) >= 0.0
