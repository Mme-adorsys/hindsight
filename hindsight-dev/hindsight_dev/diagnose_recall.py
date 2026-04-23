"""
Read-only diagnostic for recall failures.

Walks a query through every stage of the recall pipeline and prints scores
and intermediate tokens, so you can pinpoint where a candidate is lost.

Usage:
    uv run --project hindsight-dev python -m hindsight_dev.diagnose_recall \
        --bank memory-engram-dev --query "branch"

The script makes no writes. It reads from Postgres, embeds the query,
scores candidates with the cross-encoder, and calls recall_async with
enable_trace=True in all four modes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from hindsight_api import MemoryEngine
from hindsight_api.engine.response_models import RetrievalMode, Session
from hindsight_api.engine.utils import acquire_with_retry, fq_table
from hindsight_api.models import RequestContext
from rich.console import Console
from rich.table import Table

console = Console()


async def create_engine() -> MemoryEngine:
    engine = MemoryEngine(
        db_url=os.getenv("HINDSIGHT_API_DATABASE_URL", "pg0"),
        memory_llm_provider=os.getenv("HINDSIGHT_API_LLM_PROVIDER", "groq"),
        memory_llm_api_key=os.getenv("HINDSIGHT_API_LLM_API_KEY"),
        memory_llm_model=os.getenv("HINDSIGHT_API_LLM_MODEL", "openai/gpt-oss-120b"),
        memory_llm_base_url=os.getenv("HINDSIGHT_API_LLM_BASE_URL") or None,
    )
    await engine.initialize()
    return engine


def _sanitize_for_tsquery(query_text: str) -> str:
    """Mirror retrieve_bm25 sanitization (retrieval.py:134-149)."""
    import re

    sanitized = re.sub(r"[^\w\s]", " ", query_text.lower())
    tokens = [t for t in sanitized.split() if t]
    return " | ".join(tokens) if tokens else ""


async def check_0_global_inventory(conn, query: str) -> None:
    """Scan every schema that has a memory_units table and list bank sizes + query-keyword hits."""
    console.rule("[bold]0. Global inventory (schemas × banks)")
    schemas = await conn.fetch(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = 'memory_units'
        ORDER BY table_schema
        """
    )
    if not schemas:
        console.print("[red]No memory_units table found anywhere.[/red]")
        return
    tokens = [t for t in query.split() if t]
    for row in schemas:
        schema = row["table_schema"]
        console.print(f"\n[bold]schema = {schema}[/bold]")
        banks = await conn.fetch(
            f"""
            SELECT bank_id, count(*) AS n
            FROM "{schema}".memory_units
            GROUP BY bank_id
            ORDER BY n DESC
            """
        )
        if not banks:
            console.print("  (empty)")
            continue
        tbl = Table(show_header=True, header_style="dim")
        tbl.add_column("bank_id")
        tbl.add_column("n", justify="right")
        for b in banks:
            tbl.add_row(str(b["bank_id"]), str(b["n"]))
        console.print(tbl)

        for tok in tokens:
            hits = await conn.fetch(
                f"""
                SELECT bank_id, count(*) AS n
                FROM "{schema}".memory_units
                WHERE text ILIKE $1 OR context ILIKE $1
                GROUP BY bank_id
                ORDER BY n DESC
                """,
                f"%{tok}%",
            )
            if hits:
                console.print(
                    f"  [green]'{tok}' ILIKE hits:[/green] " + ", ".join(f"{h['bank_id']}={h['n']}" for h in hits)
                )


async def check_1_bank_sanity(conn, bank_id: str, query: str) -> list[dict[str, Any]]:
    console.rule("[bold]1. Bank sanity")
    total = await conn.fetchval(
        f"SELECT count(*) FROM {fq_table('memory_units')} WHERE bank_id = $1",
        bank_id,
    )
    console.print(f"memory_units in bank [cyan]{bank_id}[/cyan] (schema of current tenant): [bold]{total}[/bold]")

    like_patterns = [f"%{token}%" for token in query.split()]
    hits: list[dict[str, Any]] = []
    for pattern in like_patterns:
        rows = await conn.fetch(
            f"""
            SELECT id, left(text, 160) AS snippet, fact_type, mentioned_at
            FROM {fq_table("memory_units")}
            WHERE bank_id = $1 AND text ILIKE $2
            LIMIT 5
            """,
            bank_id,
            pattern,
        )
        for r in rows:
            if not any(h["id"] == r["id"] for h in hits):
                hits.append(dict(r))

    if not hits:
        console.print(f"[yellow]No row matched ILIKE for any token of '{query}'.[/yellow]")
        return []

    tbl = Table(title=f"ILIKE hits for '{query}' tokens", show_lines=False)
    tbl.add_column("id")
    tbl.add_column("fact_type")
    tbl.add_column("snippet", overflow="fold")
    for h in hits:
        tbl.add_row(str(h["id"]), str(h["fact_type"]), str(h["snippet"]))
    console.print(tbl)
    return hits


