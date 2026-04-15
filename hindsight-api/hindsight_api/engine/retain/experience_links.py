"""
Experience Links — CAUSAL and PREDICTION_ERROR link creation.

Neuroscience basis (concept.md ch. 10 + ch. 15):
  CAUSAL links encode action→effect chains, analogous to how the hippocampus
  represents causal sequences between episodic memory traces.
  PREDICTION_ERROR links encode expectation↔outcome divergence, analogous to
  dopamine-driven prediction-error signals that modulate reconsolidation strength.

Called by the retain orchestrator after the main retain pipeline has committed
engrams to all three databases. Uses content_dict metadata (_action_pair_key,
_effect_pair_key, _experience_pair_key) to identify paired engrams and build
Neo4j LinkRecords.

Three producers of :CAUSAL edges live here:

1. ``build_causal_links``           — structured ACTION_EFFECT unit pairs
                                       (_action_pair_key / _effect_pair_key,
                                       set by sequence_analysis.units_to_content_dicts).
2. ``build_llm_causal_links``       — LLM-extracted ``causal_relations`` on
                                       ProcessedFacts (the "causes / caused_by /
                                       enables / prevents" links the extractor
                                       already persists into Postgres
                                       ``memory_links`` via
                                       ``link_utils.create_causal_links_batch``).
                                       We mirror them into Neo4j so the graph
                                       view and graph-mode retrieval can follow
                                       free-text causal chains, not only the
                                       structured ACTION_EFFECT pairs.
3. ``build_prediction_error_links`` — EXPERIENCE pairs with high
                                       expectation↔outcome divergence.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from .neo4j_link_writer import LinkRecord

if TYPE_CHECKING:
    from .types import ProcessedFact

logger = logging.getLogger(__name__)

# LLM causal_relation_type → (flip_direction, subtype_label)
# "caused_by" is the semantic inverse of "causes", so we flip the endpoints to
# store a consistent A-causes-B orientation in Neo4j. "enables" / "prevents"
# keep their direction; their nuance is preserved via the LinkRecord.source
# label so downstream can distinguish subtypes if needed.
_LLM_CAUSAL_DIRECTIONS: dict[str, tuple[bool, str]] = {
    "causes": (False, "causes"),
    "caused_by": (True, "caused_by"),
    "enables": (False, "enables"),
    "prevents": (False, "prevents"),
}


# ---------------------------------------------------------------------------
# CAUSAL links (ACTION_EFFECT units)
# ---------------------------------------------------------------------------


def build_causal_links(
    content_dicts: list[dict],
    engram_ids: list[str | None],
) -> list[LinkRecord]:
    """
    Build CAUSAL LinkRecords for ACTION_EFFECT unit pairs.

    Scans content_dicts for _action_pair_key / _effect_pair_key markers set by
    units_to_content_dicts. Pairs are matched by their shared pair_id; the action
    engram becomes the source (from_id) and the effect engram the target (to_id).

    Args:
        content_dicts: Expanded list of content dicts, in same order as engram_ids.
        engram_ids:    Engram IDs returned by the retain pipeline (None = failed).

    Returns:
        List of CAUSAL LinkRecords; empty if no pairs found or IDs are missing.
    """
    action_map: dict[int, tuple[str, float]] = {}  # pair_id → (engram_id, confidence)
    effect_map: dict[int, str] = {}  # pair_id → engram_id

    for i, d in enumerate(content_dicts):
        eid = engram_ids[i] if i < len(engram_ids) else None
        if eid is None:
            continue
        if "_action_pair_key" in d:
            action_map[d["_action_pair_key"]] = (eid, float(d.get("_confidence", 1.0)))
        elif "_effect_pair_key" in d:
            effect_map[d["_effect_pair_key"]] = eid

    links: list[LinkRecord] = []
    for pair_id, (action_id, confidence) in action_map.items():
        effect_id = effect_map.get(pair_id)
        if effect_id is None:
            logger.warning("CAUSAL: no effect engram found for pair_id=%s — skipping", pair_id)
            continue
        links.append(
            LinkRecord(
                from_id=action_id,
                to_id=effect_id,
                rel_type="CAUSAL",
                weight=confidence,
                source="experience_links",
            )
        )

    return links


# ---------------------------------------------------------------------------
# CAUSAL links (LLM-extracted causal_relations on ProcessedFacts)
# ---------------------------------------------------------------------------


def build_llm_causal_links(
    unit_ids: list[str],
    facts: "list[ProcessedFact]",
) -> list[LinkRecord]:
    """
    Mirror LLM-extracted causal_relations into Neo4j as ``:CAUSAL`` edges.

    The fact-extraction prompt lets the LLM declare up to two causal links per
    fact (``causes`` / ``caused_by`` / ``enables`` / ``prevents``). Those
    relations are already persisted into the Postgres ``memory_links`` table
    by ``link_utils.create_causal_links_batch`` — this helper generates the
    parallel Neo4j LinkRecords so the graph view and graph-mode retrieval can
    follow free-text causal chains, not only the structured ACTION_EFFECT
    pairs handled by ``build_causal_links``.

    Direction handling:
      * ``causes``   — ``(fact_i)-[:CAUSAL]->(target)``
      * ``caused_by``— flipped to ``(target)-[:CAUSAL]->(fact_i)`` so every
                       edge in Neo4j points from cause to effect
      * ``enables``  — ``(fact_i)-[:CAUSAL]->(target)`` (semantic nuance kept
                       via ``source='llm_causal_relations:enables'``)
      * ``prevents`` — ``(fact_i)-[:CAUSAL]->(target)`` (source carries the
                       'prevents' label)

    Args:
        unit_ids: Engram IDs in the same order as ``facts``. Must be
                  length-aligned — this is the convention already used by
                  ``link_utils.create_causal_links_batch``.
        facts:    ProcessedFacts carrying ``causal_relations`` with
                  ``target_fact_index`` pointing into the same list.

    Returns:
        List of CAUSAL LinkRecords. Skips self-links and out-of-range target
        indexes; logs and drops unknown relation_type values.
    """
    if not unit_ids or not facts or len(unit_ids) != len(facts):
        return []

    links: list[LinkRecord] = []
    for i, fact in enumerate(facts):
        relations = getattr(fact, "causal_relations", None)
        if not relations:
            continue

        from_unit_id = unit_ids[i]
        for rel in relations:
            relation_type = getattr(rel, "relation_type", None)
            target_idx = getattr(rel, "target_fact_index", None)
            if relation_type is None or target_idx is None:
                continue

            direction = _LLM_CAUSAL_DIRECTIONS.get(relation_type)
            if direction is None:
                logger.warning(
                    "build_llm_causal_links: unknown relation_type %r on fact %d — skipping",
                    relation_type,
                    i,
                )
                continue

            if target_idx < 0 or target_idx >= len(unit_ids):
                continue
            to_unit_id = unit_ids[target_idx]
            if to_unit_id == from_unit_id:
                continue  # self-link

            flip, subtype = direction
            src, dst = (to_unit_id, from_unit_id) if flip else (from_unit_id, to_unit_id)

            weight = float(getattr(rel, "strength", 1.0) or 1.0)
            links.append(
                LinkRecord(
                    from_id=src,
                    to_id=dst,
                    rel_type="CAUSAL",
                    weight=weight,
                    source=f"llm_causal_relations:{subtype}",
                )
            )

    return links


# ---------------------------------------------------------------------------
# PREDICTION_ERROR links (EXPERIENCE units)
# ---------------------------------------------------------------------------


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 1.0  # treat zero-vectors as identical → no prediction error
    return dot / (norm1 * norm2)


async def build_prediction_error_links(
    content_dicts: list[dict],
    engram_ids: list[str | None],
    embed_fn: Any,
    divergence_threshold: float = 0.8,
) -> list[LinkRecord]:
    """
    Build PREDICTION_ERROR LinkRecords for EXPERIENCE unit pairs.

    Scans content_dicts for _experience_pair_key markers. For each pair, embeds
    both the expectation and outcome texts and computes cosine similarity. If the
    similarity falls below divergence_threshold (default 0.8), a PREDICTION_ERROR
    link is created from expectation-engram → outcome-engram with
    weight = 1.0 - cosine_similarity.

    Args:
        content_dicts:        Expanded content dicts, in same order as engram_ids.
        engram_ids:           Engram IDs returned by the retain pipeline (None = failed).
        embed_fn:             Async callable: embed_fn([text1, text2]) → list[list[float]].
        divergence_threshold: Cosine similarity below this value triggers a link.

    Returns:
        List of PREDICTION_ERROR LinkRecords; empty if no divergent pairs found.
    """
    # Collect expectation dicts (no "experience" tag) and outcome dicts (have "experience" tag)
    expectation_map: dict[int, tuple[str, str]] = {}  # pair_id → (engram_id, text)
    outcome_map: dict[int, tuple[str, str]] = {}  # pair_id → (engram_id, text)

    for i, d in enumerate(content_dicts):
        if "_experience_pair_key" not in d:
            continue
        eid = engram_ids[i] if i < len(engram_ids) else None
        if eid is None:
            continue
        pair_id = d["_experience_pair_key"]
        tags = d.get("tags") or []
        if "experience" in tags:
            # outcome-engram: has expectation + outcome stored
            outcome_map[pair_id] = (eid, d.get("content", ""))
        else:
            # expectation-engram: content IS the expectation text
            expectation_map[pair_id] = (eid, d.get("content", ""))

    links: list[LinkRecord] = []
    for pair_id, (exp_id, exp_text) in expectation_map.items():
        outcome_entry = outcome_map.get(pair_id)
        if outcome_entry is None:
            logger.warning("PREDICTION_ERROR: no outcome engram for pair_id=%s — skipping", pair_id)
            continue
        out_id, out_text = outcome_entry

        if not exp_text or not out_text:
            continue

        try:
            embeddings = await embed_fn([exp_text, out_text])
            cosine_sim = _cosine_similarity(embeddings[0], embeddings[1])
        except Exception as e:
            logger.warning("PREDICTION_ERROR: embedding failed for pair_id=%s (%s) — skipping", pair_id, e)
            continue

        if cosine_sim < divergence_threshold:
            magnitude = 1.0 - cosine_sim
            links.append(
                LinkRecord(
                    from_id=exp_id,
                    to_id=out_id,
                    rel_type="PREDICTION_ERROR",
                    weight=magnitude,
                    source="experience_links",
                )
            )
            logger.debug(
                "PREDICTION_ERROR link: %s → %s (cosine=%.3f, magnitude=%.3f)",
                exp_id,
                out_id,
                cosine_sim,
                magnitude,
            )

    return links
