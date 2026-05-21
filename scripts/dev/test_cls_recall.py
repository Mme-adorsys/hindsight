#!/usr/bin/env python
"""Recall test against a populated bank.

Issues a fixed set of queries against the running API and prints the
top-N hits per mode so we can see what the HybridRetriever surfaces
for a bank that already holds engrams + schemas + hyper-schemas.

Designed to run after ``test_cls_pipeline.py`` has populated the bank;
this script is read-only — no retains, no resets, no NCR triggers.

Usage:
    python scripts/dev/test_cls_recall.py
    python scripts/dev/test_cls_recall.py --bank-id dev-cls-smoke-mixed
    python scripts/dev/test_cls_recall.py --top 8 --modes precision,exploration
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BANK = "dev-cls-smoke-mixed"
DEFAULT_API = "http://localhost:8889"
DEFAULT_TOP = 5
DEFAULT_MODES = ("precision", "exploration", "analogy", "validation")

# Queries chosen to hit both clusters (experience + fact) and probe the
# 5 expected schemas + 2 hyper-schemas in the cortex of the mixed bank.
QUERIES: list[tuple[str, str]] = [
    ("coffee_morning",   "morning coffee one-on-one about sprint planning"),
    ("coffee_afternoon", "afternoon coffee chat about weekend hobbies"),
    ("sprint_retro",     "Friday sprint retrospective with the team"),
    ("tech_facts",       "API latency at p95 for the recall handler"),
    ("team_metrics",     "team velocity in story points per sprint"),
    ("lifestyle_facts",  "kilometres of the riverside trail"),
    ("hyper_schema",     "coffee meeting general"),
]


def _post_json(url: str, body: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} at {url}: {body_text[:200]}") from e


def _short(s: str, n: int = 80) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def run_query(base: str, bank_id: str, query: str, mode: str, top: int) -> list[dict]:
    """Issue a single recall and return the (truncated) results list."""
    resp = _post_json(
        f"{base}/v1/default/banks/{bank_id}/memories/recall",
        {"query": query, "mode": mode, "max_tokens": 4000},
        timeout=60.0,
    )
    return (resp.get("results") or [])[:top]


def render_hit(idx: int, hit: dict) -> str:
    # Recall results carry diverse shapes; the HybridRetriever decorates
    # entries with their kind (engram vs schema). Fall back gracefully
    # when fields are missing — the goal is observability not validation.
    kind = hit.get("kind") or ("schema" if hit.get("schema_id") else "engram")
    score = hit.get("score") or hit.get("rerank_score") or hit.get("activation") or 0.0
    text = hit.get("text") or hit.get("description") or hit.get("content") or ""
    tags = hit.get("tags") or []
    tag_str = ",".join(t for t in tags if isinstance(t, str))[:30]
    return (
        f"    [{idx + 1:2d}] {kind:7s} score={float(score):.3f}  "
        f"tags=[{tag_str}]  text={_short(text, 75)!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="CLS recall probe")
    parser.add_argument("--bank-id", default=DEFAULT_BANK)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument(
        "--modes",
        default=",".join(DEFAULT_MODES),
        help="comma-separated mode list (precision,exploration,analogy,validation)",
    )
    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    print()
    print("=" * 72)
    print(f"  CLS RECALL PROBE — Bank: {args.bank_id}   Modes: {','.join(modes)}")
    print("=" * 72)

    for label, query in QUERIES:
        print()
        print("─" * 72)
        print(f"  Q[{label}] :: {query!r}")
        print("─" * 72)
        for mode in modes:
            try:
                hits = run_query(base, args.bank_id, query, mode, args.top)
            except Exception as exc:
                print(f"  [{mode}] ERROR: {exc}")
                continue
            print(f"  [{mode}] {len(hits)} hits:")
            if not hits:
                print("    (no hits)")
                continue
            for idx, hit in enumerate(hits):
                print(render_hit(idx, hit))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