async def check_2_tsvector(conn, unit_ids: list[Any]) -> None:
    console.rule("[bold]2. tsvector inspection (stored tokens)")
    if not unit_ids:
        console.print("[yellow]No candidate ids — skipping tsvector dump.[/yellow]")
        return
    rows = await conn.fetch(
        f"""
        SELECT id,
               to_tsvector('simple', COALESCE(text,'') || ' ' || COALESCE(context,'')) AS tokens
        FROM {fq_table("memory_units")}
        WHERE id = ANY($1::uuid[])
        """,
        unit_ids,
    )
    for r in rows:
        console.print(f"[cyan]{r['id']}[/cyan]")
        console.print(f"  {r['tokens']}")


async def check_3_bm25(conn, bank_id: str, query: str) -> list[dict[str, Any]]:
    console.rule("[bold]3. BM25 direct query (identical to retrieve_bm25)")
    ts = _sanitize_for_tsquery(query)
    if not ts:
        console.print("[yellow]Sanitized tsquery is empty — BM25 cannot run.[/yellow]")
        return []
    console.print(f"tsquery = [magenta]{ts}[/magenta]")
    rows = await conn.fetch(
        f"""
        SELECT id, left(text, 160) AS snippet,
               ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS bm25_score
        FROM {fq_table("memory_units")}
        WHERE bank_id = $2 AND search_vector @@ to_tsquery('simple', $1)
        ORDER BY bm25_score DESC
        LIMIT 10
        """,
        ts,
        bank_id,
    )
    if not rows:
        console.print("[red]BM25 returned no rows.[/red]")
        return []
    tbl = Table(title="BM25 matches (top 10)")
    tbl.add_column("id")
    tbl.add_column("bm25", justify="right")
    tbl.add_column("snippet", overflow="fold")
    for r in rows:
        tbl.add_row(str(r["id"]), f"{r['bm25_score']:.4f}", str(r["snippet"]))
    console.print(tbl)
    return [dict(r) for r in rows]


async def check_5b_cross_encoder_bm25(engine: MemoryEngine, query: str, bm25_hits: list[dict[str, Any]]) -> None:
    console.rule("[bold]5b. Cross-encoder scores for BM25 hits (the relevant ones)")
    if not bm25_hits:
        console.print("[yellow]Skipping — no BM25 hits.[/yellow]")
        return
    ce = engine._cross_encoder_reranker.cross_encoder
    pairs = [(query, h["snippet"]) for h in bm25_hits]
    scores = await ce.predict(pairs)
    tbl = Table(title=f"Cross-encoder ({ce.provider_name}) scores on BM25 matches")
    tbl.add_column("id")
    tbl.add_column("bm25", justify="right")
    tbl.add_column("ce", justify="right")
    tbl.add_column("snippet", overflow="fold")
    for h, s in zip(bm25_hits, scores, strict=False):
        color = "green" if s >= 0.05 else ("yellow" if s >= 0.01 else "red")
        tbl.add_row(
            str(h["id"]),
            f"{h['bm25_score']:.4f}",
            f"[{color}]{s:.4f}[/{color}]",
            str(h["snippet"]),
        )
    console.print(tbl)
    console.print("Thresholds: precision=0.05, validation=0.03, analogy=0.02, exploration=0.01")


