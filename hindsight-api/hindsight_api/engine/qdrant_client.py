"""
Qdrant client for Engram content storage and vector similarity search.

Provides async access to Qdrant with retry logic analogous to db_utils.py.
Collection: 'engrams' — 384-dim Cosine Distance, indexed by engram_id.

Usage:
    from hindsight_api.engine.qdrant_client import QdrantEngineClient
    from hindsight_api.config import get_config

    config = get_config()
    client = QdrantEngineClient(
        url=config.qdrant_url,
        api_key=config.qdrant_api_key,
        collection=config.qdrant_collection,
    )
    await client.connect()
    await client.ensure_collection()
"""

import asyncio
import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

logger = logging.getLogger(__name__)

# Retry configuration (mirrors db_utils.py defaults)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 0.5  # seconds
DEFAULT_MAX_DELAY = 5.0  # seconds

EMBEDDING_DIMENSION = 384

RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    asyncio.TimeoutError,
    OSError,
)


async def _retry_with_backoff(
    func,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """Execute an async function with exponential backoff retry."""
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except RETRYABLE_EXCEPTIONS as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    f"Qdrant operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Qdrant operation failed after {max_retries + 1} attempts: {e}")
    raise last_exception


class QdrantEngineClient:
    """
    Async Qdrant client for Engram content operations.

    Wraps AsyncQdrantClient with retry logic and collection management.
    One instance per application lifetime — created at startup.
    """

    def __init__(self, url: str, api_key: str | None, collection: str) -> None:
        self._url = url
        self._api_key = api_key
        self._collection = collection
        self._client: AsyncQdrantClient | None = None

    async def connect(self) -> None:
        """Initialize the async Qdrant client."""
        self._client = AsyncQdrantClient(url=self._url, api_key=self._api_key)
        logger.info(f"Qdrant client connected: url={self._url}, collection={self._collection}")

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if self._client:
            await self._client.close()
            self._client = None

    def _require_client(self) -> AsyncQdrantClient:
        if self._client is None:
            raise RuntimeError("QdrantEngineClient not connected. Call connect() first.")
        return self._client

    async def ensure_collection(self) -> None:
        """
        Create the engrams collection if it does not exist (idempotent).

        Collection config: 384-dim vectors, Cosine distance.
        Payload indexes: engram_id (keyword), tags (keyword), source (keyword).
        """
        client = self._require_client()

        async def _create():
            exists = await client.collection_exists(self._collection)
            if exists:
                logger.debug(f"Qdrant collection '{self._collection}' already exists — skipping creation.")
                return

            await client.create_collection(
                collection_name=self._collection,
                vectors_config=qdrant_models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            # Payload indexes for efficient filtering. `kind` distinguishes
            # engram embeddings from schema centroids (Epic 25 Story 03);
            # `schema_id` is set on schema-centroid points instead of engram_id.
            for field_name in ("engram_id", "schema_id", "bank_id", "tags", "source", "kind"):
                await client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field_name,
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )
            logger.info(f"Qdrant collection '{self._collection}' created (dim={EMBEDDING_DIMENSION}, Cosine).")

        await _retry_with_backoff(_create)

    async def upsert_point(
        self,
        engram_id: str,
        embedding: list[float],
        payload: dict[str, Any],
    ) -> None:
        """
        Insert or update a single engram point in the collection.

        Args:
            engram_id: UUID string used as both the Qdrant point ID and payload field.
            embedding: 384-dim float vector.
            payload: Arbitrary metadata (text, tags, source, etc.). Epic 25 Story 03:
                `kind="engram"` is injected if the caller does not set it explicitly,
                so the schema-centroid filter on the recall side stays clean.
        """
        client = self._require_client()
        point_id = str(uuid.UUID(engram_id))  # Validate UUID format
        # Story 03 invariant: engram points are pinned to kind="engram" — caller
        # cannot override (use upsert_schema_centroid for schema centroids).
        merged_payload = {**payload, "engram_id": engram_id, "kind": "engram"}

        async def _upsert():
            await client.upsert(
                collection_name=self._collection,
                points=[
                    qdrant_models.PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload=merged_payload,
                    )
                ],
            )

        await _retry_with_backoff(_upsert)

    async def upsert_schema_centroid(
        self,
        schema_id: str,
        centroid: list[float],
        schema_meta: dict[str, Any] | None = None,
    ) -> None:
        """
        Insert or update a schema centroid point (Epic 25 Story 03).

        Schemas live alongside engrams in the same collection; recall fans out
        a single vector search across both populations. The point id is the
        schema_id (matches Neo4j `:Schema {id}.centroid_qdrant_id`).

        Args:
            schema_id: UUID string of the cortex schema node.
            centroid: 384-dim L2-normalised mean of evidence engram embeddings
                (see ``engine.schema.centroid.compute_centroid``).
            schema_meta: optional payload extras — typically a short description
                and tag rollup for visual debugging. ``kind`` and ``schema_id``
                are managed here and override caller-supplied values.
        """
        client = self._require_client()
        point_id = str(uuid.UUID(schema_id))
        meta = dict(schema_meta or {})
        meta["kind"] = "schema"
        meta["schema_id"] = schema_id

        async def _upsert():
            await client.upsert(
                collection_name=self._collection,
                points=[
                    qdrant_models.PointStruct(
                        id=point_id,
                        vector=centroid,
                        payload=meta,
                    )
                ],
            )

        await _retry_with_backoff(_upsert)

    async def search_similar(
        self,
        embedding: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar vectors by cosine distance.

        Args:
            embedding: Query vector (384-dim).
            limit: Maximum number of results.
            filters: Optional Qdrant filter dict (passed as Filter model).
            kind: Optional payload-kind filter — Epic 25 Story 03. ``"engram"``
                restricts hits to engram embeddings, ``"schema"`` to schema
                centroids, ``None`` (default) returns mixed hits.

        Returns:
            List of dicts with keys: engram_id, score, payload.
        """
        client = self._require_client()
        qdrant_filter = self._build_filter(filters, kind)

        async def _search():
            response = await client.query_points(
                collection_name=self._collection,
                query=embedding,
                limit=limit,
                query_filter=qdrant_filter,
                with_payload=True,
            )
            return [
                {
                    "engram_id": hit.payload.get("engram_id", str(hit.id)),
                    "score": hit.score,
                    "payload": hit.payload,
                }
                for hit in response.points
            ]

        return await _retry_with_backoff(_search)

    @staticmethod
    def _build_filter(
        filters: dict[str, Any] | None,
        kind: str | None,
    ) -> qdrant_models.Filter | None:
        """Compose an optional caller filter with an optional ``kind`` filter.

        Both arguments may be None, one, or both. When ``kind`` is set we add
        a ``FieldCondition(key="kind", match=MatchValue(value=kind))`` to the
        ``must`` list — composing rather than replacing so callers retain
        their bank/tag filters.
        """
        if filters is None and kind is None:
            return None

        base: dict[str, Any] = dict(filters) if filters else {}
        if kind is not None:
            kind_condition = qdrant_models.FieldCondition(
                key="kind",
                match=qdrant_models.MatchValue(value=kind),
            )
            existing_must = list(base.get("must") or [])
            existing_must.append(kind_condition)
            base["must"] = existing_must
        return qdrant_models.Filter(**base)

    async def get_by_id(self, engram_id: str) -> dict[str, Any] | None:
        """
        Retrieve a single point by engram_id.

        Returns:
            Dict with engram_id, vector, payload — or None if not found.
        """
        client = self._require_client()
        point_id = str(uuid.UUID(engram_id))

        async def _get():
            results = await client.retrieve(
                collection_name=self._collection,
                ids=[point_id],
                with_vectors=True,
                with_payload=True,
            )
            if not results:
                return None
            point = results[0]
            return {
                "engram_id": point.payload.get("engram_id", str(point.id)),
                "vector": point.vector,
                "payload": point.payload,
            }

        try:
            return await _retry_with_backoff(_get)
        except UnexpectedResponse:
            return None

    async def delete_by_id(self, engram_id: str) -> None:
        """Delete a point from the collection by engram_id."""
        client = self._require_client()
        point_id = str(uuid.UUID(engram_id))

        async def _delete():
            await client.delete(
                collection_name=self._collection,
                points_selector=qdrant_models.PointIdsList(points=[point_id]),
            )

        await _retry_with_backoff(_delete)

    async def set_payload_fields(self, engram_id: str, fields: dict[str, Any]) -> None:
        """
        Update specific payload fields without touching the vector.

        Used by NCR Decay to mark Engrams as archived without re-embedding.

        Args:
            engram_id: UUID string of the point to update.
            fields:    Payload key/value pairs to set (merged into existing payload).
        """
        client = self._require_client()
        point_id = str(uuid.UUID(engram_id))

        async def _set():
            await client.set_payload(
                collection_name=self._collection,
                payload=fields,
                points=[point_id],
            )

        await _retry_with_backoff(_set)

    async def batch_upsert(self, points: list[dict[str, Any]]) -> None:
        """
        Insert or update multiple engram points in a single request.

        Args:
            points: List of dicts with keys: engram_id, embedding, payload.
                Epic 25 Story 03: ``kind="engram"`` is injected into each
                payload by default. Schema centroids should go through
                ``upsert_schema_centroid`` instead.
        """
        client = self._require_client()
        qdrant_points = [
            qdrant_models.PointStruct(
                id=str(uuid.UUID(p["engram_id"])),
                vector=p["embedding"],
                payload={**p.get("payload", {}), "engram_id": p["engram_id"], "kind": "engram"},
            )
            for p in points
        ]

        async def _batch():
            await client.upsert(
                collection_name=self._collection,
                points=qdrant_points,
            )

        await _retry_with_backoff(_batch)
