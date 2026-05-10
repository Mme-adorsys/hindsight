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

    Cluster A and B are intentionally very similar in content but differ in
    `time` / `mood` properties — that's the R3 hyper-schema bait. Cluster C
    is a different topic entirely so it doesn't bleed into the others.
    """
    out: list[SeedMemory] = []

    # ── Cluster A: Coffee 1:1 morning, productive ─────────────────────────
    coffee_morning_query = "morning coffee 1on1 with colleague productive"
    for person in ("Anna", "Ben", "Carla", "Dario", "Eva"):
        out.append(
            SeedMemory(
                cluster="coffee_morning",
                content=(
                    f"Had a 30-minute morning coffee 1:1 with {person} at the "
                    "espresso bar. We focused on the sprint plan, made progress "
                    "on the next milestone. Productive session."
                ),
                tags=[
                    "cluster:coffee_morning",
                    "format:1on1",
                    "drink:coffee",
                    "time:morning",
                    "mood:productive",
                    "duration:30",
                ],
                recall_query=coffee_morning_query,
            )
        )

    # ── Cluster B: Coffee 1:1 afternoon, casual ───────────────────────────
    coffee_afternoon_query = "afternoon coffee 1on1 catching up casual"
    for person in ("Felix", "Gina", "Hans", "Inka", "Jonas"):
        out.append(
            SeedMemory(
                cluster="coffee_afternoon",
                content=(
                    f"Took a 30-minute afternoon coffee break with {person}. "
                    "We caught up on personal stuff and team gossip. Relaxed, "
                    "casual chat — no agenda."
                ),
                tags=[
                    "cluster:coffee_afternoon",
                    "format:1on1",
                    "drink:coffee",
                    "time:afternoon",
                    "mood:casual",
                    "duration:30",
                ],
                recall_query=coffee_afternoon_query,
            )
        )

    # ── Cluster C: Friday sprint retro, group session ─────────────────────
    retro_query = "friday sprint retrospective group action items"
    for week in ("week 12", "week 13", "week 14", "week 15", "week 16"):
        out.append(
            SeedMemory(
                cluster="sprint_retro",
                content=(
                    f"Sprint retrospective on Friday for {week}. The full team "
                    "of six gathered, walked through what went well and what "
                    "didn't, captured action items for next week."
                ),
                tags=[
                    "cluster:sprint_retro",
                    "format:group",
                    "participants:6",
                    "time:friday",
                    "mood:reflective",
                    "duration:60",
                ],
                recall_query=retro_query,
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
    for idx, s in enumerate(seeds, 1):
        body = {
            "items": [{"content": s.content, "tags": s.tags}],
            "mode": s.mode,
            "task_context": s.task_context,
        }
        sub = time.time()
        _post_json(f"{base}/v1/default/banks/{bank_id}/memories", body, timeout=180.0)
        by_cluster[s.cluster] = by_cluster.get(s.cluster, 0) + 1
        print(f"  [{idx:2d}/{len(seeds)}] {s.cluster:<20s} {time.time() - sub:5.1f}s")
    print()
    print("  Per cluster:", ", ".join(f"{k}={v}" for k, v in by_cluster.items()))
    print()
    return PhaseReport(name="seed", duration_s=time.time() - t0, payload={"by_cluster": by_cluster})


def phase_recall_loop(base: str, bank_id: str, seeds: list[SeedMemory]) -> PhaseReport:
    print("=" * 72)
    print("STEP 3: TARGETED RECALLS (push access_count past C1 STC gate)")
    print("=" * 72)
    t0 = time.time()
    # One representative query per cluster — all members within a cluster
    # share the same query, so each member should see access_count grow as
    # the recall iterates. Total recalls per cluster ~= recall_count × 1
    # (we issue the cluster's query that many times).
    cluster_queries: dict[str, tuple[str, str, int]] = {}
    for s in seeds:
        if s.cluster in cluster_queries:
            continue
        cluster_queries[s.cluster] = (s.recall_query, s.mode, s.recall_count)

    total = 0
    for cluster, (query, mode, n) in cluster_queries.items():
        hits = 0
        for _ in range(n):
            try:
                resp = _post_json(
                    f"{base}/v1/default/banks/{bank_id}/memories/recall",
                    {"query": query, "mode": mode},
                    timeout=60.0,
                )
                hits += len(resp.get("results", []))
                total += 1
            except Exception as exc:
                print(f"  {cluster} recall failed: {exc}")
                break
        print(f"  {cluster:<20s} {n}× recalls, total returned hits={hits}")
    print(f"\n  Total recalls executed: {total}")
    print()
    return PhaseReport(name="recall", duration_s=time.time() - t0, payload={"total": total})


def phase_ncr(base: str, bank_id: str, phase: str, label: str) -> PhaseReport:
    print("=" * 72)
    print(f"STEP: NCR TRIGGER — phase={phase} ({label})")
    print("=" * 72)
    t0 = time.time()
    resp = _post_json(
        f"{base}/v1/default/banks/{bank_id}/ncr/trigger?phase={phase}",
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
            for layer in stats.get("layers", []):
                print(
                    f"  layer={layer.get('layer'):<12s} count={layer.get('count'):>3d}  "
                    f"avg_strength={layer.get('avg_strength', 0):.3f}"
                )
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
