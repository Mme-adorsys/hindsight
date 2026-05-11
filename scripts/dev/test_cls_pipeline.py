#!/usr/bin/env python
"""End-to-End CLS Pipeline Smoke Test (Epic 25 post-completion).

Walks 15 cluster-friendly memories through Retain → Recall → C1 → C2(×2) → C3
and inspects the resulting :Schema / :HyperSchema graph in Neo4j. Designed
to live next to scripts/dev/test_consolidation.py — that one verifies C1
layer transitions on diverse content; this one verifies that *schemas
actually emerge* in the cortex when the buffer holds clusterable memories.

Usage:
    python scripts/dev/test_cls_pipeline.py
    python scripts/dev/test_cls_pipeline.py --bank-id dev-cls-smoke --api-url http://localhost:8889
    python scripts/dev/test_cls_pipeline.py --skip-reset --skip-c3

Requires:
    docker compose -f docker-compose.dev.yml up -d
    ./scripts/dev/start-api.sh   (API on :8889)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Re-use the 3-store reset helpers from the existing dev script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reset_bank import _load_env, reset_neo4j, reset_postgres, reset_qdrant  # noqa: E402

DEFAULT_BANK = "dev-cls-smoke"
DEFAULT_API = "http://localhost:8889"


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _post_json(url: str, body: dict, timeout: float = 180.0) -> dict:
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
        raise RuntimeError(f"HTTP {e.code} at {url}: {body_text}") from e


def _get_json(url: str, timeout: float = 30.0) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Test data — 3 clusters of 5 closely-related memories
# ---------------------------------------------------------------------------


@dataclass
class SeedMemory:
    cluster: str
    content: str
    tags: list[str]
    mode: str = "exploration"
    task_context: str = "CLS pipeline smoke test"
    recall_count: int = 6  # ≥5 to clear the C1 STC hard gate
    recall_query: str = ""


def build_seed_memories() -> list[SeedMemory]:
    """3×5 cluster-friendly seeds.

    Cluster A and B share the same overall structure (1:1 coffee) but vary
    by time + mood — that's the R3 hyper-schema bait. Cluster C is a
    different topic so it doesn't bleed into the others. Per-memory
    content is *distinct enough* that retain-side dedup (embedding
    similarity threshold) doesn't merge them.
    """
    out: list[SeedMemory] = []

    # ── Cluster A: Coffee 1:1 morning, productive ─────────────────────────
    a_topics = [
        ("Anna", "the new authentication flow", "token rotation approach"),
        ("Ben", "the migration of the orders table", "dry-run first decision"),
        ("Carla", "API response times", "hot path in the recall handler"),
        ("Dario", "the upcoming release", "changelog user-facing buckets"),
        ("Eva", "the on-call rotation", "shift rebalance for the quarter"),
    ]
    for person, topic, outcome in a_topics:
        out.append(
            SeedMemory(
                cluster="coffee_morning",
                content=(
                    f"Had a 30-minute morning coffee one-on-one with {person} at the "
                    f"espresso bar. We talked about {topic} and {outcome}. "
                    "Productive sprint-plan focused session."
                ),
                tags=[
                    "cluster:coffee_morning",
                    "format:1on1",
                    "drink:coffee",
                    "time:morning",
                    "mood:productive",
                    "duration:30",
                ],
                # Per-memory query so the recall loop bumps THIS engram's
                # access_count, not the cluster's strongest one.
                recall_query=f"coffee {person} morning {topic}",
            )
        )

    # ── Cluster B: Coffee 1:1 afternoon, casual ───────────────────────────
    b_topics = [
        ("Felix", "weekend hiking plans"),
        ("Gina", "the new espresso machine in the office"),
        ("Hans", "his recent ski trip to Austria"),
        ("Inka", "a podcast about urban planning"),
        ("Jonas", "the office foosball tournament"),
    ]
    for person, topic in b_topics:
        out.append(
            SeedMemory(
                cluster="coffee_afternoon",
                content=(
                    f"Took a 30-minute afternoon coffee break with {person}. "
                    f"We chatted casually about {topic}. Relaxed, no-agenda "
                    "personal catch-up."
                ),
                tags=[
                    "cluster:coffee_afternoon",
                    "format:1on1",
                    "drink:coffee",
                    "time:afternoon",
                    "mood:casual",
                    "duration:30",
                ],
                recall_query=f"coffee {person} afternoon {topic}",
            )
        )

    # ── Cluster C: Friday sprint retro, group session ─────────────────────
    c_items = [
        ("12", "shipping the schema-explorer hotfix", "split prep tickets earlier"),
        ("13", "the green-light test pass", "automate the dev-stack reset"),
        ("14", "two production incidents", "expand runbooks for cache invalidation"),
        ("15", "rolling out the new retriever", "tighten Qdrant payload defaults"),
        ("16", "the load-test campaign", "raise hint budgets for analogy mode"),
    ]
    for week, win, action in c_items:
        out.append(
            SeedMemory(
                cluster="sprint_retro",
                content=(
                    f"Sprint retrospective on Friday for week {week}. The team of six "
                    f"celebrated {win} and captured the action item to {action} "
                    "for the next sprint."
                ),
                tags=[
                    "cluster:sprint_retro",
                    "format:group",
                    "participants:6",
                    "time:friday",
                    "mood:reflective",
                    "duration:60",
                ],
                recall_query=f"sprint retro week {week} {win}",
            )
        )

    return out


# ---------------------------------------------------------------------------
# Pipeline phases
# ---------------------------------------------------------------------------


@dataclass
class PhaseReport:
    name: str
    duration_s: float = 0.0
    payload: dict = field(default_factory=dict)


def phase_reset(bank_id: str) -> PhaseReport:
    print("=" * 72)
    print("STEP 1: RESET (Postgres + Qdrant + Neo4j)")
    print("=" * 72)
    t0 = time.time()
    _load_env()
    pg = reset_postgres(bank_id)
    pg_total = sum(v for v in pg.values() if v > 0)
    qd = reset_qdrant(bank_id)
    neo = reset_neo4j(bank_id)
    print(f"  Postgres: {pg_total} rows deleted")
    print(f"  Qdrant:   {qd} points deleted")
    print(f"  Neo4j:    {neo.get('engrams', 0)} engrams, {neo.get('schemas', 0)} schemas deleted")
    print()
    return PhaseReport(name="reset", duration_s=time.time() - t0, payload={"pg": pg, "qd": qd, "neo": neo})


def phase_seed(base: str, bank_id: str, seeds: list[SeedMemory]) -> PhaseReport:
    print("=" * 72)
    print(f"STEP 2: SEED ({len(seeds)} memories in 3 clusters)")
    print("=" * 72)
    t0 = time.time()
    by_cluster: dict[str, int] = {}
    persisted_by_cluster: dict[str, int] = {}
    duplicates: list[str] = []
    for idx, s in enumerate(seeds, 1):
        body = {
            "items": [{"content": s.content, "tags": s.tags}],
            "mode": s.mode,
            "task_context": s.task_context,
        }
        sub = time.time()
        resp = _post_json(f"{base}/v1/default/banks/{bank_id}/memories", body, timeout=180.0)
        by_cluster[s.cluster] = by_cluster.get(s.cluster, 0) + 1

        # Surface dedup outcomes — the retain response carries one
        # `outcomes` entry per item with status ∈ {persisted, deduplicated,
        # filtered}. Anything other than `persisted` explains the
        # `/graph` shortfall.
        outcomes = resp.get("outcomes") or resp.get("items") or []
        status = (outcomes[0].get("status") if outcomes else None) or "unknown"
        if status == "persisted":
            persisted_by_cluster[s.cluster] = persisted_by_cluster.get(s.cluster, 0) + 1
        elif status in ("deduplicated", "filtered"):
            duplicates.append(f"{s.cluster}#{idx} → {status}")
        print(
            f"  [{idx:2d}/{len(seeds)}] {s.cluster:<20s} {time.time() - sub:5.1f}s  status={status}"
        )
    print()
    print("  Sent per cluster:     ", ", ".join(f"{k}={v}" for k, v in by_cluster.items()))
    print("  Persisted per cluster:", ", ".join(f"{k}={v}" for k, v in persisted_by_cluster.items()) or "(empty)")
    if duplicates:
        print("  Non-persisted:")
        for d in duplicates:
            print(f"    {d}")
    print()
    return PhaseReport(
        name="seed",
        duration_s=time.time() - t0,
        payload={"by_cluster": by_cluster, "persisted_by_cluster": persisted_by_cluster, "duplicates": duplicates},
    )


def phase_recall_loop(base: str, bank_id: str, seeds: list[SeedMemory]) -> PhaseReport:
    print("=" * 72)
    print("STEP 3: TARGETED RECALLS (push access_count past C1 STC gate)")
    print("=" * 72)
    t0 = time.time()
    # Per-seed distinctive query so each engram gets its own recall hits
    # (otherwise the cluster's strongest engram absorbs everything and the
    # rest stay at access_count=0).
    total = 0
    per_cluster_hits: dict[str, int] = {}
    for s in seeds:
        hits = 0
        for _ in range(s.recall_count):
            try:
                resp = _post_json(
                    f"{base}/v1/default/banks/{bank_id}/memories/recall",
                    {"query": s.recall_query, "mode": s.mode},
                    timeout=60.0,
                )
                hits += len(resp.get("results", []))
                total += 1
            except Exception as exc:
                print(f"  {s.cluster} recall failed: {exc}")
                break
        per_cluster_hits[s.cluster] = per_cluster_hits.get(s.cluster, 0) + hits
        print(f"  {s.cluster:<20s} q='{s.recall_query[:40]}'  hits={hits}")

    print()
    for cluster, hits in per_cluster_hits.items():
        print(f"  Sum {cluster:<20s} hits={hits}")
    print(f"\n  Total recalls executed: {total}")
    print()
    return PhaseReport(name="recall", duration_s=time.time() - t0, payload={"total": total})


def phase_ncr(base: str, bank_id: str, phase: str, label: str, *, force: bool = True) -> PhaseReport:
    """Trigger one NCR phase. ``force=True`` bypasses the per-phase cooldown
    (dev-only escape introduced for this smoke test)."""
    print("=" * 72)
    print(f"STEP: NCR TRIGGER — phase={phase} ({label})")
    print("=" * 72)
    t0 = time.time()
    qs = f"phase={phase}" + ("&force=true" if force else "")
    resp = _post_json(
        f"{base}/v1/default/banks/{bank_id}/ncr/trigger?{qs}",
        {"bank_id": bank_id},
        timeout=600.0,
    )
    dur = resp.get("duration_seconds", 0)
    print(f"  Duration: {dur:.2f}s")

    if phase == "c1":
        c1 = resp.get("consolidation") or {}
        print(
            f"  C1: consolidated={c1.get('consolidated', 0)} skipped={c1.get('skipped', 0)} "
            f"archived={c1.get('archived', 0)} errors={c1.get('errors', 0)}"
        )
    elif phase == "c2":
        c2 = resp.get("c2") or {}
        decay = c2.get("decay") or {}
        print(
            f"  C2: candidates_detected={c2.get('candidates_detected', 0)} "
            f"matured={c2.get('matured', 0)} reinforced={c2.get('reinforced', 0)} "
            f"created={c2.get('created', 0)}"
        )
        if decay:
            print(
                f"  C2 decay: total={decay.get('total', 0)} archived={decay.get('archived', 0)} "
                f"retained={decay.get('retained', 0)} skipped_locked={decay.get('skipped_locked', False)}"
            )
    elif phase == "c3":
        c3 = resp.get("c3") or {}
        r3 = c3.get("r3") or {}
        r5 = c3.get("r5") or {}
        print(
            f"  C3 R3: schemas_scanned={r3.get('schemas_scanned', 0)} "
            f"above_cosine={r3.get('pairs_above_cosine', 0)} "
            f"with_diff={r3.get('pairs_with_property_diff', 0)} "
            f"hyper_created={r3.get('hyper_schemas_created', 0)}"
        )
        print(
            f"  C3 R5: schemas_scanned={r5.get('schemas_scanned', 0)} "
            f"archived={len(r5.get('archived_ids') or [])}"
        )

    if resp.get("errors"):
        print(f"  Errors: {resp['errors']}")
    print()
    return PhaseReport(name=f"ncr-{phase}", duration_s=time.time() - t0, payload=resp)


def phase_inspect_buffer(base: str, bank_id: str) -> PhaseReport:
    print("=" * 72)
    print("STEP: INSPECT BUFFER (Layer distribution + per-engram snapshot)")
    print("=" * 72)
    t0 = time.time()
    try:
        stats = _get_json(f"{base}/v1/default/banks/{bank_id}/engrams/stats")
        if isinstance(stats, dict):
            print(f"  total={stats.get('total', 0)}")
            # EngramStatsResponse.layers is a dict[layer_name, {count, avg_strength}].
            for layer_name, layer_stats in (stats.get("layers") or {}).items():
                if isinstance(layer_stats, dict):
                    print(
                        f"  layer={layer_name:<14s} count={layer_stats.get('count'):>3d}  "
                        f"avg_strength={layer_stats.get('avg_strength', 0):.3f}"
                    )
            sd = stats.get("strength_distribution") or {}
            if sd:
                print(f"  strength: weak={sd.get('weak', 0)} moderate={sd.get('moderate', 0)} strong={sd.get('strong', 0)}")
    except Exception as exc:
        print(f"  /engrams/stats failed: {exc}")

    try:
        graph = _get_json(f"{base}/v1/default/banks/{bank_id}/graph?limit=500")
    except Exception as exc:
        print(f"  /graph failed: {exc}")
        graph = {}
    rows = graph.get("table_rows", []) if isinstance(graph, dict) else []
    by_layer: dict[str, int] = {}
    by_cluster: dict[tuple[str, str], int] = {}
    for row in rows:
        layer = row.get("layer") or "working"
        by_layer[layer] = by_layer.get(layer, 0) + 1
        for tag in row.get("tags") or []:
            if tag.startswith("cluster:"):
                key = (tag.split(":", 1)[1], layer)
                by_cluster[key] = by_cluster.get(key, 0) + 1
    print(f"  /graph: {sum(by_layer.values())} total rows, by layer: {by_layer}")
    if by_cluster:
        print("  per cluster × layer:")
        for (cluster, layer), n in sorted(by_cluster.items()):
            print(f"    {cluster:<20s} layer={layer:<10s} {n}")
    print()
    return PhaseReport(name="inspect-buffer", duration_s=time.time() - t0, payload={"by_layer": by_layer})


def phase_inspect_cortex(base: str, bank_id: str) -> PhaseReport:
    print("=" * 72)
    print("STEP: INSPECT CORTEX (CP /v1/cp/* endpoints)")
    print("=" * 72)
    t0 = time.time()
    try:
        schemas = _get_json(f"{base}/v1/cp/banks/{bank_id}/schemas?limit=50")
    except Exception as exc:
        print(f"  /v1/cp/.../schemas failed: {exc}")
        schemas = []

    if not isinstance(schemas, list):
        schemas = []
    print(f"  Schemas in cortex: {len(schemas)}")
    for s in schemas:
        print(
            f"    id={s.get('id')[:8]}…  evidence={s.get('evidence_count'):>3d}  "
            f"cycles={s.get('cycles_survived'):>2d}  tier={s.get('confidence_tier') or '-'}  "
            f"desc={(s.get('description') or '')[:60]}"
        )
        if s.get("id"):
            try:
                detail = _get_json(f"{base}/v1/cp/schemas/{s['id']}")
                props = detail.get("properties", {}) if isinstance(detail, dict) else {}
                interesting = {k: v for k, v in props.items() if k.startswith(("cluster", "format", "time", "mood"))}
                if interesting:
                    print(f"      props (filtered): {json.dumps(interesting, default=str)}")
                evidence_ids = detail.get("evidence_engram_ids") if isinstance(detail, dict) else []
                if evidence_ids:
                    print(f"      evidence_engram_ids ({len(evidence_ids)}): {evidence_ids[:3]}…")
            except Exception as exc:
                print(f"      detail fetch failed: {exc}")

    try:
        hypers = _get_json(f"{base}/v1/cp/banks/{bank_id}/hyper-schemas?limit=20")
    except Exception as exc:
        print(f"  /v1/cp/.../hyper-schemas failed: {exc}")
        hypers = []
    if not isinstance(hypers, list):
        hypers = []
    print(f"\n  HyperSchemas: {len(hypers)}")
    for h in hypers:
        cids = h.get("children_ids") or []
        print(
            f"    id={h.get('id')[:8]}…  evidence={h.get('evidence_count'):>3d}  "
            f"children={len(cids)}  desc={(h.get('description') or '')[:60]}"
        )
    print()
    return PhaseReport(
        name="inspect-cortex",
        duration_s=time.time() - t0,
        payload={"schema_count": len(schemas), "hyper_count": len(hypers)},
    )


# ---------------------------------------------------------------------------
# Pass / fail summary
# ---------------------------------------------------------------------------


def evaluate_summary(reports: list[PhaseReport]) -> int:
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    by_name = {r.name: r for r in reports}
    schemas_after_c2 = (by_name.get("inspect-cortex-after-c2") or PhaseReport(name="x")).payload.get("schema_count", 0)
    final_cortex = (by_name.get("inspect-cortex-final") or PhaseReport(name="x")).payload
    final_schemas = final_cortex.get("schema_count", 0)
    final_hypers = final_cortex.get("hyper_count", 0)

    c1 = (by_name.get("ncr-c1") or PhaseReport(name="x")).payload.get("consolidation") or {}
    c2_first = (by_name.get("ncr-c2-1") or PhaseReport(name="x")).payload.get("c2") or {}
    c2_second = (by_name.get("ncr-c2-2") or PhaseReport(name="x")).payload.get("c2") or {}

    checks: list[tuple[str, bool, str]] = [
        (
            "C1 promoted ≥ 9 engrams to buffer",
            (c1.get("consolidated", 0) >= 9),
            f"consolidated={c1.get('consolidated', 0)}",
        ),
        (
            "C2 detected clusters in run 1",
            (c2_first.get("candidates_detected", 0) >= 1),
            f"candidates={c2_first.get('candidates_detected', 0)}",
        ),
        (
            "C2 matured ≥ 1 cluster in run 2",
            (c2_second.get("matured", 0) >= 1),
            f"matured={c2_second.get('matured', 0)}",
        ),
        (
            "C2 minted ≥ 2 schemas",
            (c2_second.get("created", 0) >= 2),
            f"created={c2_second.get('created', 0)}",
        ),
        (
            "Cortex shows ≥ 2 schemas after C2",
            (schemas_after_c2 >= 2),
            f"cortex_schemas={schemas_after_c2}",
        ),
        (
            "Final cortex schemas == 3",
            (final_schemas == 3),
            f"cortex_schemas={final_schemas}",
        ),
    ]

    pass_count = sum(1 for _, ok, _ in checks if ok)
    for label, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}  ({detail})")

    if final_hypers >= 1:
        print(f"  [BONUS] HyperSchema emerged ({final_hypers} hyper-schemas)")
    else:
        print("  [INFO]  No HyperSchema (R3 is geometry-sensitive — not a hard fail)")

    print()
    print(f"  Result: {pass_count}/{len(checks)} hard checks passed.")
    print()
    return 0 if pass_count == len(checks) else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(bank_id: str, base_url: str, *, skip_reset: bool, skip_c3: bool) -> int:
    base = base_url.rstrip("/")
    seeds = build_seed_memories()
    reports: list[PhaseReport] = []

    print()
    print("=" * 72)
    print("  EPIC 25 — END-TO-END CLS PIPELINE SMOKE TEST")
    print(f"  Bank: {bank_id}   API: {base}")
    print("=" * 72)
    print()

    if not skip_reset:
        reports.append(phase_reset(bank_id))
    else:
        print("STEP 1: RESET — skipped (--skip-reset)\n")

    reports.append(phase_seed(base, bank_id, seeds))
    reports.append(phase_recall_loop(base, bank_id, seeds))

    reports.append(phase_ncr(base, bank_id, "c1", "Working Memory → Buffer"))
    buf = phase_inspect_buffer(base, bank_id)
    buf.name = "inspect-buffer-after-c1"
    reports.append(buf)

    c2_first = phase_ncr(base, bank_id, "c2", "Pattern Recognition — run 1 (cycles=1)")
    c2_first.name = "ncr-c2-1"
    reports.append(c2_first)

    c2_second = phase_ncr(base, bank_id, "c2", "Pattern Recognition — run 2 (R2 maturation)")
    c2_second.name = "ncr-c2-2"
    reports.append(c2_second)

    cortex_after_c2 = phase_inspect_cortex(base, bank_id)
    cortex_after_c2.name = "inspect-cortex-after-c2"
    reports.append(cortex_after_c2)

    if not skip_c3:
        reports.append(phase_ncr(base, bank_id, "c3", "Schema Restructure — R3 + R5"))
        cortex_final = phase_inspect_cortex(base, bank_id)
        cortex_final.name = "inspect-cortex-final"
        reports.append(cortex_final)
    else:
        # Carry the after-c2 snapshot as the "final" so the summary still works.
        cortex_after_c2_dup = PhaseReport(
            name="inspect-cortex-final",
            duration_s=0,
            payload=cortex_after_c2.payload,
        )
        reports.append(cortex_after_c2_dup)

    return evaluate_summary(reports)


def main() -> int:
    parser = argparse.ArgumentParser(description="CLS pipeline end-to-end smoke test")
    parser.add_argument("--bank-id", default=DEFAULT_BANK)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--skip-reset", action="store_true")
    parser.add_argument("--skip-c3", action="store_true", help="skip the R3+R5 phase")
    args = parser.parse_args()
    return run(args.bank_id, args.api_url, skip_reset=args.skip_reset, skip_c3=args.skip_c3)


if __name__ == "__main__":
    sys.exit(main())
