#!/usr/bin/env python
"""Backfill ``payload.kind = "engram"`` on existing Qdrant points (Epic 25 Story 03).

Pre-Epic-25 the collection contained only engram embeddings; the new
schema-centroid coexistence (concept §3) needs a kind discriminator. Points
written before this migration have no ``kind`` payload field — running this
script tags them as ``"engram"`` so the recall ``kind`` filter is consistent.

Idempotent: points that already carry ``kind="engram"`` are skipped; points
with ``kind="schema"`` (centroids written by Story 09 onwards) are left alone.

Usage:
    python scripts/dev/migrate_qdrant_kind_payload.py
    python scripts/dev/migrate_qdrant_kind_payload.py --qdrant-url http://localhost:6333 --collection engrams
    python scripts/dev/migrate_qdrant_kind_payload.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

logger = logging.getLogger("migrate_qdrant_kind_payload")

DEFAULT_BATCH = 256


async def _migrate(
    *,
    url: str,
    api_key: str | None,
    collection: str,
    batch: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Walk the collection in batches and stamp missing ``kind`` fields.

    Returns ``(scanned, updated)``.
    """
    client = AsyncQdrantClient(url=url, api_key=api_key)
    try:
        if not await client.collection_exists(collection):
            logger.warning("Collection %r does not exist — nothing to migrate.", collection)
            return 0, 0

        scanned = 0
        updated = 0
        offset: str | int | None = None

        while True:
            points, next_offset = await client.scroll(
                collection_name=collection,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break

            ids_needing_kind = [
                p.id
                for p in points
                if (p.payload or {}).get("kind") != "schema" and (p.payload or {}).get("kind") != "engram"
            ]
            scanned += len(points)

            if ids_needing_kind:
                if dry_run:
                    logger.info(
                        "DRY-RUN: would set kind='engram' on %d points (sample id=%s)",
                        len(ids_needing_kind),
                        ids_needing_kind[0],
                    )
                else:
                    await client.set_payload(
                        collection_name=collection,
                        payload={"kind": "engram"},
                        points=qdrant_models.PointIdsList(points=ids_needing_kind).points,
                    )
                updated += len(ids_needing_kind)

            if next_offset is None:
                break
            offset = next_offset

        return scanned, updated
    finally:
        await client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--qdrant-url",
        default=os.getenv("HINDSIGHT_API_QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL (default: $HINDSIGHT_API_QDRANT_URL or http://localhost:6333)",
    )
    parser.add_argument(
        "--qdrant-api-key",
        default=os.getenv("HINDSIGHT_API_QDRANT_API_KEY"),
        help="Qdrant API key (default: $HINDSIGHT_API_QDRANT_API_KEY)",
    )
    parser.add_argument(
        "--collection",
        default=os.getenv("HINDSIGHT_API_QDRANT_COLLECTION", "engrams"),
        help="Collection name (default: $HINDSIGHT_API_QDRANT_COLLECTION or 'engrams')",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"Scroll batch size (default: {DEFAULT_BATCH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be changed without writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    scanned, updated = asyncio.run(
        _migrate(
            url=args.qdrant_url,
            api_key=args.qdrant_api_key,
            collection=args.collection,
            batch=args.batch,
            dry_run=args.dry_run,
        )
    )
    verb = "would update" if args.dry_run else "updated"
    logger.info("Done. Scanned %d points, %s %d.", scanned, verb, updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
