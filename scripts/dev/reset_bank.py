#!/usr/bin/env python
"""
Reset a single memory bank across all three stores (Postgres, Qdrant, Neo4j).

Usage:
    uv run --directory hindsight-api python ../scripts/dev/reset_bank.py --bank-id marcel-engram-dev

Safety:
    Refuses to touch bank IDs that don't match the dev/test whitelist.
    Reads connection info from the project-level .env file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Whitelist of bank IDs that are safe to wipe. Anything not matching is refused.
SAFE_BANK_PATTERNS = (
    "marcel-engram-dev",
    "dev-",
    "test-",
    "mcp",
    "integration_test",
)


def _is_safe_bank(bank_id: str) -> bool:
    return any(bank_id == p or bank_id.startswith(p) for p in SAFE_BANK_PATTERNS)


def _load_env() -> None:
    """Load .env from the project root if present."""
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Don't override values already in the environment
        if key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def reset_postgres(bank_id: str) -> dict[str, int]:
    """DELETE rows for this bank from all bank-scoped tables."""
    import psycopg2

    db_url = os.environ.get("HINDSIGHT_API_DATABASE_URL")
    if not db_url or db_url == "pg0":
        # Dev-stack default
        db_url = "postgresql://hindsight:hindsight@localhost:5433/hindsight"
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    counts: dict[str, int] = {}
    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                # Order matters: child rows first, parents last. Most FKs cascade
                # but we delete explicitly so the counts are visible.
                tables = [
                    "retain_traces",
                    "ncr_runs",
                    "memory_links",
                    "unit_entities",
                    "engram_dictionary",
                    "working_memory",
                    "sessions",
                    "memory_units",
                    "chunks",
                    "documents",
                    "entities",
                    "banks",  # also drops banks.op_count so cycles_alive resets
                ]
                for tbl in tables:
                    try:
                        cur.execute(f"DELETE FROM {tbl} WHERE bank_id = %s", (bank_id,))
                        counts[tbl] = cur.rowcount
                    except psycopg2.errors.UndefinedTable:
                        counts[tbl] = -1
                        conn.rollback()
                    except psycopg2.errors.UndefinedColumn:
                        # Table exists but doesn't have bank_id column (e.g. sessions
                        # is transient in some schemas). Skip silently.
                        counts[tbl] = -2
                        conn.rollback()
    finally:
        conn.close()
    return counts


def reset_qdrant(bank_id: str) -> int:
    """Filter-delete all Qdrant points with matching bank_id payload."""
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    url = os.environ.get("QDRANT_URL", "http://localhost:6336")
    api_key = os.environ.get("QDRANT_API_KEY") or None
    collection = os.environ.get("QDRANT_COLLECTION", "engrams")

    client = QdrantClient(url=url, api_key=api_key)
    try:
        collections = {c.name for c in client.get_collections().collections}
        if collection not in collections:
            print(f"  Qdrant: collection '{collection}' does not exist — nothing to delete")
            return 0

        # Count before delete for reporting
        count_before = client.count(
            collection_name=collection,
            count_filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="bank_id", match=qmodels.MatchValue(value=bank_id))]
            ),
            exact=True,
        ).count

        client.delete(
            collection_name=collection,
            points_selector=qmodels.Filter(
                must=[qmodels.FieldCondition(key="bank_id", match=qmodels.MatchValue(value=bank_id))]
            ),
        )
        return count_before
    finally:
        client.close()


def reset_neo4j(bank_id: str) -> dict[str, int]:
    """DETACH DELETE all Engram and Schema nodes for this bank."""
    from neo4j import GraphDatabase

    url = os.environ.get("NEO4J_BOLT_URL", "bolt://localhost:7688")
    username = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "hindsightdev")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")

    counts: dict[str, int] = {}
    driver = GraphDatabase.driver(url, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            # Engrams
            result = session.run(
                "MATCH (e:Engram {bank_id: $bank_id}) "
                "WITH collect(e) AS nodes "
                "CALL { WITH nodes UNWIND nodes AS n DETACH DELETE n } "
                "RETURN size(nodes) AS deleted",
                bank_id=bank_id,
            )
            counts["engrams"] = result.single()["deleted"]

            # Schemas
            result = session.run(
                "MATCH (s:Schema {bank_id: $bank_id}) "
                "WITH collect(s) AS nodes "
                "CALL { WITH nodes UNWIND nodes AS n DETACH DELETE n } "
                "RETURN size(nodes) AS deleted",
                bank_id=bank_id,
            )
            counts["schemas"] = result.single()["deleted"]
    finally:
        driver.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a memory bank across all 3 stores")
    parser.add_argument("--bank-id", required=True, help="Bank ID to wipe")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation (useful for scripts).",
    )
    args = parser.parse_args()

    bank_id = args.bank_id

    if not _is_safe_bank(bank_id):
        print(
            f"REFUSED: bank_id '{bank_id}' is not in the dev/test whitelist "
            f"({', '.join(SAFE_BANK_PATTERNS)}). This script only wipes dev banks.",
            file=sys.stderr,
        )
        return 2

    _load_env()

    if not args.yes:
        confirm = input(f"About to wipe ALL data for bank '{bank_id}' across Postgres/Qdrant/Neo4j. Type 'yes' to proceed: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1

    print(f"Resetting bank: {bank_id}")
    print("-" * 60)

    print("1. Postgres cascade-delete...")
    pg_counts = reset_postgres(bank_id)
    for tbl, n in pg_counts.items():
        marker = "✓" if n >= 0 else ("—" if n == -1 else "·")
        label = "rows" if n >= 0 else ("table not found" if n == -1 else "no bank_id column")
        print(f"   {marker} {tbl}: {n if n >= 0 else label}")

    print("2. Qdrant filter-delete...")
    qdrant_count = reset_qdrant(bank_id)
    print(f"   ✓ deleted {qdrant_count} points from engrams collection")

    print("3. Neo4j detach-delete...")
    neo4j_counts = reset_neo4j(bank_id)
    print(f"   ✓ engrams: {neo4j_counts.get('engrams', 0)}")
    print(f"   ✓ schemas: {neo4j_counts.get('schemas', 0)}")

    print("-" * 60)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
