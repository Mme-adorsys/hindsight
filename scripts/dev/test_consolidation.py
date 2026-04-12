#!/usr/bin/env python
"""
Consolidation Integration Test — Reset → Seed → Recall → NCR → Verify.

Tests the revised Consolidation 1 algorithm (Epic 24) with 10 Engrams
that have varying saliency, novelty, and recall patterns.  After seeding
and targeted recalls, triggers NCR and verifies that each Engram landed
in the expected layer (working / buffer / archived).

Usage:
    python scripts/dev/test_consolidation.py --bank-id marcel-engram-dev
    python scripts/dev/test_consolidation.py --bank-id marcel-engram-dev --api-url http://localhost:8889
    python scripts/dev/test_consolidation.py --bank-id marcel-engram-dev --skip-reset
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no deps beyond the API server)
# ---------------------------------------------------------------------------


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


def _delete(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} at {url}: {body_text}") from e


# ---------------------------------------------------------------------------
# Test data definitions
# ---------------------------------------------------------------------------


@dataclass
class TestEngram:
    """Definition of one test retain with expected outcome."""

    tag: str  # unique tag for matching, e.g. "ct-db-01"
    cluster: str  # thematic cluster: db, perf, sec, arch, known
    label: str  # human-readable label
    mode: str  # session mode at retain time
    content: str  # the text to retain
    task_context: str
    recall_count: int  # how many times to recall this engram
    recall_query: str  # query to use for targeted recall
    expected_layer: str  # "working", "buffer", or "archived"
    expected_reason: str  # why we expect this outcome
    expectation: str | None = None
    outcome: str | None = None


def build_test_engrams() -> list[TestEngram]:
    """25 test engrams in 5 thematic clusters (DB, Perf, Sec, Arch, Known).

    Each cluster has 5 related engrams to test ranking quality:
    recalls must differentiate between similar engrams within the same cluster.
    """
    return [
        # ─────────────────────────────────────────────────────────
        # Cluster 1: Database (5 engrams)
        # ─────────────────────────────────────────────────────────
        TestEngram(
            tag="ct-db-01",
            cluster="db",
            label="PostgreSQL 17 MERGE-Befehl",
            mode="exploration",
            content=(
                "PostgreSQL 17 fuehrt einen neuen MERGE-Befehl ein der atomare "
                "Upsert-Operationen ohne ON CONFLICT ermoeglicht. Dies vereinfacht "
                "die Batch-Verarbeitung in ETL-Pipelines erheblich."
            ),
            task_context="Database research",
            recall_count=5,
            recall_query="PostgreSQL 17 MERGE Befehl atomare Upsert ETL",
            expected_layer="buffer",
            expected_reason="5 recalls + exploration threshold 0.5",
        ),
        TestEngram(
            tag="ct-db-02",
            cluster="db",
            label="Connection Pool Optimierung",
            mode="precision",
            content=(
                "Der optimale Connection-Pool-Size fuer unseren PostgreSQL-Server "
                "liegt bei 20 Verbindungen pro Worker-Prozess. Bei mehr als 25 "
                "treten Lock-Contention-Probleme auf die sich in P99-Latenzen "
                "ueber 500ms aeussern."
            ),
            task_context="Database tuning",
            recall_count=5,
            recall_query="Connection Pool Size 20 Verbindungen Worker Lock Contention",
            expected_layer="working",
            expected_reason="Low saliency + precision threshold 0.8 → not enough",
        ),
        TestEngram(
            tag="ct-db-03",
            cluster="db",
            label="Festplattenausfall Incident",
            mode="exploration",
            content=(
                "KRITISCH: Der Produktions-Datenbankserver hatte heute um 03:14 einen "
                "vollstaendigen Festplattenausfall. Alle Schreiboperationen waren fuer "
                "47 Minuten blockiert. Der Failover auf den Standby-Server funktionierte "
                "erst nach manuellem Eingriff weil der Watchdog-Prozess abgestuerzt war."
            ),
            task_context="Incident Post-Mortem",
            recall_count=5,
            recall_query="kritischer Festplattenausfall Produktion 03:14 Failover Standby Watchdog",
            expected_layer="buffer",
            expected_reason="High emotional saliency + 5 recalls",
        ),
        TestEngram(
            tag="ct-db-04",
            cluster="db",
            label="pgvector Index-Optimierung",
            mode="precision",
            content=(
                "Fuer pgvector mit mehr als 100k Vektoren sollte der HNSW-Index "
                "mit m=16 und ef_construction=64 erstellt werden. Der ivfflat-Index "
                "ist bei dieser Groesse signifikant langsamer und weniger genau."
            ),
            task_context="Vector search tuning",
            recall_count=3,
            recall_query="pgvector HNSW Index m ef_construction 100k Vektoren",
            expected_layer="working",
            expected_reason="Only 3 recalls < MIN_ACCESS=5 → access gate",
        ),
        TestEngram(
            tag="ct-db-05",
            cluster="db",
            label="HTTP Standard Timeout",
            mode="precision",
            content=(
                "Die Standard-Timeout-Einstellung fuer HTTP-Verbindungen im "
                "requests-Modul betraegt 30 Sekunden. Fuer interne Microservice-"
                "Aufrufe wird empfohlen den Timeout auf 5 Sekunden zu setzen."
            ),
            task_context="Code review standards",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls → access gate",
        ),
        # ─────────────────────────────────────────────────────────
        # Cluster 2: Performance (5 engrams)
        # ─────────────────────────────────────────────────────────
        TestEngram(
            tag="ct-perf-01",
            cluster="perf",
            label="Caching-Layer verschlechtert Latenz",
            mode="validation",
            content=(
                "Entgegen der Erwartung hat das neue Caching-Layer die API-Latenz "
                "nicht verbessert sondern um 340ms verschlechtert. Ursache: der "
                "Cache-Invalidierungs-Overhead uebersteigt den Lese-Vorteil bei "
                "unserem Schreiblast-dominanten Workload (85% writes)."
            ),
            task_context="Performance Validation",
            expectation="Das Caching-Layer reduziert die API-Latenz um mindestens 50%",
            outcome="Die API-Latenz stieg um 340ms wegen Cache-Invalidierungs-Overhead bei 85% Schreiblast",
            recall_count=5,
            recall_query="Caching Layer API Latenz 340ms verschlechtert Invalidierung Schreiblast",
            expected_layer="buffer",
            expected_reason="High surprise + 5 recalls + validation threshold 0.7",
        ),
        TestEngram(
            tag="ct-perf-02",
            cluster="perf",
            label="P99 Lock-Contention",
            mode="precision",
            content=(
                "P99-Latenzen ueber 500ms wurden auf Lock-Contention im PostgreSQL "
                "Connection-Pool zurueckgefuehrt. Nach Reduzierung der parallelen "
                "Worker von 50 auf 30 fielen die P99-Werte auf unter 200ms."
            ),
            task_context="Performance debugging",
            recall_count=15,
            recall_query="P99 Latenz 500ms Lock Contention Worker reduziert 200ms",
            expected_layer="buffer",
            expected_reason="15 recalls compensate low saliency",
        ),
        TestEngram(
            tag="ct-perf-03",
            cluster="perf",
            label="Memory-Leak in Background Tasks",
            mode="precision",
            content=(
                "Memory-Leak im Background-Task-Worker entdeckt: asyncio Tasks die "
                "nie awaited wurden bleiben im Memory haengen. Fix durch explizites "
                "asyncio.gather() statt fire-and-forget create_task()."
            ),
            task_context="Memory profiling",
            recall_count=10,
            recall_query="Memory Leak Background Task asyncio gather create_task",
            expected_layer="buffer",
            expected_reason="10 recalls + critical content",
        ),
        TestEngram(
            tag="ct-perf-04",
            cluster="perf",
            label="GC Tuning Python",
            mode="exploration",
            content=(
                "Python Garbage Collection kann mit gc.set_threshold(700, 10, 10) "
                "fuer GC-intensive Workloads optimiert werden. Standard ist (700, 10, 10) "
                "aber fuer Long-Running Services hilft (1000, 15, 15)."
            ),
            task_context="Python tuning",
            recall_count=3,
            recall_query="Python Garbage Collection gc set_threshold Long Running",
            expected_layer="working",
            expected_reason="3 recalls < MIN_ACCESS",
        ),
        TestEngram(
            tag="ct-perf-05",
            cluster="perf",
            label="HTTP Keep-Alive Default",
            mode="precision",
            content=(
                "HTTP Keep-Alive ist im Python requests-Modul standardmaessig "
                "deaktiviert wenn keine Session verwendet wird. Mit requests.Session() "
                "bleibt die Connection offen und reduziert TLS-Handshakes."
            ),
            task_context="HTTP tuning",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
        # ─────────────────────────────────────────────────────────
        # Cluster 3: Security (5 engrams)
        # ─────────────────────────────────────────────────────────
        TestEngram(
            tag="ct-sec-01",
            cluster="sec",
            label="Race-Condition Session-Tokens CVE",
            mode="precision",
            content=(
                "SICHERHEITSWARNUNG: In der Authentifizierungs-Middleware wurde eine "
                "Race-Condition entdeckt die es ermoeglicht Session-Tokens zu "
                "duplizieren. CVE-Nummer wurde beantragt. Patch muss vor dem "
                "naechsten Release deployed werden."
            ),
            task_context="Security audit",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls — kein Recall trotz hoher Wichtigkeit",
        ),
        TestEngram(
            tag="ct-sec-02",
            cluster="sec",
            label="SSL Zertifikat Rotation",
            mode="precision",
            content=(
                "SSL-Zertifikate fuer interne Services werden monatlich rotiert "
                "via cert-manager mit Let's Encrypt. Die Rotation erfolgt automatisch "
                "30 Tage vor Ablauf, mit graceful reload der Pods ohne Downtime."
            ),
            task_context="Infrastructure security",
            recall_count=5,
            recall_query="SSL Zertifikat Rotation cert-manager Lets Encrypt monatlich",
            expected_layer="working",
            expected_reason="Low saliency + precision 0.8",
        ),
        TestEngram(
            tag="ct-sec-03",
            cluster="sec",
            label="SQL Injection Legacy-Endpoint",
            mode="validation",
            content=(
                "KRITISCH: SQL Injection in /api/v1/legacy/search entdeckt durch "
                "Penetration Test. User-Input wurde direkt in WHERE-Clause interpoliert. "
                "Fix durch Umstellung auf parameterisierte Queries deployed."
            ),
            task_context="Security incident",
            expectation="Der Legacy-Endpoint nutzt parameterisierte Queries wie alle anderen",
            outcome="User-Input wurde unsicher per String-Concatenation in SQL eingefuegt",
            recall_count=10,
            recall_query="SQL Injection legacy search WHERE parameterisierte Queries Penetration",
            expected_layer="buffer",
            expected_reason="High surprise + 10 recalls",
        ),
        TestEngram(
            tag="ct-sec-04",
            cluster="sec",
            label="Argon2 Password Hashing",
            mode="precision",
            content=(
                "Password-Hashing wurde von bcrypt auf Argon2id umgestellt. Argon2id "
                "bietet besseren Schutz gegen GPU-basierte Brute-Force Angriffe und "
                "ist seit 2015 OWASP empfohlen."
            ),
            task_context="Auth refactoring",
            recall_count=3,
            recall_query="Argon2id bcrypt Password Hashing GPU Brute Force OWASP",
            expected_layer="working",
            expected_reason="3 recalls < MIN_ACCESS",
        ),
        TestEngram(
            tag="ct-sec-05",
            cluster="sec",
            label="CORS Konfiguration",
            mode="precision",
            content=(
                "CORS ist nur fuer die offiziellen Frontend-Domains erlaubt. "
                "Wildcards sind verboten. Allowed-Origins werden in der API-Config "
                "explizit gelistet und nightly gegen Domain-Registry validiert."
            ),
            task_context="API security",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
        # ─────────────────────────────────────────────────────────
        # Cluster 4: Architecture (5 engrams)
        # ─────────────────────────────────────────────────────────
        TestEngram(
            tag="ct-arch-01",
            cluster="arch",
            label="Event-Sourcing Buchhaltungs-Journal",
            mode="analogy",
            content=(
                "Die Event-Sourcing-Architektur verhaelt sich wie ein Buchhaltungs-"
                "Journal: jede Aenderung wird als unveraenderliches Event gespeichert "
                "und der aktuelle Zustand wird durch Replay aller Events rekonstruiert. "
                "Fehler werden nicht ueberschrieben sondern durch Kompensations-Events "
                "korrigiert."
            ),
            task_context="Architecture discussion",
            recall_count=5,
            recall_query="Event Sourcing Buchhaltungs Journal unveraenderlich Replay Kompensation",
            expected_layer="buffer",
            expected_reason="Medium saliency + 5 recalls + analogy threshold 0.6",
        ),
        TestEngram(
            tag="ct-arch-02",
            cluster="arch",
            label="CQRS Trade-offs",
            mode="exploration",
            content=(
                "CQRS trennt Read- und Write-Modelle. Vorteil: optimierte Read-Models "
                "fuer Queries. Nachteil: Eventual Consistency, hoehere Komplexitaet, "
                "doppelte Datenmodelle. Empfohlen ab 100k Reads pro Sekunde."
            ),
            task_context="Architecture decision",
            recall_count=5,
            recall_query="CQRS Read Write Modell Eventual Consistency 100k Reads",
            expected_layer="buffer",
            expected_reason="5 recalls + exploration threshold",
        ),
        TestEngram(
            tag="ct-arch-03",
            cluster="arch",
            label="Microservices Cut-Decision",
            mode="exploration",
            content=(
                "Microservices Cut entschieden anhand Conway's Law: die Service-"
                "Grenzen folgen den Team-Grenzen. Inventory, Pricing und Order sind "
                "separate Bounded Contexts mit eigenen Datenbanken und APIs."
            ),
            task_context="Microservices design",
            recall_count=10,
            recall_query="Microservices Cut Conway Law Bounded Context Inventory Pricing Order",
            expected_layer="buffer",
            expected_reason="10 recalls",
        ),
        TestEngram(
            tag="ct-arch-04",
            cluster="arch",
            label="gRPC vs REST Vergleich",
            mode="analogy",
            content=(
                "gRPC ist 5-7x schneller als REST fuer Service-zu-Service Kommunikation "
                "durch Protocol Buffers und HTTP/2 Multiplexing. REST bleibt besser "
                "fuer oeffentliche APIs wegen Tooling und Browser-Support."
            ),
            task_context="API architecture",
            recall_count=3,
            recall_query="gRPC REST Protocol Buffers HTTP/2 Multiplexing Service",
            expected_layer="working",
            expected_reason="3 recalls < MIN_ACCESS",
        ),
        TestEngram(
            tag="ct-arch-05",
            cluster="arch",
            label="Saga Pattern",
            mode="exploration",
            content=(
                "Saga Pattern verwaltet verteilte Transaktionen ueber mehrere Services "
                "durch eine Kette von Local Transactions mit Compensating Actions bei "
                "Fehlern. Alternative zu 2PC ohne dessen Performance-Probleme."
            ),
            task_context="Distributed systems",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
        # ─────────────────────────────────────────────────────────
        # Cluster 5: Bekanntes/Allgemeinwissen (5 engrams)
        # ─────────────────────────────────────────────────────────
        TestEngram(
            tag="ct-known-01",
            cluster="known",
            label="Python Geschichte",
            mode="exploration",
            content=(
                "Python ist eine interpretierte Programmiersprache die 1991 von "
                "Guido van Rossum entwickelt wurde. Sie zeichnet sich durch eine "
                "klare Syntax und umfangreiche Standardbibliothek aus."
            ),
            task_context="General knowledge",
            recall_count=10,
            recall_query="Python interpretierte Programmiersprache Guido van Rossum 1991",
            expected_layer="buffer",
            expected_reason="10 recalls + low saliency, but exploration threshold 0.5",
        ),
        TestEngram(
            tag="ct-known-02",
            cluster="known",
            label="Git Branch-Name Standard",
            mode="exploration",
            content=(
                "Der Standard Git-Branch-Name wurde in neueren Git-Versionen von "
                "'master' auf 'main' geaendert. Die Umstellung erfolgt automatisch "
                "bei neuen Repositories ab Git Version 2.28."
            ),
            task_context="Developer tooling",
            recall_count=30,
            recall_query="Git Branch Name master main Umstellung Version 2.28",
            expected_layer="buffer",
            expected_reason="30 recalls compensate low saliency",
        ),
        TestEngram(
            tag="ct-known-03",
            cluster="known",
            label="HTTP Status Codes",
            mode="exploration",
            content=(
                "HTTP Status Codes sind in Klassen organisiert: 2xx Erfolg, 3xx "
                "Redirect, 4xx Client-Fehler, 5xx Server-Fehler. 200 OK, 201 Created, "
                "404 Not Found, 500 Internal Server Error sind die haeufigsten."
            ),
            task_context="HTTP basics",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
        TestEngram(
            tag="ct-known-04",
            cluster="known",
            label="REST Prinzipien",
            mode="exploration",
            content=(
                "REST API Prinzipien: stateless, cacheable, uniform interface, "
                "client-server separation, layered system, code on demand (optional). "
                "Definiert von Roy Fielding in seiner Dissertation 2000."
            ),
            task_context="API design",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
        TestEngram(
            tag="ct-known-05",
            cluster="known",
            label="JSON Spezifikation",
            mode="exploration",
            content=(
                "JSON ist ein textbasiertes Datenformat das von JavaScript abstammt "
                "aber sprachunabhaengig ist. Spezifiziert in RFC 8259. Unterstuetzt "
                "Strings, Numbers, Booleans, Arrays, Objects und null."
            ),
            task_context="Data formats",
            recall_count=0,
            recall_query="",
            expected_layer="working",
            expected_reason="0 recalls",
        ),
    ]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _resolve_layer(row: dict) -> str:
    """Determine effective layer from graph row data."""
    status = row.get("status")
    if status == "archived":
        return "archived"
    layer = row.get("layer")
    if layer is None or layer == "working":
        return "working"
    return layer


def _find_engram_by_tag(table_rows: list[dict], tag: str) -> dict | None:
    """Find an engram in the graph response by its unique tag."""
    for row in table_rows:
        tags = row.get("tags") or []
        if tag in tags:
            return row
    return None


@dataclass
class EngramResult:
    """Observed state of one engram after consolidation."""

    tag: str
    cluster: str
    label: str
    novelty: float | None = None
    surprise: float | None = None
    emotional_valence: float | None = None
    saliency: float | None = None
    access_count: int = 0
    strength: float | None = None
    layer: str = "unknown"
    expected_layer: str = ""
    expected_reason: str = ""
    passed: bool = False


def run_test(bank_id: str, base_url: str, skip_reset: bool = False) -> int:
    """Run the full consolidation integration test. Returns exit code."""
    base = base_url.rstrip("/")
    engrams = build_test_engrams()

    # ── Step 1: Reset (all 3 stores: PG + Qdrant + Neo4j) ──────────
    if not skip_reset:
        print("=" * 72)
        print("STEP 1: RESET (Postgres + Qdrant + Neo4j)")
        print("=" * 72)
        try:
            # Use reset_bank.py functions for full 3-store cleanup.
            # The HTTP DELETE only removes PG data — Qdrant vectors with old
            # embedding model stay behind and cause false dedup matches.
            import sys
            from pathlib import Path

            scripts_dir = Path(__file__).resolve().parent
            sys.path.insert(0, str(scripts_dir))
            from reset_bank import _load_env, reset_neo4j, reset_postgres, reset_qdrant

            _load_env()
            pg = reset_postgres(bank_id)
            pg_total = sum(v for v in pg.values() if v > 0)
            qd = reset_qdrant(bank_id)
            neo = reset_neo4j(bank_id)
            print(f"  Postgres: {pg_total} rows deleted")
            print(f"  Qdrant:   {qd} points deleted")
            print(f"  Neo4j:    {neo.get('engrams', 0)} engrams, {neo.get('schemas', 0)} schemas deleted")
        except Exception as exc:
            print(f"  Warning: 3-store reset failed ({exc}), falling back to API delete")
            try:
                _delete(f"{base}/v1/default/banks/{bank_id}")
                print(f"  Bank '{bank_id}' deleted via API (PG only)")
            except Exception:
                print(f"  Bank '{bank_id}' did not exist — clean start")
        print()
    else:
        print("STEP 1: RESET — skipped (--skip-reset)")
        print()

    # ── Step 2: Seed ───────────────────────────────────────────────
    total = len(engrams)
    print("=" * 72)
    print(f"STEP 2: SEED ({total} test engrams in 5 clusters)")
    print("=" * 72)
    for idx, e in enumerate(engrams, 1):
        body: dict = {
            "items": [
                {
                    "content": e.content,
                    "tags": [e.tag],
                }
            ],
            "mode": e.mode,
            "task_context": e.task_context,
        }
        if e.expectation:
            body["items"][0]["expectation"] = e.expectation
        if e.outcome:
            body["items"][0]["outcome"] = e.outcome

        t0 = time.time()
        try:
            resp = _post_json(f"{base}/v1/default/banks/{bank_id}/memories", body)
            elapsed = time.time() - t0
            print(f"  [{idx:2d}/{total}] {e.tag:<12s} {e.label[:40]:<40s}  {elapsed:.1f}s  mode={e.mode}")
        except Exception as exc:
            print(f"  [{idx:2d}/{total}] {e.tag} FAILED: {exc}")
            return 2
    print()

    # ── Step 3: Snapshot after seed ────────────────────────────────
    print("=" * 72)
    print("STEP 3: SNAPSHOT (after seed, before recalls)")
    print("=" * 72)
    graph = _get_json(f"{base}/v1/default/banks/{bank_id}/graph?limit=100")
    table_rows = graph.get("table_rows", [])
    print(f"  Total engrams in bank: {graph.get('total_units', 0)}")
    print()
    print(f"  {'Tag':<12s} {'Layer':<10s} {'Nov':>5s} {'Sur':>5s} {'Emo':>5s} {'Acc':>4s} {'Mode':<12s} Text")
    print(f"  {'---':<12s} {'---':<10s} {'---':>5s} {'---':>5s} {'---':>5s} {'---':>4s} {'---':<12s} ---")
    current_cluster = ""
    for e in engrams:
        if e.cluster != current_cluster:
            current_cluster = e.cluster
            print(f"  ── {e.cluster.upper()} ──")
        row = _find_engram_by_tag(table_rows, e.tag)
        if row:
            ts = row.get("thalamus_scores") or {}
            print(
                f"  {e.tag:<12s} {(row.get('layer') or 'working'):<10s} "
                f"{ts.get('novelty', 0):.2f}  {ts.get('surprise', 0):.2f}  "
                f"{ts.get('emotional_valence', 0):.2f}  "
                f"{row.get('access_count', 0):>3d}  "
                f"{(row.get('session_mode') or '-'):<12s} "
                f"{row.get('text', '')[:50]}"
            )
        else:
            print(f"  {e.tag:<12s} NOT FOUND")
    print()

    # ── Step 4: Targeted recalls ───────────────────────────────────
    print("=" * 72)
    print("STEP 4: TARGETED RECALLS")
    print("=" * 72)
    total_recalls = sum(e.recall_count for e in engrams)
    done_recalls = 0
    for e in engrams:
        if e.recall_count == 0:
            print(f"  {e.tag} — 0 recalls (skipped)")
            continue
        print(f"  {e.tag} — {e.recall_count}x recalls ...", end="", flush=True)
        for i in range(e.recall_count):
            try:
                _post_json(
                    f"{base}/v1/default/banks/{bank_id}/memories/recall",
                    {"query": e.recall_query, "mode": e.mode},
                )
                done_recalls += 1
            except Exception as exc:
                print(f" FAILED at iteration {i + 1}: {exc}")
                break
        print(" done")
    print(f"\n  Total recalls executed: {done_recalls}/{total_recalls}")
    print()

    # ── Step 5: NCR Trigger ────────────────────────────────────────
    print("=" * 72)
    print("STEP 5: NCR TRIGGER (Consolidation 1)")
    print("=" * 72)
    try:
        ncr_resp = _post_json(
            f"{base}/v1/default/banks/{bank_id}/ncr/trigger?phase=c1",
            {"bank_id": bank_id},
            timeout=600.0,
        )
        c1 = ncr_resp.get("consolidation", {})
        print(f"  Duration:     {ncr_resp.get('duration_seconds', 0):.2f}s")
        print(f"  Consolidated: {c1.get('consolidated', 0)}")
        print(f"  Skipped:      {c1.get('skipped', 0)}")
        print(f"  Archived:     {c1.get('archived', 0)}")
        print(f"  Errors:       {c1.get('errors', 0)}")
    except Exception as exc:
        print(f"  NCR FAILED: {exc}")
        return 2
    print()

    # ── Step 6: Verify ─────────────────────────────────────────────
    print("=" * 72)
    print("STEP 6: VERIFICATION REPORT")
    print("=" * 72)

    graph_after = _get_json(f"{base}/v1/default/banks/{bank_id}/graph?limit=100")
    rows_after = graph_after.get("table_rows", [])

    results: list[EngramResult] = []
    for e in engrams:
        row = _find_engram_by_tag(rows_after, e.tag)
        r = EngramResult(
            tag=e.tag,
            cluster=e.cluster,
            label=e.label,
            expected_layer=e.expected_layer,
            expected_reason=e.expected_reason,
        )
        if row:
            ts = row.get("thalamus_scores") or {}
            r.novelty = ts.get("novelty")
            r.surprise = ts.get("surprise")
            r.emotional_valence = ts.get("emotional_valence")
            ev = r.emotional_valence or 0.0
            su = r.surprise or 0.0
            r.saliency = max(ev, su)
            r.access_count = row.get("access_count") or 0
            r.strength = row.get("strength")

            # Determine actual layer.
            # The /graph endpoint does not return engram_dictionary.status,
            # so we detect archived engrams by their signature: strength=0.0
            # and layer still 'working' (C1 sets status='archived' + strength=0
            # but does not change layer).
            layer = row.get("layer")
            strength_val = row.get("strength")

            if strength_val is not None and strength_val == 0.0 and (layer is None or layer == "working"):
                # Archived by C1 (novelty gate sets strength=0.0, status=archived)
                r.layer = "archived"
            elif layer is None or layer == "working":
                r.layer = "working"
            elif layer == "buffer":
                r.layer = "buffer"
            elif layer == "neocortex":
                r.layer = "neocortex"
            else:
                r.layer = layer

            r.passed = r.layer == e.expected_layer
        else:
            # Not found in graph — likely archived (filtered out by status='active')
            if e.expected_layer == "archived":
                r.layer = "archived"
                r.passed = True
            else:
                r.layer = "NOT FOUND"
                r.passed = False

        results.append(r)

    # ── Report ─────────────────────────────────────────────────────
    print()
    header = (
        f"  {'#':>2s}  {'Tag':<12s} {'Label':<35s} {'Nov':>5s} {'Sal':>5s} "
        f"{'Acc':>4s} {'Str':>6s} {'Expected':<10s} {'Actual':<10s} {'':>3s}"
    )
    print(header)
    print(
        f"  {'--':>2s}  {'---':<12s} {'---':<35s} {'---':>5s} {'---':>5s} "
        f"{'---':>4s} {'---':>6s} {'---':<10s} {'---':<10s} {'---':>3s}"
    )

    pass_count = 0
    fail_count = 0
    cluster_stats: dict[str, tuple[int, int]] = {}  # cluster → (pass, fail)
    current_cluster = ""
    for idx, r in enumerate(results, 1):
        if r.cluster != current_cluster:
            current_cluster = r.cluster
            print(f"  ── {r.cluster.upper()} ──")

        mark = "OK" if r.passed else "FAIL"
        if r.passed:
            pass_count += 1
        else:
            fail_count += 1
        cs = cluster_stats.get(r.cluster, (0, 0))
        cluster_stats[r.cluster] = (cs[0] + (1 if r.passed else 0), cs[1] + (0 if r.passed else 1))

        nov = f"{r.novelty:.2f}" if r.novelty is not None else "  -  "
        sal = f"{r.saliency:.2f}" if r.saliency is not None else "  -  "
        acc = f"{r.access_count:>3d}" if r.access_count is not None else "  -"
        stren = f"{r.strength:.3f}" if r.strength is not None else "   -  "

        print(
            f"  {idx:>2d}  {r.tag:<12s} {r.label[:35]:<35s} {nov:>5s} {sal:>5s} "
            f"{acc:>4s} {stren:>6s} {r.expected_layer:<10s} {r.layer:<10s} {mark:>4s}"
        )

    print()
    print(f"  Result: {pass_count}/{len(results)} PASS, {fail_count} FAIL")
    print("  Per cluster:")
    for cluster, (p, f) in sorted(cluster_stats.items()):
        print(f"    {cluster:<8s}  {p}/{p + f} pass")
    print()

    # Print failure details
    if fail_count > 0:
        print("  FAILURES:")
        for r in results:
            if not r.passed:
                print(f"    {r.tag} {r.label}")
                print(f"      Expected: {r.expected_layer} ({r.expected_reason})")
                print(f"      Actual:   {r.layer}")
                print(
                    f"      Novelty={r.novelty}, Saliency={r.saliency}, Access={r.access_count}, Strength={r.strength}"
                )
                print()

    # ── Engram Stats ───────────────────────────────────────────────
    print("  Layer distribution (from /engrams/stats):")
    try:
        stats = _get_json(f"{base}/v1/default/banks/{bank_id}/engrams/stats")
        layers = stats.get("layers", [])
        for layer_info in layers:
            print(
                f"    {layer_info['layer']:<15s} count={layer_info['count']:>3d}  avg_strength={layer_info.get('avg_strength', 0):.3f}"
            )
    except Exception as exc:
        print(f"    Could not fetch stats: {exc}")

    print()
    return 0 if fail_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidation integration test")
    parser.add_argument("--bank-id", default="marcel-engram-dev")
    parser.add_argument("--api-url", default="http://localhost:8889")
    parser.add_argument("--skip-reset", action="store_true", help="Don't delete the bank first")
    args = parser.parse_args()

    print()
    print("=" * 72)
    print("  CONSOLIDATION INTEGRATION TEST")
    print(f"  Bank: {args.bank_id}  API: {args.api_url}")
    print("=" * 72)
    print()

    return run_test(args.bank_id, args.api_url, skip_reset=args.skip_reset)


if __name__ == "__main__":
    sys.exit(main())
