"""
Unit tests for Epic 02 Story 01 — Engram Pydantic Models.

Tests:
- ThalamusScores dataclass defaults and values
- ExtractedFact backwards-compatibility (new fields optional)
- ProcessedFact.from_extracted_fact() passes new fields through
- Engram Pydantic Model validation (layer Literal, strength range)
- FullEngram composition with EngramMetadata
"""

import uuid
from datetime import datetime, timezone

import pytest

from hindsight_api.engine.response_models import (
    Engram,
    EngramContent,
    EngramMetadata,
    EngramRelationship,
    FullEngram,
)
from hindsight_api.engine.engram_types import ThalamusScores
from hindsight_api.engine.retain.types import (
    CausalRelation,
    ExtractedFact,
    ProcessedFact,
)


# =============================================================================
# ThalamusScores
# =============================================================================


class TestThalamusScores:
    def test_defaults_all_zero(self):
        scores = ThalamusScores()
        assert scores.novelty == 0.0
        assert scores.surprise == 0.0
        assert scores.task_relevance == 0.0
        assert scores.emotional_valence == 0.0
        assert scores.overall == 0.0

    def test_explicit_values(self):
        scores = ThalamusScores(novelty=0.8, surprise=0.5, task_relevance=0.9, emotional_valence=0.3, overall=0.7)
        assert scores.novelty == 0.8
        assert scores.surprise == 0.5
        assert scores.task_relevance == 0.9
        assert scores.emotional_valence == 0.3
        assert scores.overall == 0.7

    def test_partial_construction(self):
        scores = ThalamusScores(novelty=1.0)
        assert scores.novelty == 1.0
        assert scores.surprise == 0.0

    def test_rejects_value_above_one(self):
        with pytest.raises(ValueError, match="must be in range"):
            ThalamusScores(novelty=1.5)

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError, match="must be in range"):
            ThalamusScores(surprise=-0.1)

    def test_boundary_values_accepted(self):
        scores = ThalamusScores(novelty=0.0, surprise=1.0, overall=0.0)
        assert scores.novelty == 0.0
        assert scores.surprise == 1.0


# =============================================================================
# ExtractedFact — backwards-compatibility
# =============================================================================


class TestExtractedFactBackwardsCompatibility:
    def test_minimal_construction_no_new_fields(self):
        """Existing code that creates ExtractedFact without tags/thalamus_scores must still work."""
        fact = ExtractedFact(fact_text="Alice works at Google", fact_type="world")
        assert fact.fact_text == "Alice works at Google"
        assert fact.fact_type == "world"
        assert fact.tags == []
        assert fact.thalamus_scores is None

    def test_with_tags(self):
        fact = ExtractedFact(fact_text="Deployed to prod", fact_type="world", tags=["technical", "work"])
        assert fact.tags == ["technical", "work"]

    def test_with_thalamus_scores(self):
        scores = ThalamusScores(novelty=0.7, task_relevance=0.9)
        fact = ExtractedFact(fact_text="Critical bug found", fact_type="world", thalamus_scores=scores)
        assert fact.thalamus_scores is not None
        assert fact.thalamus_scores.novelty == 0.7
        assert fact.thalamus_scores.task_relevance == 0.9

    def test_existing_fields_unchanged(self):
        """All pre-existing fields still work as before."""
        now = datetime.now(timezone.utc)
        fact = ExtractedFact(
            fact_text="Test fact",
            fact_type="experience",
            entities=["Alice"],
            context="test context",
            mentioned_at=now,
            metadata={"source": "test"},
        )
        assert fact.fact_type == "experience"
        assert fact.entities == ["Alice"]
        assert fact.context == "test context"
        assert fact.mentioned_at == now


# =============================================================================
# ProcessedFact.from_extracted_fact() — new fields pass-through
# =============================================================================


class TestProcessedFactFromExtractedFact:
    def test_tags_passed_through(self):
        fact = ExtractedFact(fact_text="Test", fact_type="world", tags=["technical", "decision"])
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[0.1] * 384)
        assert processed.tags == ["technical", "decision"]

    def test_thalamus_scores_passed_through(self):
        scores = ThalamusScores(novelty=0.6, surprise=0.4)
        fact = ExtractedFact(fact_text="Test", fact_type="world", thalamus_scores=scores)
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[0.1] * 384)
        assert processed.thalamus_scores is not None
        assert processed.thalamus_scores.novelty == 0.6

    def test_no_new_fields_defaults(self):
        """from_extracted_fact without new fields → empty tags, None thalamus."""
        fact = ExtractedFact(fact_text="Test", fact_type="world")
        processed = ProcessedFact.from_extracted_fact(fact, embedding=[0.1] * 384)
        assert processed.tags == []
        assert processed.thalamus_scores is None


