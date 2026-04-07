"""
NCR Phase 3 — Schema Compression Hook.

Defines the SchemaProcessor interface and a NoOp default implementation.
The real implementation (Game-of-Life rules R1-R5) is provided by Epic 13.

Biological mapping:
  REM Sleep — pattern recognition across Engrams, schema birth and compression.
  Abstract patterns emerge bottom-up from connection density, not top-down.
  Ref: concept.md ch. 12 — NCR Phase 3 (Schema Compression)
       concept.md ch. 13 — Schema Emergence (5 Game-of-Life Rules)

Epic 12, Story 04.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hindsight_api.engine.response_models import FullEngram


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SchemaResult:
    """Summary of a single NCR Phase 3 (Schema Compression) run.

    Attributes:
        created:         Number of new Schema nodes created (alias: schemas_created).
        strengthened:    Number of existing Schemas strengthened.
        deleted:         Number of Schemas that died (alias: schemas_deleted).
        details:         Optional list of per-schema details.
        clusters_found:  Number of ClusterCandidate nodes found/created (R1).
        schemas_matured: Number of ClusterCandidates that reached maturation threshold (R2).
        schemas_created: Number of new Schema nodes created (R3) — mirrors created.
        schemas_deleted: Number of Schema nodes deleted (R5) — mirrors deleted.
        reinforcements:  Number of Schema–Engram links reinforced at Retain time (R4).
    """

    created: int = 0
    strengthened: int = 0
    deleted: int = 0
    details: list[Any] = field(default_factory=list)
    clusters_found: int = 0
    schemas_matured: int = 0
    schemas_created: int = 0
    schemas_deleted: int = 0
    reinforcements: int = 0


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class SchemaProcessor(ABC):
    """
    Abstract interface for NCR Phase 3 — Schema Compression.

    Receives all neocortex-layer Engrams for a bank and applies the
    Game-of-Life emergence rules (R1-R5). The NCR Orchestrator calls
    this once per NCR cycle after Phase 2 (Strengthen) has completed.

    Epic 13 provides the concrete implementation. Until then, the
    NoOpSchemaProcessor is registered as the default.
    """

    @abstractmethod
    async def process(self, bank_id: str, engrams: list[FullEngram]) -> SchemaResult:
        """
        Run Schema Compression on neocortex Engrams.

        Args:
            bank_id: The memory bank being processed.
            engrams: All active neocortex-layer FullEngrams for the bank.
                     Filtered and fetched by the NCR Orchestrator (Story 05).

        Returns:
            SchemaResult with counts of created/strengthened/deleted schemas.
        """


# ---------------------------------------------------------------------------
# NoOp default (replaced by Epic 13)
# ---------------------------------------------------------------------------


class NoOpSchemaProcessor(SchemaProcessor):
    """
    Default SchemaProcessor — does nothing.

    Used until Epic 13 provides the real Game-of-Life implementation.
    Returns an empty SchemaResult without touching any storage.
    """

    async def process(self, bank_id: str, engrams: list[FullEngram]) -> SchemaResult:
        return SchemaResult()
