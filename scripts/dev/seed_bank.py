#!/usr/bin/env python
"""
Seed a memory bank with a didactic set of retains that exercise different
aspects of the retain algorithm.

Thematic plot arc: "Dev-Stack Bring-Up 2026-04-09" — we narrate today's
session (parallel dev stack, Phase B observability, post-verification bugs)
across 7 retains that vary session_mode, task_context, expectation/outcome,
tags, and document_id grouping. Each retain is explicitly designed to show
one or more features of the retain pipeline:

    1. exploration, no experience       — baseline Thalamus novelty=1.0
    2. validation, expectation ≠ outcome — real prediction error
    3. exploration, batch + document_id — multi-item grouping + causal links
    4. precision, expectation ≈ outcome — strict dedup, low surprise
    5. analogy, high tag density        — weak-link traversal hook
    6. validation, negative outcome     — future-improvement edge case
    7. precision, epic closure          — semantic overlap with earlier items

After the 7 retains we fire one recall and one NCR trigger so the bank is
ready for exploration in the CP.

Usage:
    python scripts/dev/seed_bank.py --bank-id marcel-engram-dev
    python scripts/dev/seed_bank.py --bank-id marcel-engram-dev --api-url http://localhost:8889
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class SeedRetain:
    """One retain call with full provenance and one or more content items."""

    mode: str
    task_context: str
    items: list[dict]
    document_id: str | None = None
    label: str = ""
    purpose: str = ""  # human-readable summary of what this retain demonstrates
    tags_all: list[str] = field(default_factory=list)

    def build_request_body(self) -> dict:
        items_payload = []
        for item in self.items:
            payload = {"content": item["content"]}
            if "expectation" in item:
                payload["expectation"] = item["expectation"]
            if "outcome" in item:
                payload["outcome"] = item["outcome"]
            if "tags" in item:
                payload["tags"] = item["tags"]
            elif self.tags_all:
                payload["tags"] = self.tags_all
            if self.document_id:
                payload["document_id"] = self.document_id
            items_payload.append(payload)
        return {
            "items": items_payload,
            "mode": self.mode,
            "task_context": self.task_context,
        }


def build_seed_script() -> list[SeedRetain]:
    return [
        SeedRetain(
            label="1. Dev-Stack Ports (exploration, no experience)",
            purpose=(
                "Baseline Thalamus scoring. No expectation/outcome → surprise=0.3 "
                "default. task_relevance = cosine(content, 'Dev-Stack bring-up'). "
                "Novelty=1.0 because this is the first retain in the fresh bank."
            ),
            mode="exploration",
            task_context="Dev-Stack bring-up",
            tags_all=["dev-stack", "ports", "infrastructure"],
            items=[
                {
                    "content": (
                        "Der MM-Dev-Stack läuft auf alternativen Ports: API :8889, "
                        "CP :3001, Postgres :5433, Neo4j :7475 (HTTP) + :7688 (Bolt), "
                        "Qdrant :6336 (HTTP) + :6337 (gRPC). Die alte Hindsight-Instanz "
                        "auf :8888 / :9999 bleibt unberührt und läuft parallel weiter."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="2. Pipeline Tracer Persistence (validation, surprise)",
            purpose=(
                "Validation mode with real prediction error. expectation 9 steps, "
                "outcome 8 steps → surprise ≈ 1 - cos(expectation, outcome). "
                "Demonstrates that Thalamus picks up genuine mismatch."
            ),
            mode="validation",
            task_context="Phase B Pipeline Tracer verification",
            tags_all=["phase-b", "pipeline-tracer", "verification"],
            items=[
                {
                    "content": (
                        "Der Universal Pipeline Tracer persistiert retain-Traces in "
                        "einer neuen Tabelle retain_traces und NCR-Traces als JSONB "
                        "Spalte ncr_runs.trace_data."
                    ),
                    "expectation": (
                        "Der erste Retain nach API-Restart legt einen Eintrag in "
                        "retain_traces an mit allen 9 Pipeline-Steps von r0_sequence_analysis "
                        "bis r9_observations."
                    ),
                    "outcome": (
                        "Erster Eintrag hatte nur 8 Steps, weil der Retain-Pfad über "
                        "die r3_deduplication replacement-Branch lief und r8_schema_fit "
                        "übersprang. Das ist ein Code-Pfad-Detail, kein Tracer-Fehler."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="3. Trace Shape + CP View (exploration, batch, document)",
            purpose=(
                "Batch retain with 2 items sharing a document_id. Exercises "
                "temporal_proximity + semantic links between co-retained items, "
                "schema-fit check for the batch, and causal link via shared doc."
            ),
            mode="exploration",
            task_context="Phase B Pipeline Tracer verification",
            document_id="phase-b-verification",
            tags_all=["cp", "observability", "pipeline-trace-view"],
            items=[
                {
                    "content": (
                        "retain_traces.trace_data JSONB hat ein flaches steps-Array "
                        "mit name, phase, started_at, duration_ms, status, inputs, "
                        "outputs, rationale und error Feld pro Step."
                    ),
                    "expectation": (
                        "Die trace_data JSONB enthält eine flache Steps-Array-Struktur "
                        "mit allen 9 Metadaten-Feldern pro Step."
                    ),
                    "outcome": (
                        "Bestätigt: curl auf /retain_traces/{id} liefert genau diese "
                        "Struktur inkl. rationale strings wie 'score-aware dedup kept 1, "
                        "dropped 0, replaced 1'."
                    ),
                },
                {
                    "content": (
                        "Die CP PipelineTraceView Komponente rendert einen Trace als "
                        "vertikale Timeline mit Status-Dots (green/red/grey), Duration-"
                        "Badges, Rationale-Subzeilen und expandable Inputs/Outputs-JSON."
                    ),
                    "expectation": (
                        "Click auf 'Show Retain Traces' im Memory Detail Panel öffnet "
                        "Modal mit Liste. Click auf Row lädt den Detail-Trace und rendert "
                        "die Timeline mit 8 Steps."
                    ),
                    "outcome": (
                        "Bug gefunden: Modal war nur im non-inPanel Return-Block. "
                        "Alle vier Call-Sites nutzen inPanel=true, daher Modal nie "
                        "gerendert. Fix in Commit 60541c6 durch Duplikation des Modal-"
                        "Blocks in den inPanel-Zweig."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="4. Graph Endpoint Bug (precision, near-dup)",
            purpose=(
                "Precision mode with tight Thalamus threshold. Content overlaps "
                "semantically with item 3 (both about CP observability bugs) → "
                "dedup check will compare embeddings. Causal chain to earlier items."
            ),
            mode="precision",
            task_context="CP Memory Detail Panel Provenance Bug",
            tags_all=["bug", "cp", "graph-endpoint", "phase-a5"],
            items=[
                {
                    "content": (
                        "Der /graph-Endpoint lieferte zunächst keine Phase-A5-Felder "
                        "(tags, session_mode, task_context, retain_context), weil "
                        "admin_operations.get_graph_data das SELECT nicht erweitert "
                        "hatte. Nur admin_operations.list_memory_units war in Phase A4 "
                        "gefixt worden."
                    ),
                    "expectation": (
                        "Hard-Reload im Browser und das Memory Detail Panel zeigt "
                        "alle Phase-A5-Sections für neu retainte Memories."
                    ),
                    "outcome": (
                        "Noch immer nichts gezeigt. Debug ergab: die Memory Table "
                        "liest aus /graph.table_rows, nicht aus /memories/list. Fix "
                        "in Commit ca39dad durch Erweitern der get_graph_data SELECT "
                        "um die 6 fehlenden Spalten."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="5. Bio-Mapping Analogy (analogy mode, no experience)",
            purpose=(
                "Analogy mode boosts thalamus emotional_valence weighting. High "
                "tag density for schema-fit matching. Weak-link traversal hook "
                "for later recall. No expectation/outcome — pure analogy insight."
            ),
            mode="analogy",
            task_context="Bio-Mapping vertiefen",
            tags_all=["bio-mapping", "analogy", "observability", "lfp-metaphor", "neuroscience"],
            items=[
                {
                    "content": (
                        "Der Universal Pipeline Tracer ist analog zu einer "
                        "Oszilloskop-Spur im neurowissenschaftlichen Experiment: "
                        "pro Pipeline-Durchlauf werden alle beobachtbaren Signale "
                        "als TraceSteps erfasst (Timing, Input, Output, Begründung). "
                        "Man kann später post-hoc analysieren warum eine Entscheidung "
                        "so fiel — genau wie ein Neurowissenschaftler eine LFP-Aufnahme "
                        "nach der Session auswertet um Encoding-Muster zu verstehen."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="6. r8 Edge Case (validation, weak surprise)",
            purpose=(
                "Validation mode again, but this time with a nuanced prediction "
                "error — outcome is not opposite to expectation, just more detailed. "
                "Thalamus surprise should be lower than item 2."
            ),
            mode="validation",
            task_context="Phase B Tracer edge cases",
            tags_all=["phase-b", "r8-schema-fit", "edge-case", "future-improvement"],
            items=[
                {
                    "content": (
                        "Wenn ein Retain kein neues Engram erzeugt, weil r3_deduplication "
                        "als REPLACE resolved wird, läuft der Neo4j-dual-write-Block nicht "
                        "und damit auch kein r8_schema_fit Step im Tracer."
                    ),
                    "expectation": (
                        "Der r8_schema_fit Step erscheint in jedem Retain-Trace, auch "
                        "wenn keine Schemas matchen — dann als status=ok mit 0 "
                        "schema_links."
                    ),
                    "outcome": (
                        "r8_schema_fit fehlt komplett wenn der Retain via REPLACE "
                        "resolved wird, weil der umgebende if-Block gar nicht erst "
                        "betreten wird. Konsistent, aber nicht optimal für die Timeline — "
                        "sollte als status=skipped mit rationale explizit erfasst werden."
                    ),
                },
            ],
        ),
        SeedRetain(
            label="7. Phase B Epic Closure (precision, high overlap)",
            purpose=(
                "Precision mode, task_context semantically adjacent to items 2-3 "
                "('Phase B ...'). Expected high semantic link density. Potential "
                "NCR Phase 3 schema candidate after cycles survive."
            ),
            mode="precision",
            task_context="Observability Epic closure",
            tags_all=["phase-b", "observability", "epic-closure", "milestone"],
            items=[
                {
                    "content": (
                        "Observability Phase B ist abgeschlossen mit 8 sequentiellen "
                        "Commits: 1ddea3d (B1 tracer base), 6a62810 (B2 migrations), "
                        "64154cd (B3 retain hooks), b1567d3 (B4 NCR hooks), 9401cc9 "
                        "(B5 recall wrapper), ff9feeb (B6 API endpoints), 69926ec "
                        "(B7 CP PipelineTraceView), 67cd196 (prettier). Plus zwei "
                        "Post-Verification-Fixes: 60541c6 (modal inPanel bug) und "
                        "ca39dad (graph endpoint provenance)."
                    ),
                    "expectation": (
                        "Alle drei Tracer-Streams (retain, recall, NCR) erzeugen "
                        "Traces die in der CP als PipelineTraceView sichtbar sind."
                    ),
                    "outcome": (
                        "Retain-Traces: persistiert + im Modal sichtbar. NCR-Traces: "
                        "persistiert + im History-Expand sichtbar. Recall-Traces: "
                        "transient + im neuen Pipeline-Tab sichtbar. Alle drei "
                        "live-verifiziert am 2026-04-09 gegen marcel-engram-dev."
                    ),
                },
            ],
        ),
    ]


def _post_json(url: str, body: dict, timeout: float = 120.0) -> dict:
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


def _get_json(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a bank with didactic retains")
    parser.add_argument("--bank-id", required=True)
    parser.add_argument("--api-url", default="http://localhost:8889")
    parser.add_argument("--skip-ncr", action="store_true", help="Don't trigger NCR after seeding")
    parser.add_argument("--skip-recall", action="store_true", help="Don't run a test recall after seeding")
    args = parser.parse_args()

    bank_id = args.bank_id
    base = args.api_url.rstrip("/")

    print(f"Seeding bank '{bank_id}' at {base}")
    print("=" * 72)

    retains = build_seed_script()
    start_total = time.time()

    for idx, r in enumerate(retains, start=1):
        print(f"\n[{idx}/{len(retains)}] {r.label}")
        print(f"       purpose: {r.purpose}")
        body = r.build_request_body()
        print(f"       mode={r.mode}  task_context='{r.task_context[:50]}'  items={len(body['items'])}")

        t0 = time.time()
        try:
            resp = _post_json(f"{base}/v1/default/banks/{bank_id}/memories", body)
            elapsed = time.time() - t0
            print(f"       ✓ {elapsed:.2f}s  success={resp.get('success')}  usage={resp.get('usage', {}).get('total_tokens', 0)}tok")
        except Exception as exc:
            print(f"       ✗ FAILED: {exc}")
            return 2

    print("\n" + "=" * 72)
    if not args.skip_recall:
        print("\nRunning a smoke-test recall...")
        try:
            recall_resp = _post_json(
                f"{base}/v1/default/banks/{bank_id}/memories/recall",
                {"query": "Was haben wir in Phase B gebaut?", "mode": "exploration", "trace": True},
            )
            num_results = len(recall_resp.get("results", []))
            steps = len(recall_resp.get("pipeline_trace", {}).get("steps", []))
            print(f"  ✓ recall returned {num_results} results, pipeline_trace has {steps} steps")
        except Exception as exc:
            print(f"  ✗ recall failed: {exc}")

    if not args.skip_ncr:
        print("\nTriggering NCR...")
        try:
            ncr_resp = _post_json(
                f"{base}/v1/default/banks/{bank_id}/ncr/trigger",
                {"bank_id": bank_id},
            )
            print(
                f"  ✓ NCR complete in {ncr_resp.get('duration_seconds', 0):.2f}s  "
                f"consolidated={ncr_resp.get('consolidation', {}).get('consolidated', 0)}  "
                f"decayed={ncr_resp.get('phase1_decay', {}).get('decayed', 0)}  "
                f"promoted={ncr_resp.get('phase2_strengthen', {}).get('promoted', 0)}"
            )
        except Exception as exc:
            print(f"  ✗ NCR failed: {exc}")

    print("\n" + "=" * 72)
    print("\nFinal bank summary:")
    try:
        graph = _get_json(f"{base}/v1/default/banks/{bank_id}/graph?limit=100")
        total = graph.get("total_units", 0)
        with_mode = sum(1 for r in graph.get("table_rows", []) if r.get("session_mode"))
        with_task = sum(1 for r in graph.get("table_rows", []) if r.get("task_context"))
        with_rc = sum(1 for r in graph.get("table_rows", []) if r.get("retain_context"))
        print(f"  total engrams:      {total}")
        print(f"  with session_mode:  {with_mode}")
        print(f"  with task_context:  {with_task}")
        print(f"  with retain_context:{with_rc}")
    except Exception as exc:
        print(f"  ✗ summary failed: {exc}")

    print(f"\nTotal seeding time: {time.time() - start_total:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
