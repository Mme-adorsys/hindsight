"""Schema persistence for C2 (Epic 25 Story 09).

End of the C2 creation pipeline: a matured cluster that didn't match an
existing schema (Story 06) and got Properties (Story 07) + Description
(Story 08) is now committed atomically into Neo4j (`:Schema` node) and
Qdrant (centroid point with `payload.kind="schema"`).

Concept §4.2 — Schema has three parallel representations bound by the
same UUID: Centroid (Qdrant) for vector match, Description (Neo4j prop)
for humans/LLM, Properties (Neo4j prop) for structured queries. They
must be written together; partial writes leave the cortex inconsistent.

Atomicity model: write Neo4j first (cheap to roll back via
``archive_schema``), then Qdrant. If Qdrant fails the schema is archived
so HybridRetriever (Story 15) won't surface a phantom node — the row
isn't deleted because R5 Schema Death (Story 14) is the canonical
removal path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from ..db_utils import acquire_with_retry
from ..schema.models import SchemaModel
from ..schema.schema_repository import archive_schema, create_schema
from .constants import SCHEMA_TOP_N_EVIDENCE

if TYPE_CHECKING:
    import asyncpg

    from ..neo4j_client import Neo4jEngineClient
    from ..qdrant_client import QdrantEngineClient
    from .c2_pattern_recognition import CreationPayload

logger = logging.getLogger(__name__)


async def select_top_n_evidence(
    pool: "asyncpg.Pool",
    engram_ids: list[str],
    *,
    n: int = SCHEMA_TOP_N_EVIDENCE,
) -> list[UUID]:
    """Pick the strongest ``n`` engrams from a cluster (Indexing Theory pointer set).

    Strength = ``engram_dictionary.strength`` which since Epic 24 Story 03
    holds the composite score ``thalamus_overall * decay``. Highest-scoring
    members make the cut so the schema's audit trail points at the
    representative episodes, not at the noisiest ones.

    Returns ``list[UUID]`` ordered by descending strength. Missing ids are
    silently dropped — they may have been archived between R1 and persist.
    """
    if not engram_ids or n <= 0:
        return []
    query = """
        SELECT engram_id, COALESCE(strength, 0.0) AS strength
        FROM engram_dictionary
        WHERE engram_id = ANY($1::uuid[])
        ORDER BY strength DESC
        LIMIT $2
    """
    async with acquire_with_retry(pool) as conn:
        rows = await conn.fetch(query, [UUID(eid) for eid in engram_ids], n)
    return [row["engram_id"] for row in rows]


async def persist_new_schema(
    payload: "CreationPayload",
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> SchemaModel:
    """Create the :Schema node and its Qdrant centroid atomically.

    Returns the persisted ``SchemaModel`` (with the freshly minted ``id``
    and the ``evidence_engram_ids`` selection). On Qdrant failure the
    Neo4j node is archived (status='archived') so retrieval ignores it.
    """
    schema_id = uuid4()
    now = datetime.now(timezone.utc)
    evidence_ids = await select_top_n_evidence(pool, list(payload.cluster.engram_ids))

    model = SchemaModel(
        id=schema_id,
        description=payload.description,
        properties=dict(payload.properties),
        centroid_qdrant_id=schema_id,
        evidence_engram_ids=evidence_ids,
        evidence_count=int(payload.properties.get("evidence_count", len(payload.cluster.engram_ids)) or 0),
        cycles_survived=1,
        status="active",
        created_at=now,
        last_reinforced_at=now,
    )

    try:
        await create_schema(neo4j, model, label="Schema")
    except Exception:
        logger.exception("persist_new_schema neo4j create failed bank=%s schema_id=%s", bank_id, schema_id)
        raise

    try:
        await qdrant.upsert_schema_centroid(
            schema_id=str(schema_id),
            centroid=list(payload.cluster.centroid),
            schema_meta={"bank_id": bank_id, "description_short": _short(payload.description)},
        )
    except Exception:
        logger.error(
            "persist_new_schema qdrant upsert failed bank=%s schema_id=%s — archiving neo4j node",
            bank_id,
            schema_id,
        )
        try:
            await archive_schema(neo4j, schema_id, label="Schema")
        except Exception:
            logger.exception("persist_new_schema archive-fallback also failed bank=%s schema_id=%s", bank_id, schema_id)
        raise

    logger.info(
        "persist_new_schema bank=%s schema_id=%s evidence=%d properties=%d",
        bank_id,
        schema_id,
        len(evidence_ids),
        sum(1 for k in model.properties if k != "evidence_count"),
    )
    return model


async def persist_creation_payloads(
    payloads: tuple["CreationPayload", ...],
    bank_id: str,
    *,
    neo4j: "Neo4jEngineClient",
    qdrant: "QdrantEngineClient",
    pool: "asyncpg.Pool",
) -> list[SchemaModel]:
    """Run :func:`persist_new_schema` per payload, sequentially.

    Sequential because per-bank Schema-ID space is small and Neo4j+Qdrant
    write contention is real; parallelism here would create a thundering
    herd against the cortex. Per-payload failures are logged but don't
    abort the batch — best-effort semantics matching the rest of C2.
    """
    persisted: list[SchemaModel] = []
    for payload in payloads:
        try:
            schema = await persist_new_schema(payload, bank_id, neo4j=neo4j, qdrant=qdrant, pool=pool)
        except Exception as exc:
            logger.warning(
                "persist_creation_payloads skipping payload bank=%s reason=%s",
                bank_id,
                exc.__class__.__name__,
            )
            continue
        persisted.append(schema)
    logger.info(
        "persist_creation_payloads bank=%s requested=%d persisted=%d",
        bank_id,
        len(payloads),
        len(persisted),
    )
    return persisted


def _short(text: str, max_chars: int = 80) -> str:
    """Truncate ``text`` for the Qdrant payload (debug-render only)."""
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
