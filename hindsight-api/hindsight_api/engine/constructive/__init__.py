"""
Constructive Memory — retrieval as reconstruction (Epic 11).

Retrieval is not a lookup but a reconstruction process: the system assembles a
ConstructedAnswer from retrieved Engram fragments, derives Inferences where explicit
facts are insufficient, and identifies knowledge Gaps.

The Session Mode controls how creative the construction is:
- conservative   (Precision)  → strict facts, minimal inference
- creative       (Exploration) → speculative connections allowed
- cross_domain   (Analogy)    → cross-schema inference
- evidence_based (Validation) → counter-evidence highlighted

Bio mapping:
- Fact extraction  → hippocampal pattern completion: reactivating stored traces
- Inference phase  → prefrontal cortex: combining stored knowledge with current context
- Gap detection    → novelty signal (CA1 mismatch): awareness of missing information
- Prediction Error → dopaminergic PE signal gating reconsolidation + mode adaptation

Concept reference: docs/engram/concept.md — Chapter 11 (Constructive Memory)
"""

from .models import (
    ConstructedAnswer,
    ConstructedFact,
    Gap,
    Inference,
)
from .pipeline import ConstructionPipeline

__all__ = [
    "ConstructedFact",
    "Inference",
    "Gap",
    "ConstructedAnswer",
    "ConstructionPipeline",
]