async def check_4_semantic(engine: MemoryEngine, conn, bank_id: str, query: str) -> list[dict[str, Any]]:
    console.rule("[bold]4. Semantic top-10 (no threshold)")
    vec = engine.embeddings.encode([query])[0]
    emb_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
    rows = await conn.fetch(
        f"""
        SELECT id, left(text, 160) AS snippet,
               1 - (embedding <=> $1::vector) AS similarity
        FROM {fq_table("memory_units")}
        WHERE bank_id = $2 AND embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector
        LIMIT 10
        """,
        emb_str,
        bank_id,
    )
    if not rows:
        console.print("[red]No embeddings in bank — semantic path cannot run.[/red]")
        return []
    tbl = Table(title=f"Top-10 cosine similarity for '{query}'")
    tbl.add_column("id")
    tbl.add_column("sim", justify="right")
    tbl.add_column("snippet", overflow="fold")
    rank = 0
    for r in rows:
        rank += 1
        color = "green" if r["similarity"] >= 0.7 else ("yellow" if r["similarity"] >= 0.5 else "red")
        tbl.add_row(str(r["id"]), f"[{color}]{r['similarity']:.4f}[/{color}]", str(r["snippet"]))
    console.print(tbl)
    console.print("Thresholds for reference: precision=0.70, validation=0.60, analogy/exploration=0.50")
    return [dict(r) for r in rows]


async def check_5_cross_encoder(engine: MemoryEngine, query: str, semantic_hits: list[dict[str, Any]]) -> None:
    console.rule("[bold]5. Cross-encoder scores for semantic top-10")
    if not semantic_hits:
        console.print("[yellow]Skipping — no semantic hits.[/yellow]")
        return
    ce = engine._cross_encoder_reranker.cross_encoder
    pairs = [(query, h["snippet"]) for h in semantic_hits]
    scores = await ce.predict(pairs)

    tbl = Table(title=f"Cross-encoder ({ce.provider_name}) scores")
    tbl.add_column("id")
    tbl.add_column("ce", justify="right")
    tbl.add_column("snippet", overflow="fold")
    for h, s in zip(semantic_hits, scores, strict=False):
        color = "green" if s >= 0.05 else ("yellow" if s >= 0.01 else "red")
        tbl.add_row(str(h["id"]), f"[{color}]{s:.4f}[/{color}]", str(h["snippet"]))
    console.print(tbl)
    console.print("Thresholds: precision=0.05, validation=0.03, analogy=0.02, exploration=0.01")


async def check_6_recall_modes(engine: MemoryEngine, bank_id: str, query: str) -> None:
    console.rule("[bold]6. recall_async across all modes (enable_trace=True)")
    for mode in RetrievalMode:
        console.print(f"\n[bold cyan]mode = {mode.value}[/bold cyan]")
        try:
            result = await engine.recall_async(
                bank_id,
                query,
                enable_trace=True,
                session=Session(mode=mode),
                request_context=RequestContext(),
            )
        except Exception as exc:
            console.print(f"[red]recall_async raised: {type(exc).__name__}: {exc}[/red]")
            continue

        count = len(result.results)
        console.print(f"results returned: [bold]{count}[/bold]")
        for i, fact in enumerate(result.results[:5], start=1):
            text = getattr(fact, "text", None) or getattr(fact, "content", None) or str(fact)
            console.print(f"  {i}. {str(text)[:160]}")
        if result.trace is not None:
            trace_repr = str(result.trace)
            console.print("[dim]trace:[/dim]")
            console.print(trace_repr[:2000])
            if len(trace_repr) > 2000:
                console.print(f"[dim]... ({len(trace_repr) - 2000} more chars)[/dim]")


async def run(bank_id: str, query: str) -> int:
    engine = await create_engine()
    try:
        pool = await engine._ctx.get_pool()
        async with acquire_with_retry(pool) as conn:
            await engine._ctx.authenticate_tenant(RequestContext())
            await check_0_global_inventory(conn, query)
            hits = await check_1_bank_sanity(conn, bank_id, query)
            await check_2_tsvector(conn, [h["id"] for h in hits])
            bm25_hits = await check_3_bm25(conn, bank_id, query)
            sem_hits = await check_4_semantic(engine, conn, bank_id, query)
            await check_5_cross_encoder(engine, query, sem_hits)
            await check_5b_cross_encoder_bm25(engine, query, bm25_hits)
        await check_6_recall_modes(engine, bank_id, query)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only recall diagnostic")
    parser.add_argument("--bank", required=True, help="bank_id to diagnose")
    parser.add_argument("--query", required=True, help="query text to trace")
    args = parser.parse_args()
    return asyncio.run(run(args.bank, args.query))


if __name__ == "__main__":
    sys.exit(main())
