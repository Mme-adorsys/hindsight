"""
Context-Tag-Overlap scoring primitives.

Pure functions used by the recall scoring pipeline to weigh candidates by
how strongly the query's implicit tags overlap with the engram's stored
tags. Today engrams carry tags in ``engram_dictionary.tags``; before this
module the retrieval path only used tags as an AND filter. Spec: see
``docs/engram/11_retrieval_architecture.md`` §3.2.

The v1 extraction strategy is deliberately cheap: lowercase the query,
split on non-alphanumeric, drop bilingual DE+EN stopwords, and keep
tokens of length ≥ 3. An LLM-based tag extractor is out of scope — it
adds latency and a provider dependency for unclear marginal value over
plain tokenization. Revisit when retrieval benchmarks show residual
error concentrated in tag-sensitive queries.
"""

from __future__ import annotations

import re

# Bilingual stopword list (DE + EN). Kept intentionally short — the goal
# is to strip the high-frequency filler tokens that would otherwise
# dominate the overlap signal. Rare domain words stay in.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # German
        "aber",
        "alle",
        "als",
        "also",
        "auch",
        "auf",
        "aus",
        "bei",
        "bin",
        "bis",
        "bist",
        "das",
        "dass",
        "dein",
        "deine",
        "dem",
        "den",
        "der",
        "des",
        "die",
        "dies",
        "dir",
        "doch",
        "dort",
        "durch",
        "ein",
        "eine",
        "einen",
        "einer",
        "eines",
        "etwa",
        "euch",
        "euer",
        "eure",
        "für",
        "gegen",
        "haben",
        "hast",
        "hat",
        "hatte",
        "ich",
        "ihm",
        "ihn",
        "ihr",
        "ist",
        "kann",
        "kein",
        "keine",
        "man",
        "mein",
        "meine",
        "mich",
        "mir",
        "mit",
        "nach",
        "nein",
        "nicht",
        "noch",
        "nun",
        "nur",
        "ohne",
        "oder",
        "schon",
        "sehr",
        "sein",
        "seine",
        "sich",
        "sie",
        "sind",
        "soll",
        "sondern",
        "über",
        "und",
        "uns",
        "unser",
        "vom",
        "von",
        "vor",
        "war",
        "waren",
        "was",
        "weil",
        "wenn",
        "wer",
        "wie",
        "will",
        "wir",
        "wird",
        "wo",
        "zu",
        "zum",
        "zur",
        # English
        "and",
        "are",
        "but",
        "for",
        "from",
        "had",
        "has",
        "have",
        "her",
        "him",
        "his",
        "how",
        "its",
        "not",
        "now",
        "off",
        "our",
        "out",
        "she",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)

_TOKEN_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)
_MIN_TOKEN_LEN = 3


def extract_query_tags(query: str) -> set[str]:
    """
    Derive the implicit tag set of a recall query.

    Strategy: lowercase, split on non-alphabetic (unicode-aware so German
    umlauts survive), drop stopwords and tokens shorter than three chars.
    Returns the deduplicated token set — order is not meaningful for the
    downstream Jaccard comparison.

    Returns an empty set when the query contains no tag-worthy tokens; the
    caller should treat that as "no tag signal", yielding a zero overlap
    score rather than a bonus or penalty.
    """
    if not query:
        return set()
    tokens = _TOKEN_PATTERN.findall(query.lower())
    return {t for t in tokens if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS}


def jaccard_tag_overlap(query_tags: set[str], engram_tags: set[str]) -> float:
    """
    Jaccard similarity between the two tag sets: ``|A ∩ B| / |A ∪ B|``.

    Symmetric, normalized to ``[0.0, 1.0]``. Returns ``0.0`` when either
    side is empty — empty query tags means "no tag evidence", which must
    not reward engrams just for being richly tagged, and empty engram
    tags means the engram provides no tag signal to match against.

    Engram tags are compared case-insensitively to align with the
    lowercased query tokens produced by :func:`extract_query_tags`.
    """
    if not query_tags or not engram_tags:
        return 0.0
    engram_normalized = {t.lower() for t in engram_tags if t}
    if not engram_normalized:
        return 0.0
    intersection = query_tags & engram_normalized
    if not intersection:
        return 0.0
    union = query_tags | engram_normalized
    return len(intersection) / len(union)