# =============================================================================
# Engram Pydantic Model
# =============================================================================


class TestEngramModel:
    def test_minimal_construction(self):
        engram = Engram(
            engram_id=uuid.uuid4(),
            text="Alice is a software engineer",
            created_at=datetime.now(timezone.utc),
        )
        assert engram.layer == "buffer"
        assert engram.strength == 0.0
        assert engram.abstraction_level == 0.0
        assert engram.status == "active"
        assert engram.tags == []
        assert engram.thalamus_scores.overall == 0.0

    def test_layer_literal_valid(self):
        engram = Engram(
            engram_id=uuid.uuid4(),
            text="Test",
            created_at=datetime.now(timezone.utc),
            layer="neocortex",
        )
        assert engram.layer == "neocortex"

    def test_layer_literal_invalid(self):
        with pytest.raises(Exception):
            Engram(
                engram_id=uuid.uuid4(),
                text="Test",
                created_at=datetime.now(timezone.utc),
                layer="cortex",  # invalid
            )

    def test_strength_range_valid(self):
        engram = Engram(
            engram_id=uuid.uuid4(),
            text="Test",
            created_at=datetime.now(timezone.utc),
            strength=0.75,
        )
        assert engram.strength == 0.75

    def test_strength_range_out_of_bounds(self):
        with pytest.raises(Exception):
            Engram(
                engram_id=uuid.uuid4(),
                text="Test",
                created_at=datetime.now(timezone.utc),
                strength=1.5,  # > 1.0
            )

    def test_status_literal_valid_values(self):
        for status in ["active", "archived", "decayed"]:
            engram = Engram(
                engram_id=uuid.uuid4(),
                text="Test",
                created_at=datetime.now(timezone.utc),
                status=status,
            )
            assert engram.status == status

    def test_thalamus_scores_default_factory(self):
        e1 = Engram(engram_id=uuid.uuid4(), text="T1", created_at=datetime.now(timezone.utc))
        e2 = Engram(engram_id=uuid.uuid4(), text="T2", created_at=datetime.now(timezone.utc))
        # Ensure separate instances (not shared mutable default)
        e1.thalamus_scores.novelty = 0.9
        assert e2.thalamus_scores.novelty == 0.0

    def test_with_thalamus_scores(self):
        scores = ThalamusScores(novelty=0.8, task_relevance=0.9, overall=0.85)
        engram = Engram(
            engram_id=uuid.uuid4(),
            text="Important decision made",
            created_at=datetime.now(timezone.utc),
            thalamus_scores=scores,
            tags=["decision", "work"],
        )
        assert engram.thalamus_scores.novelty == 0.8
        assert engram.tags == ["decision", "work"]


# =============================================================================
# FullEngram — composition
# =============================================================================


class TestFullEngramComposition:
    def test_minimal_full_engram(self):
        eid = uuid.uuid4()
        fe = FullEngram(engram_id=eid)
        assert fe.engram_id == eid
        assert fe.metadata is None
        assert fe.content is None
        assert fe.relationships == []

    def test_with_metadata(self):
        eid = uuid.uuid4()
        meta = EngramMetadata(engram_id=eid, bank_id="test-bank", strength=0.6, tags=["technical"])
        fe = FullEngram(engram_id=eid, metadata=meta)
        assert fe.metadata.strength == 0.6
        assert fe.metadata.tags == ["technical"]

    def test_with_content(self):
        eid = uuid.uuid4()
        content = EngramContent(text="Alice deployed the service", embedding=[0.1] * 384)
        fe = FullEngram(engram_id=eid, content=content)
        assert fe.content.text == "Alice deployed the service"

    def test_with_relationship(self):
        eid = uuid.uuid4()
        target = uuid.uuid4()
        rel = EngramRelationship(target_id=target, rel_type="semantic", weight=0.8)
        fe = FullEngram(engram_id=eid, relationships=[rel])
        assert len(fe.relationships) == 1
        assert fe.relationships[0].rel_type == "semantic"

    def test_engram_metadata_has_thalamus_fields(self):
        """EngramMetadata from Epic 01 must expose all thalamus score fields."""
        eid = uuid.uuid4()
        meta = EngramMetadata(
            engram_id=eid,
            bank_id="test",
            novelty=0.7,
            surprise=0.3,
            task_relevance=0.9,
            emotional_valence=0.1,
            thalamus_overall=0.6,
        )
        assert meta.novelty == 0.7
        assert meta.thalamus_overall == 0.6
