"""
Tests for Epic 15 Story 04 — Experience Engram & Link Types.

Covers:
  - units_to_content_dicts: EXPERIENCE split, ACTION_EFFECT split, FACT passthrough
  - build_causal_links: correct direction, weight = confidence
  - build_prediction_error_links: link when cosine < 0.8, no link when cosine >= 0.8
  - PREDICTION_ERROR weight = 1.0 - cosine_similarity
  - Recall compatibility: "experience" tag present in outcome dict
"""

from __future__ import annotations

import math
import pytest

from hindsight_api.engine.retain.experience_links import (
    _cosine_similarity,
    build_causal_links,
    build_prediction_error_links,
)
from hindsight_api.engine.retain.neo4j_link_writer import LinkRecord
from hindsight_api.engine.retain.sequence_analysis import (
    StructuredUnit,
    UnitType,
    units_to_content_dicts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_unit(
    unit_type: UnitType,
    content: str = "content",
    expectation: str | None = None,
    outcome: str | None = None,
    confidence: float = 0.9,
    context: str = "ctx",
) -> StructuredUnit:
    return StructuredUnit(
        content=content,
        unit_type=unit_type,
        context=context,
        expectation=expectation,
        outcome=outcome,
        confidence=confidence,
    )


_SOURCE = {"context": "base_ctx", "event_date": None, "tags": ["base_tag"]}


# ---------------------------------------------------------------------------
# units_to_content_dicts — FACT unit
# ---------------------------------------------------------------------------

class TestUnitsToContentDictsFact:
    def test_fact_single_dict(self):
        unit = _make_unit(UnitType.FACT, content="Alice works at ACME.")
        dicts = units_to_content_dicts([unit], _SOURCE)
        assert len(dicts) == 1
        assert dicts[0]["content"] == "Alice works at ACME."

    def test_fact_no_pair_keys(self):
        unit = _make_unit(UnitType.FACT)
        d = units_to_content_dicts([unit], _SOURCE)[0]
        assert "_action_pair_key" not in d
        assert "_effect_pair_key" not in d
        assert "_experience_pair_key" not in d

    def test_fact_inherits_base_tags(self):
        unit = _make_unit(UnitType.FACT)
        d = units_to_content_dicts([unit], _SOURCE)[0]
        assert "experience" not in (d.get("tags") or [])


# ---------------------------------------------------------------------------
# units_to_content_dicts — ACTION_EFFECT unit
# ---------------------------------------------------------------------------

class TestUnitsToContentDictsActionEffect:
    def _unit(self):
        return _make_unit(
            UnitType.ACTION_EFFECT,
            content="Alice deployed the service.",
            outcome="The service went down.",
            confidence=0.85,
        )

    def test_action_effect_produces_two_dicts(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        assert len(dicts) == 2

    def test_action_dict_content(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        action = next(d for d in dicts if "_action_pair_key" in d)
        assert action["content"] == "Alice deployed the service."

    def test_effect_dict_content(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        effect = next(d for d in dicts if "_effect_pair_key" in d)
        assert effect["content"] == "The service went down."

    def test_pair_keys_match(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        action = next(d for d in dicts if "_action_pair_key" in d)
        effect = next(d for d in dicts if "_effect_pair_key" in d)
        assert action["_action_pair_key"] == effect["_effect_pair_key"]

    def test_confidence_propagated(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        for d in dicts:
            assert d["_confidence"] == pytest.approx(0.85)

    def test_action_effect_without_outcome_falls_back_to_single(self):
        unit = _make_unit(UnitType.ACTION_EFFECT, content="Something happened.", outcome=None)
        dicts = units_to_content_dicts([unit], _SOURCE)
        assert len(dicts) == 1


# ---------------------------------------------------------------------------
# units_to_content_dicts — EXPERIENCE unit
# ---------------------------------------------------------------------------

class TestUnitsToContentDictsExperience:
    def _unit(self):
        return _make_unit(
            UnitType.EXPERIENCE,
            content="ignored",
            expectation="The meeting will go smoothly.",
            outcome="The meeting escalated.",
            confidence=0.75,
        )

    def test_experience_produces_two_dicts(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        assert len(dicts) == 2

    def test_expectation_dict_content(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        exp_dict = next(d for d in dicts if "experience" not in (d.get("tags") or []))
        assert exp_dict["content"] == "The meeting will go smoothly."

    def test_outcome_dict_content(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        out_dict = next(d for d in dicts if "experience" in (d.get("tags") or []))
        assert out_dict["content"] == "The meeting escalated."

    def test_experience_tag_on_outcome_dict(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        out_dict = next(d for d in dicts if "experience" in (d.get("tags") or []))
        assert "experience" in out_dict["tags"]

    def test_experience_tag_not_on_expectation_dict(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        exp_dict = next(d for d in dicts if "experience" not in (d.get("tags") or []))
        assert "experience" not in (exp_dict.get("tags") or [])

    def test_expectation_outcome_fields_on_outcome_dict(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        out_dict = next(d for d in dicts if "experience" in (d.get("tags") or []))
        assert out_dict["expectation"] == "The meeting will go smoothly."
        assert out_dict["outcome"] == "The meeting escalated."

    def test_pair_keys_match(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        pair_keys = [d["_experience_pair_key"] for d in dicts]
        assert pair_keys[0] == pair_keys[1]

    def test_confidence_propagated(self):
        dicts = units_to_content_dicts([self._unit()], _SOURCE)
        for d in dicts:
            assert d["_confidence"] == pytest.approx(0.75)

    def test_experience_without_expectation_falls_back_to_single(self):
        unit = _make_unit(UnitType.EXPERIENCE, content="something", expectation=None, outcome="result")
        dicts = units_to_content_dicts([unit], _SOURCE)
        assert len(dicts) == 1

    def test_base_tags_preserved_in_outcome(self):
        unit = self._unit()
        dicts = units_to_content_dicts([unit], _SOURCE)
        out_dict = next(d for d in dicts if "experience" in (d.get("tags") or []))
        assert "base_tag" in out_dict["tags"]


# ---------------------------------------------------------------------------
# build_causal_links
# ---------------------------------------------------------------------------

class TestBuildCausalLinks:
    def _paired_dicts(self, confidence: float = 0.9) -> tuple[list[dict], list[str | None]]:
        unit = _make_unit(
            UnitType.ACTION_EFFECT,
            content="Alice pushed code.",
            outcome="Tests broke.",
            confidence=confidence,
        )
        dicts = units_to_content_dicts([unit], {})
        eids: list[str | None] = ["engram-action-1", "engram-effect-1"]
        return dicts, eids

    def test_returns_one_causal_link(self):
        dicts, eids = self._paired_dicts()
        links = build_causal_links(dicts, eids)
        assert len(links) == 1
        assert links[0].rel_type == "CAUSAL"

    def test_causal_direction_action_to_effect(self):
        dicts, eids = self._paired_dicts()
        links = build_causal_links(dicts, eids)
        link = links[0]
        # action dict is first → engram-action-1 is from_id
        assert link.from_id == "engram-action-1"
        assert link.to_id == "engram-effect-1"

    def test_causal_weight_equals_confidence(self):
        dicts, eids = self._paired_dicts(confidence=0.75)
        links = build_causal_links(dicts, eids)
        assert links[0].weight == pytest.approx(0.75)

    def test_no_links_without_pair_keys(self):
        dicts = [{"content": "fact", "_confidence": 1.0}]
        eids = ["some-id"]
        links = build_causal_links(dicts, eids)
        assert links == []

    def test_no_link_if_effect_engram_id_is_none(self):
        unit = _make_unit(UnitType.ACTION_EFFECT, content="A", outcome="B")
        dicts = units_to_content_dicts([unit], {})
        eids: list[str | None] = ["action-id", None]  # effect engram failed
        links = build_causal_links(dicts, eids)
        assert links == []

    def test_multiple_pairs_produce_multiple_links(self):
        unit1 = _make_unit(UnitType.ACTION_EFFECT, content="A1", outcome="B1")
        unit2 = _make_unit(UnitType.ACTION_EFFECT, content="A2", outcome="B2")
        dicts = units_to_content_dicts([unit1, unit2], {})
        eids = ["id-a1", "id-b1", "id-a2", "id-b2"]
        links = build_causal_links(dicts, eids)
        assert len(links) == 2
        rel_types = {lnk.rel_type for lnk in links}
        assert rel_types == {"CAUSAL"}

    def test_source_field_is_experience_links(self):
        dicts, eids = self._paired_dicts()
        link = build_causal_links(dicts, eids)[0]
        assert link.source == "experience_links"


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_one(self):
        # Zero-vector treated as identical (no prediction error)
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_general_case(self):
        v1 = [1.0, 1.0]
        v2 = [1.0, 0.0]
        expected = 1.0 / math.sqrt(2)
        assert _cosine_similarity(v1, v2) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# build_prediction_error_links
# ---------------------------------------------------------------------------

def _unit_embed(text: str, similarity_to_other: float = 0.5) -> list[float]:
    """Return a simple 2D embedding; pair similarity controlled by the caller."""
    return [1.0, 0.0]


@pytest.mark.asyncio
class TestBuildPredictionErrorLinks:
    def _experience_dicts_and_eids(self) -> tuple[list[dict], list[str | None]]:
        unit = _make_unit(
            UnitType.EXPERIENCE,
            expectation="Project will succeed.",
            outcome="Project failed.",
            confidence=0.8,
        )
        dicts = units_to_content_dicts([unit], {})
        eids: list[str | None] = ["exp-engram-1", "out-engram-1"]
        return dicts, eids

    async def test_link_created_when_cosine_below_threshold(self):
        dicts, eids = self._experience_dicts_and_eids()

        # Orthogonal embeddings → cosine = 0.0 < 0.8
        async def embed_fn(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert len(links) == 1
        assert links[0].rel_type == "PREDICTION_ERROR"

    async def test_no_link_when_cosine_above_threshold(self):
        dicts, eids = self._experience_dicts_and_eids()

        # Identical embeddings → cosine = 1.0 ≥ 0.8
        async def embed_fn(texts):
            return [[1.0, 0.0], [1.0, 0.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert links == []

    async def test_weight_equals_one_minus_cosine(self):
        dicts, eids = self._experience_dicts_and_eids()

        # cosine = 0.0 → weight = 1.0
        async def embed_fn(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert links[0].weight == pytest.approx(1.0)

    async def test_weight_partial_divergence(self):
        dicts, eids = self._experience_dicts_and_eids()

        # cosine ≈ 0.707 → weight ≈ 0.293
        async def embed_fn(texts):
            return [[1.0, 1.0], [1.0, 0.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert len(links) == 1
        expected_weight = 1.0 - (1.0 / math.sqrt(2))
        assert links[0].weight == pytest.approx(expected_weight, abs=1e-4)

    async def test_direction_expectation_to_outcome(self):
        dicts, eids = self._experience_dicts_and_eids()

        async def embed_fn(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        link = links[0]
        assert link.from_id == "exp-engram-1"
        assert link.to_id == "out-engram-1"

    async def test_no_link_if_outcome_engram_id_none(self):
        dicts, eids = self._experience_dicts_and_eids()
        eids[1] = None  # outcome engram failed

        async def embed_fn(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert links == []

    async def test_no_link_without_experience_pair_keys(self):
        dicts = [{"content": "fact"}]
        eids = ["some-id"]

        async def embed_fn(texts):
            return [[1.0, 0.0]]

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert links == []

    async def test_embed_error_skips_gracefully(self):
        dicts, eids = self._experience_dicts_and_eids()

        async def embed_fn(texts):
            raise RuntimeError("embedding service unavailable")

        links = await build_prediction_error_links(dicts, eids, embed_fn)
        assert links == []  # no crash, no links

    async def test_source_field_is_experience_links(self):
        dicts, eids = self._experience_dicts_and_eids()

        async def embed_fn(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        link = (await build_prediction_error_links(dicts, eids, embed_fn))[0]
        assert link.source == "experience_links"


# ---------------------------------------------------------------------------
# Recall compatibility: "experience" tag survives in outcome dict
# ---------------------------------------------------------------------------

class TestRecallCompatibility:
    def test_experience_tag_in_outcome_dict(self):
        unit = _make_unit(
            UnitType.EXPERIENCE,
            expectation="X",
            outcome="Y",
        )
        dicts = units_to_content_dicts([unit], {})
        out_dicts = [d for d in dicts if "experience" in (d.get("tags") or [])]
        assert len(out_dicts) == 1

    def test_experience_dict_has_expectation_and_outcome_fields(self):
        unit = _make_unit(UnitType.EXPERIENCE, expectation="Exp", outcome="Out")
        dicts = units_to_content_dicts([unit], {})
        out_dict = next(d for d in dicts if "experience" in (d.get("tags") or []))
        assert out_dict.get("expectation") == "Exp"
        assert out_dict.get("outcome") == "Out"

    def test_private_pair_keys_do_not_leak_into_normal_fields(self):
        """Private metadata keys (_*) must not interfere with standard RetainContentDict fields."""
        unit = _make_unit(UnitType.ACTION_EFFECT, content="action", outcome="effect")
        dicts = units_to_content_dicts([unit], {})
        standard_keys = {"content", "context", "event_date", "metadata", "entities", "document_id", "tags",
                         "expectation", "outcome", "thalamus_scores"}
        for d in dicts:
            private_keys = [k for k in d if k.startswith("_")]
            assert all(k not in standard_keys for k in private_keys)
