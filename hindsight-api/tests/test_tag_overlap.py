"""Unit tests for tag_overlap primitives — pure functions, no IO."""

from __future__ import annotations

import pytest

from hindsight_api.engine.search.tag_overlap import (
    extract_query_tags,
    jaccard_tag_overlap,
)


class TestExtractQueryTags:
    def test_empty_query_returns_empty_set(self):
        assert extract_query_tags("") == set()

    def test_whitespace_only_returns_empty_set(self):
        assert extract_query_tags("   \t\n") == set()

    def test_lowercases_and_dedupes(self):
        assert extract_query_tags("Kafka kafka KAFKA") == {"kafka"}

    def test_drops_short_tokens(self):
        # 'ab' is below the 3-char minimum, 'abc' is at the boundary
        assert extract_query_tags("ab abc abcd") == {"abc", "abcd"}

    def test_drops_stopwords_de(self):
        # 'der', 'wird', 'nicht' are all in the stopword list.
        assert extract_query_tags("der Branch wird nicht gefunden") == {"branch", "gefunden"}

    def test_drops_stopwords_en(self):
        # 'the' is a stopword → dropped.
        assert extract_query_tags("the quick brown fox") == {"quick", "brown", "fox"}

    def test_german_umlauts_preserved(self):
        # 'über' is in the stopword list; 'größe' and 'straße' survive with their umlauts.
        assert extract_query_tags("Größe über Straße") == {"größe", "straße"}

    def test_numbers_and_punctuation_stripped(self):
        # The regex only matches letters; numbers and separators vanish.
        assert extract_query_tags("v2.28 branch-name!") == {"branch", "name"}

    def test_mixed_language_query(self):
        tags = extract_query_tags("Recall bug im Branch main")
        assert "recall" in tags
        assert "branch" in tags
        assert "main" in tags
        assert "bug" in tags
        # 'im' is not in the stopword list (German preposition variant) —
        # 2 chars anyway → filtered by length.
        assert "im" not in tags


class TestJaccardTagOverlap:
    def test_both_empty_is_zero(self):
        assert jaccard_tag_overlap(set(), set()) == 0.0

    def test_empty_query_is_zero(self):
        assert jaccard_tag_overlap(set(), {"python", "recall"}) == 0.0

    def test_empty_engram_is_zero(self):
        assert jaccard_tag_overlap({"python"}, set()) == 0.0

    def test_identical_sets_is_one(self):
        assert jaccard_tag_overlap({"python", "recall"}, {"python", "recall"}) == 1.0

    def test_disjoint_is_zero(self):
        assert jaccard_tag_overlap({"python"}, {"rust"}) == 0.0

    def test_half_overlap(self):
        # {"a","b"} ∩ {"b","c"} = {"b"} (size 1); union = {"a","b","c"} (size 3)
        assert jaccard_tag_overlap({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_subset_partial(self):
        # query ⊂ engram: intersect = query, union = engram
        score = jaccard_tag_overlap({"branch"}, {"branch", "main", "git"})
        assert score == pytest.approx(1 / 3)

    def test_case_insensitive_on_engram_side(self):
        # Query tags come from extract_query_tags (already lowercased);
        # engram tags are stored as-entered and may be mixed case.
        assert jaccard_tag_overlap({"branch"}, {"Branch"}) == 1.0
        assert jaccard_tag_overlap({"python"}, {"PYTHON", "Rust"}) == pytest.approx(1 / 2)

    def test_engram_empty_strings_ignored(self):
        # Empty/blank engram tags shouldn't count toward the union.
        assert jaccard_tag_overlap({"python"}, {"python", ""}) == 1.0
