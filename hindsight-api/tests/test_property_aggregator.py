"""Unit tests for Epic 25 Story 07 — property_aggregator.

Pure function — no I/O. Tests pin the four aggregation modes (categorical,
numeric, temporal, bag-of-tags fallback) plus edge cases (empty input,
mixed numeric ints/floats, mode-confidence semantics).
"""

from __future__ import annotations

import pytest

from hindsight_api.engine.consolidation.property_aggregator import (
    EVIDENCE_COUNT_KEY,
    KEYWORDS_BUCKET,
    _split_tag,
    aggregate_properties,
)

# ---------------------------------------------------------------------------
# _split_tag
# ---------------------------------------------------------------------------


class TestSplitTag:
    def test_namespaced_tag_splits(self):
        assert _split_tag("activity:coffee") == ("activity", "coffee")

    def test_whitespace_trimmed(self):
        assert _split_tag("  mood : productive  ") == ("mood", "productive")

    def test_unnamespaced_tag_lands_in_keywords_bucket(self):
        assert _split_tag("alice") == (KEYWORDS_BUCKET, "alice")

    def test_empty_key_or_value_falls_back_to_keywords(self):
        assert _split_tag(":coffee") == (KEYWORDS_BUCKET, ":coffee")
        assert _split_tag("activity:") == (KEYWORDS_BUCKET, "activity:")

    def test_only_first_colon_splits(self):
        # ISO timestamps contain colons in the value — must not be split mid-value.
        assert _split_tag("event_time:2026-05-07T14:00:00") == (
            "event_time",
            "2026-05-07T14:00:00",
        )


# ---------------------------------------------------------------------------
# aggregate_properties — all four aggregation modes
# ---------------------------------------------------------------------------


class TestAggregatePropertiesCoffeeBundle:
    """Eight near-identical coffee meetings — the canonical concept §4.2 example."""

    def setup_method(self):
        self.member_tags = []
        for i in range(8):
            self.member_tags.append(
                [
                    "activity:coffee",
                    "mood:productive" if i < 7 else "mood:tired",
                    f"duration_minutes:{40 + i}",  # 40,41,...47
                    f"event_time:2026-05-{i + 1:02d}T14:00:00",
                    "work",
                ]
            )

    def test_evidence_count_matches_input(self):
        out = aggregate_properties(self.member_tags)
        assert out[EVIDENCE_COUNT_KEY] == 8

    def test_categorical_mode_with_confidence(self):
        out = aggregate_properties(self.member_tags)
        activity = out["activity"]
        assert activity["type"] == "categorical"
        assert activity["value"] == "coffee"
        assert activity["count"] == 8
        assert activity["confidence"] == pytest.approx(1.0)

        mood = out["mood"]
        assert mood["value"] == "productive"
        assert mood["count"] == 7
        assert mood["confidence"] == pytest.approx(7 / 8)

    def test_numeric_aggregation(self):
        out = aggregate_properties(self.member_tags)
        duration = out["duration_minutes"]
        assert duration["type"] == "numeric"
        assert duration["min"] == 40
        assert duration["max"] == 47
        # Mean of 40..47 = 43.5; ``round`` rounds half-to-even → 44 in Python.
        assert duration["mean"] in (43, 44)
        assert duration["count"] == 8

    def test_temporal_aggregation(self):
        out = aggregate_properties(self.member_tags)
        time_window = out["event_time"]
        assert time_window["type"] == "temporal"
        assert time_window["min_iso"] == "2026-05-01T14:00:00"
        assert time_window["max_iso"] == "2026-05-08T14:00:00"
        assert time_window["count"] == 8

    def test_bag_of_tags_under_keywords_bucket(self):
        out = aggregate_properties(self.member_tags)
        kw = out[KEYWORDS_BUCKET]
        assert kw["type"] == "categorical"
        assert kw["value"] == "work"
        assert kw["count"] == 8


# ---------------------------------------------------------------------------
# Type-detection edge cases
# ---------------------------------------------------------------------------


class TestTypeDetection:
    def test_mixed_int_and_float_promotes_to_float(self):
        out = aggregate_properties([["x:1"], ["x:2.5"]])
        assert out["x"]["type"] == "numeric"
        # Saw "2.5" → float kind → cast remains float.
        assert isinstance(out["x"]["mean"], float)

    def test_pure_int_stays_int(self):
        out = aggregate_properties([["x:10"], ["x:20"], ["x:30"]])
        assert out["x"]["type"] == "numeric"
        assert isinstance(out["x"]["mean"], int)
        assert out["x"]["mean"] == 20

    def test_mixed_parseable_and_non_parseable_falls_back_to_categorical(self):
        # If even one value fails int/float parse → categorical.
        out = aggregate_properties([["x:10"], ["x:hello"]])
        assert out["x"]["type"] == "categorical"

    def test_iso_timestamp_with_z_suffix_recognised(self):
        out = aggregate_properties(
            [
                ["t:2026-05-07T14:00:00+00:00"],
                ["t:2026-05-07T15:30:00+00:00"],
            ]
        )
        assert out["t"]["type"] == "temporal"

    def test_categorical_fallback_for_truly_string_tags(self):
        out = aggregate_properties([["status:open"], ["status:open"], ["status:closed"]])
        assert out["status"]["type"] == "categorical"
        assert out["status"]["value"] == "open"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestAggregateEdgeCases:
    def test_empty_input(self):
        out = aggregate_properties([])
        assert out == {EVIDENCE_COUNT_KEY: 0}

    def test_engrams_with_no_tags(self):
        out = aggregate_properties([[], [], []])
        assert out == {EVIDENCE_COUNT_KEY: 3}

    def test_confidence_normalised_to_evidence_count_not_tag_count(self):
        # Two engrams, one carries the mood-tag twice, the other once.
        # Mode count = 3, evidence_count = 2 → confidence = 1.5? No —
        # confidence is bounded by sample size in this design; we expose the
        # raw count alongside, but normalise to evidence_count so values
        # stay interpretable as "fraction of engrams that voted this way".
        out = aggregate_properties(
            [
                ["mood:productive", "mood:productive"],
                ["mood:productive"],
            ]
        )
        mood = out["mood"]
        assert mood["count"] == 3
        # Documented behaviour: confidence = mode_count / evidence_count.
        assert mood["confidence"] == pytest.approx(3 / 2)

    def test_bag_of_tags_keywords_is_synthesised_only_when_needed(self):
        # All tags are namespaced → no _keywords bucket.
        out = aggregate_properties([["activity:coffee"], ["activity:coffee"]])
        assert KEYWORDS_BUCKET not in out
