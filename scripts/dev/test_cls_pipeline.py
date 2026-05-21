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
from reset_bank import _load_env, list_qdrant_schema_ids, reset_neo4j, reset_postgres, reset_qdrant  # noqa: E402

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


_MORNING_PEOPLE = [
    "Anna", "Ben", "Carla", "Dario", "Eva",
    "Finn", "Greta", "Hugo", "Iris", "Jonas",
    "Klara", "Lars", "Maja", "Nils", "Ola",
    "Pia", "Quentin", "Rosa", "Sami", "Tilda",
    "Ulli", "Vera", "Wim", "Xander", "Yara",
    "Zane", "Aiden", "Brooke", "Chen", "Dasha",
    "Esra", "Faruk", "Gabi", "Hari", "Inga",
    "Juno", "Kim", "Lior", "Mira", "Nora",
]
_MORNING_TOPICS = [
    ("the new authentication flow", "token rotation approach"),
    ("the migration of the orders table", "dry-run first decision"),
    ("API response times", "hot path in the recall handler"),
    ("the upcoming release", "changelog user-facing buckets"),
    ("the on-call rotation", "shift rebalance for the quarter"),
    ("the embedding cache rollout", "warm-up job for cold starts"),
    ("the analytics pipeline backlog", "weekly grooming cadence"),
    ("the staging environment refresh", "snapshot-restore script"),
    ("the postgres index review", "covering index for the hot query"),
    ("the queue worker autoscaling", "p95 latency-driven policy"),
    ("the new feature flag service", "rollout cohort design"),
    ("the monorepo build cache", "remote artifact store"),
    ("the gRPC vs REST tradeoff", "deprecation path for v1 callers"),
    ("the kafka consumer lag spike", "rebalance after partition split"),
    ("the OAuth migration plan", "session rotation cutover window"),
    ("the spec for the export job", "schema versioning convention"),
    ("the load test results", "saturation point at 2.5k rps"),
    ("the chaos test schedule", "weekly fail-injection slot"),
    ("the data warehouse model", "slowly-changing dimensions for users"),
    ("the cost dashboard", "monthly spend-per-feature breakdown"),
    ("the SDK versioning policy", "semver enforcement in CI"),
    ("the rate limit redesign", "token bucket with per-tenant capacity"),
    ("the search relevance regression", "dual-encoder retraining plan"),
    ("the auth audit log gaps", "structured fields for SIEM ingest"),
    ("the dependency upgrade sweep", "automated PR generation"),
    ("the i18n rollout for the dashboard", "extraction script and review"),
    ("the team's PR turnaround", "review SLA target of 24h"),
    ("the migration playbook draft", "step-by-step rollback decisions"),
    ("the incident response retro", "MTTR target adjustment"),
    ("the API gateway rewrite", "phased shadow-traffic plan"),
    ("the secret rotation cadence", "automated weekly rotation script"),
    ("the test flakiness dashboard", "owner assignment per top-5 flaky test"),
    ("the platform tier proposal", "free / pro / enterprise feature matrix"),
    ("the developer onboarding doc", "first-day setup script"),
    ("the metrics pipeline rewrite", "Prometheus to OpenTelemetry migration"),
    ("the customer feedback triage", "weekly synthesis ritual"),
    ("the support ticket SLA", "tier-2 routing rules"),
    ("the architecture decision log", "ADR template and naming convention"),
    ("the new design system rollout", "component migration checklist"),
    ("the privacy compliance review", "DSR fulfillment workflow"),
]
_AFTERNOON_PEOPLE = [
    "Felix", "Gina", "Hans", "Inka", "Jonas",
    "Karin", "Liam", "Mei", "Noah", "Oksana",
    "Paolo", "Quinn", "Rosa", "Samir", "Tom",
    "Uma", "Viktor", "Wren", "Xenia", "Yusuf",
    "Zoe", "Alex", "Bruno", "Cleo", "Dimi",
    "Eli", "Fanny", "Gregor", "Hella", "Ivan",
    "Jana", "Kostas", "Lina", "Mats", "Nadia",
    "Olek", "Petra", "Roman", "Selma", "Tibor",
]
_AFTERNOON_TOPICS = [
    "weekend hiking plans",
    "the new espresso machine in the office",
    "his recent ski trip to Austria",
    "a podcast about urban planning",
    "the office foosball tournament",
    "her vegetable garden harvest",
    "the latest indie game release",
    "a documentary about glaciers",
    "the half marathon they signed up for",
    "the photography exhibition downtown",
    "their toddler's first words",
    "the rainy weekend at the cabin",
    "a homemade pasta recipe",
    "the road trip through the alps",
    "a chess tournament at the local club",
    "the dog adoption from the shelter",
    "the bouldering gym opening",
    "their bee-keeping side hobby",
    "the rooftop garden project",
    "a stand-up comedy night",
    "their pottery class progress",
    "the trail run last Saturday",
    "the kayaking trip on the river",
    "a vinyl record they just bought",
    "the surf weekend on the coast",
    "the book club's next pick",
    "the language exchange meetup",
    "the homebrew beer batch",
    "the bike repair workshop",
    "the cooking class they took",
    "a new sourdough starter",
    "the camping trip in autumn",
    "the open mic night they attended",
    "a wine tasting at the vineyard",
    "the birdwatching weekend",
    "their garden tomato harvest",
    "the dance class on Tuesdays",
    "a marathon training plan",
    "the rock climbing gym",
    "a museum visit on the weekend",
]
_RETRO_THEMES = [
    ("shipping the schema-explorer hotfix", "split prep tickets earlier"),
    ("the green-light test pass", "automate the dev-stack reset"),
    ("two production incidents", "expand runbooks for cache invalidation"),
    ("rolling out the new retriever", "tighten Qdrant payload defaults"),
    ("the load-test campaign", "raise hint budgets for analogy mode"),
    ("closing the auth migration", "track session-rotation metrics weekly"),
    ("the dashboard refresh launch", "add e2e tests for the top widgets"),
    ("hitting the latency SLO", "publish the runbook for cache saturation"),
    ("the embedding cache rollout", "schedule cold-start warm-ups nightly"),
    ("the postgres index sweep", "set query-cost alerts on the hot path"),
    ("the kafka partition rebalance", "document the lag-recovery procedure"),
    ("the feature-flag service launch", "review cohort definitions monthly"),
    ("the staging refresh job", "add snapshot freshness alerting"),
    ("the chaos engineering kickoff", "publish a quarterly chaos calendar"),
    ("the SDK 2.0 release", "automate changelog generation in CI"),
    ("the rate limit redesign", "ship per-tenant token bucket configs"),
    ("the i18n launch in the dashboard", "rotate translation reviewers monthly"),
    ("the privacy compliance audit", "automate DSR ticket templates"),
    ("the dependency upgrade sweep", "set a monthly cadence with owners"),
    ("the metrics pipeline migration", "decommission the legacy collector"),
    ("the cost-dashboard launch", "tag every PR with a feature owner"),
    ("the API gateway shadow rollout", "track parity dashboards daily"),
    ("the onboarding doc rewrite", "add a self-serve setup test"),
    ("the support routing rules update", "publish SLAs per tier"),
    ("the architecture decision rituals", "back-fill ADRs for last quarter"),
    ("the design system migration", "set a per-team migration deadline"),
    ("the load-test infrastructure", "share saturation reports weekly"),
    ("the developer onboarding tooling", "track time-to-first-PR"),
    ("the security review process", "checklist for every external dep"),
    ("the search relevance regression fix", "weekly relevance review meeting"),
    ("the customer feedback synthesis", "ship a monthly summary post"),
    ("the analytics pipeline backlog", "groom tickets every Friday"),
    ("the migration playbook draft", "rehearse with the on-call team"),
    ("the platform tier proposal", "validate with three lighthouse customers"),
    ("the secret rotation rollout", "auto-rotate weekly without manual steps"),
    ("the kafka consumer lag spike fix", "set alerts on partition lag"),
    ("the OAuth cutover", "monitor session rotation rates daily"),
    ("the dashboard launch follow-up", "track widget usage per cohort"),
    ("the API rewrite roadmap", "freeze v1 deprecation date"),
    ("the team rituals refresh", "test new retro format for 6 weeks"),
]


_TECH_FACTS = [
    "The authentication service issues JWT tokens with a one-hour expiration window.",
    "PostgreSQL handles 95% of read traffic through the read replica.",
    "API latency at p95 stays under 200 milliseconds for the recall handler.",
    "The orders table holds approximately 50 million rows.",
    "The embedding model produces 384-dimensional vectors with cosine distance.",
    "Qdrant stores engram embeddings in a single collection named 'engrams'.",
    "Neo4j indexes :Schema and :HyperSchema nodes by centroid_qdrant_id.",
    "The retain pipeline extracts on average 2.3 facts per source memory.",
    "Token bucket rate limits cap free tier users at 60 requests per minute.",
    "The CI build cache reduces test suite runtime from 14 minutes to 3 minutes.",
    "OAuth refresh tokens rotate every 30 days under the current policy.",
    "The staging snapshot job runs at 02:00 UTC each weekday.",
    "Feature flags route 5% of traffic to the new retriever by default.",
    "Kafka consumer lag stays below 500 messages across all production topics.",
    "The CDN serves static assets from 47 edge locations.",
    "API gateway logs retain raw entries for 30 days and aggregates for one year.",
    "The chaos test schedule runs one fault injection per Wednesday.",
    "Service mesh sidecars add roughly 4 milliseconds of overhead per request.",
    "Postgres connection pool is sized to 80 connections per primary instance.",
    "The SDK supports Python 3.10+, Node 18+, and Rust 1.74+.",
    "Audit logs ship to the SIEM in JSON Lines format every five seconds.",
    "The cost dashboard refreshes feature-level spend nightly at 03:00.",
    "Search reranking adds 12 milliseconds median latency on top of retrieval.",
    "The metrics pipeline emits 1.2 million data points per minute.",
    "Documentation site builds rebuild on every merge to main within 90 seconds.",
    "The thalamus filter computes four scores: novelty, surprise, relevance, valence.",
    "Buffer engrams clear the C1 STC gate at access_count of five or more.",
    "The schema-fingerprint match threshold is fixed at 0.85 cosine similarity.",
    "HDBSCAN requires a minimum cluster size of three to emit a candidate.",
    "Reflect operations run on demand and consume no nightly compute budget.",
    "The control plane Next.js dev server picks an available port at startup.",
    "The development docker stack exposes Qdrant on port 6336 and Neo4j on 7688.",
    "Bank session count increments only on explicit session close.",
    "The retain orchestrator caches memory embeddings per content index.",
    "Cross-encoder reranking accepts up to 64 candidate documents per call.",
    "Decay halves an engram's score every 7 days without reinforcement.",
    "Schema centroids are L2-normalised before storage in Qdrant.",
    "The platform supports five active session modes per agent.",
    "Pre-commit hooks block commits with ruff or type errors.",
    "Embedding throughput on local GPU peaks at 800 tokens per second.",
]
_LIFESTYLE_FACTS = [
    "The riverside trail north of the city extends 12 kilometres along the bank.",
    "Bouldering gyms in the city charge 18 euros for a single day pass.",
    "Sourdough starters need daily feeding for the first two weeks after activation.",
    "Bee colonies in moderate climates need at least 60 pounds of winter honey.",
    "Public libraries in the district open from 09:00 to 19:00 on weekdays.",
    "Espresso brewing extracts at 9 bars of pressure within 25 to 30 seconds.",
    "Vegetable gardens yield roughly two kilograms of tomatoes per square metre.",
    "Half marathons cover 21.0975 kilometres on a certified course.",
    "Vinyl records spin at 33.3, 45, or 78 revolutions per minute.",
    "Indoor climbing routes are graded from 4 to 9 on the French scale.",
    "Most board games for adults take between 60 and 120 minutes to finish.",
    "Standard chess clocks allocate three minutes per move in classical play.",
    "Trail running shoes typically last 600 to 800 kilometres before retirement.",
    "Local farmers markets open Saturday morning between 08:00 and 13:00.",
    "Specialty coffee shops use water at 92 to 96 degrees Celsius for pour-over.",
    "Public swimming pools in the area maintain 27 degrees Celsius year-round.",
    "Most national parks charge eight euros per car for daily entry.",
    "Yoga studios offer drop-in classes for 22 euros without a subscription.",
    "Cycling jerseys typically weigh between 110 and 140 grams.",
    "Pottery kilns reach firing temperatures of 1200 degrees Celsius.",
    "Photography classes at the community college run for ten weekly sessions.",
    "Outdoor swimming pools close for the season in mid-September.",
    "Hiking the mountain ridge takes between six and eight hours round trip.",
    "Bird migration patterns peak in the region during April and September.",
    "Beekeeping associations charge thirty euros for an annual membership.",
    "Local museums waive admission fees on the first Sunday of each month.",
    "Tide times along the coast shift by roughly 50 minutes each day.",
    "Long-distance trains run hourly between the city and the coast.",
    "Sourdough bread loaves bake for 35 to 45 minutes at 230 degrees Celsius.",
    "Mountain bike trails in the forest reserve open from April to October.",
    "Open mic nights at the jazz club start at 21:00 every Wednesday.",
    "Community gardens allocate plots of 25 square metres per member.",
    "Public ice rinks open from November through March in the city.",
    "Stand-up comedy clubs offer half-price tickets on Tuesday evenings.",
    "Outdoor cinemas screen films on Friday evenings from June to August.",
    "Wild blueberry harvest in the forest peaks in late July.",
    "Local cycling clubs gather every Saturday at 08:30 at the main bridge.",
    "Mountain hut reservations open six months in advance for peak season.",
    "Apple harvests in the regional orchards run from late September into October.",
    "Cross-country ski trails are groomed twice weekly from December onward.",
]
_TEAM_METRICS = [
    "The team ships between four and six features per two-week sprint.",
    "Sprint retrospectives run for sixty minutes with six participants.",
    "Velocity has held at thirty-five story points for eight consecutive weeks.",
    "Pull request turnaround averages eighteen hours from open to merge.",
    "Production incidents per month dropped from six to two over the last quarter.",
    "Code review participation reaches ninety-two percent across the engineering team.",
    "On-call rotation cycles every twelve weeks across the platform group.",
    "Mean time to recovery for critical incidents sits at forty-five minutes.",
    "The team archives 23 percent of created tickets without implementation.",
    "Quarterly OKR completion averages seventy-eight percent across the engineering org.",
    "Engineering headcount has grown from twelve to twenty-one people in eighteen months.",
    "Documentation coverage for public APIs holds at eighty-four percent.",
    "Test coverage for the core engine sits steady at eighty-seven percent.",
    "Average daily PR count across the team reaches twenty-four merges.",
    "Cross-team PR reviews account for thirty-five percent of total reviews.",
    "Roadmap planning ceremonies repeat once per quarter for two days.",
    "Team velocity in story points correlates with on-call burden negatively.",
    "Open source dependency upgrades happen on a monthly cadence.",
    "Engineering managers spend thirty percent of their time in 1:1 meetings.",
    "Customer feedback synthesis ships a monthly report on the third Tuesday.",
    "Hiring loops include four technical interviews and one architecture round.",
    "Team retention over the last twelve months sits at ninety-one percent.",
    "Onboarding to first PR averages four working days for new engineers.",
    "Quarterly hackathons produce on average eight prototype demos.",
    "Knowledge-sharing sessions run weekly for forty-five minutes.",
    "Architecture decision records average twelve per quarter across the platform.",
    "Postmortem documents average two thousand words and require manager review.",
    "Support escalations from on-call to engineering reach roughly five per week.",
    "Customer Net Promoter Score for the platform holds at forty-two.",
    "Engineering blog publishes one article per month authored by a rotating engineer.",
    "Performance reviews run biannually in March and September.",
    "Engineering wide all-hands meets every other Thursday at 16:00.",
    "Internal tooling team responds to support tickets within two business days.",
    "Cross-functional projects involve product, design, and engineering by default.",
    "Sprint backlog refinement runs every Monday for ninety minutes.",
    "Pair programming sessions account for fifteen percent of engineering time.",
    "Production deploys happen Tuesday through Thursday only, never on Mondays.",
    "Annual engineering offsites run for three days each October.",
    "Customer interviews precede every major feature spec by two to four weeks.",
    "Engineering interview panels include one engineer from outside the hiring team.",
]


def build_seed_memories(cluster_size: int = 5, style: str = "experience") -> list[SeedMemory]:
    """Generate cluster-friendly seeds, scaled by ``cluster_size``.

    3 clusters × ``cluster_size`` memories. Cluster A and B share the same
    overall structure (1:1 coffee) but vary by time + mood — that's the
    R3 hyper-schema bait. Cluster C is a different topic so it doesn't
    bleed into the others. Per-memory content is *distinct enough* that
    retain-side dedup (embedding similarity threshold) doesn't merge them.

    Pools are sized for up to 40 memories per cluster; values beyond that
    cycle modulo with deterministic indexing.

    ``style="experience"`` (default) emits episodic content tagged
    ``experience`` (concept §5.3 promote threshold 0.35).
    ``style="fact"`` emits declarative world-fact content tagged ``fact``
    (concept §5.3 promote threshold 0.7 — much stricter).
    """
    if style == "fact":
        return _build_fact_seeds(cluster_size)

    out: list[SeedMemory] = []

    def _pick(pool, idx):
        return pool[idx % len(pool)]

    # ── Cluster A: Coffee 1:1 morning, productive ─────────────────────────
    a_topics = [
        (_pick(_MORNING_PEOPLE, i), *_pick(_MORNING_TOPICS, i))
        for i in range(cluster_size)
    ]
    for person, topic, outcome in a_topics:
        out.append(
            SeedMemory(
                cluster="coffee_morning",
                # Heavy shared boilerplate so the cluster centroid dominates
                # the embedding; the personalized tail stays short. Without
                # this the per-memory topic ("authentication flow" vs
                # "migration") drives pairwise cosine below the 0.75
                # cohesion gate and HDBSCAN drops the cluster.
                content=(
                    "Morning coffee one-on-one at the office espresso bar. "
                    "Productive sprint-plan focused session, aligned on "
                    "priorities for the upcoming sprint and agreed on "
                    "the next steps. Quick 30-minute morning coffee sync, "
                    "structured agenda, productive working coffee meeting. "
                    f"Talked with {person} about {topic} and {outcome}."
                ),
                tags=[
                    "experience",  # → C1 promote threshold = 0.4 (vs default 0.7)
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
        (_pick(_AFTERNOON_PEOPLE, i), _pick(_AFTERNOON_TOPICS, i))
        for i in range(cluster_size)
    ]
    for person, topic in b_topics:
        out.append(
            SeedMemory(
                cluster="coffee_afternoon",
                # Heavy shared boilerplate (see cluster A note above) — the
                # cluster signature dominates the embedding, the personal
                # tail just differentiates enough to avoid retain-side
                # dedup.
                content=(
                    "Afternoon coffee break in the office kitchen, casual "
                    "no-agenda personal catch-up. Relaxed friendly chat to "
                    "decompress between meetings, away from work topics. "
                    "Quick 30-minute afternoon coffee, easy-going personal "
                    "chitchat, friendly informal coffee break. "
                    f"Hung out with {person} about {topic}."
                ),
                tags=[
                    "experience",  # → C1 promote threshold = 0.4
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
    # Week numbers start at 12 to keep parity with the original 5-seed
    # smoke (which produced the first sprint_retro schema in earlier runs).
    c_items = [(str(12 + i), *_pick(_RETRO_THEMES, i)) for i in range(cluster_size)]
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
                    "experience",  # → C1 promote threshold = 0.4
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


def _build_fact_seeds(cluster_size: int) -> list[SeedMemory]:
    """World-fact variant of :func:`build_seed_memories`.

    Three clusters of declarative present-tense statements. Tag ``fact``
    gives a Promote-Threshold of 0.7 (concept §5.3) — significantly
    stricter than ``experience`` 0.35, so C1 promotion is the empirical
    question this variant is designed to probe.
    """
    out: list[SeedMemory] = []

    def _pick(pool, idx):
        return pool[idx % len(pool)]

    # ── Cluster A: tech world-facts ───────────────────────────────────────
    for i in range(cluster_size):
        statement = _pick(_TECH_FACTS, i)
        out.append(
            SeedMemory(
                cluster="tech_facts",
                content=(
                    "Engineering reference notes for the platform stack. "
                    "Operational metric captured for runbook documentation. "
                    f"Recorded fact: {statement}"
                ),
                tags=[
                    "fact",  # → C1 promote threshold = 0.7 (strict)
                    "cluster:tech_facts",
                    "domain:engineering",
                    "shape:declarative",
                ],
                recall_query=f"tech fact {i}: {statement[:40]}",
            )
        )

    # ── Cluster B: lifestyle world-facts ──────────────────────────────────
    for i in range(cluster_size):
        statement = _pick(_LIFESTYLE_FACTS, i)
        out.append(
            SeedMemory(
                cluster="lifestyle_facts",
                content=(
                    "Local lifestyle reference notes for the neighbourhood guide. "
                    "Documentation point captured for future reference. "
                    f"Recorded fact: {statement}"
                ),
                tags=[
                    "fact",
                    "cluster:lifestyle_facts",
                    "domain:lifestyle",
                    "shape:declarative",
                ],
                recall_query=f"lifestyle fact {i}: {statement[:40]}",
            )
        )

    # ── Cluster C: team metrics world-facts ───────────────────────────────
    for i in range(cluster_size):
        statement = _pick(_TEAM_METRICS, i)
        out.append(
            SeedMemory(
                cluster="team_metrics",
                content=(
                    "Engineering team reporting metrics for the quarterly review. "
                    "Operational metric captured for organisational documentation. "
                    f"Recorded fact: {statement}"
                ),
                tags=[
                    "fact",
                    "cluster:team_metrics",
                    "domain:engineering_ops",
                    "shape:declarative",
                ],
                recall_query=f"team metric {i}: {statement[:40]}",
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
    # Resolve schema ids from Qdrant BEFORE deleting Qdrant points — the
    # Neo4j :Schema node has no bank_id property so we have to match by id.
    schema_ids = list_qdrant_schema_ids(bank_id)
    qd = reset_qdrant(bank_id)
    neo = reset_neo4j(bank_id, schema_ids=schema_ids)
    print(f"  Postgres: {pg_total} rows deleted")
    print(f"  Qdrant:   {qd} points deleted")
    print(
        f"  Neo4j:    {neo.get('engrams', 0)} engrams, "
        f"{neo.get('schemas', 0)} schemas, "
        f"{neo.get('hyper_schemas', 0)} hyper-schemas deleted "
        f"(resolved {len(schema_ids)} schema id(s) via Qdrant)"
    )
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

        # RetainResponse only exposes coarse `success` + `items_count`
        # — per-item dedup/filter status is not surfaced by the API.
        # Treat success as "accepted" and rely on the /graph snapshot
        # later in the pipeline to detect any silent drops.
        if resp.get("success") and resp.get("items_count", 0) > 0:
            status = "accepted"
            persisted_by_cluster[s.cluster] = persisted_by_cluster.get(s.cluster, 0) + 1
        else:
            status = "rejected"
            duplicates.append(f"{s.cluster}#{idx} → rejected (items_count=0)")
        print(f"  [{idx:2d}/{len(seeds)}] {s.cluster:<20s} {time.time() - sub:5.1f}s  status={status}")
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


def phase_session_boundary(base: str, bank_id: str, label: str) -> PhaseReport:
    """Bump ``bank.session_count`` so subsequent C1 sees ``sessions_alive > 0``.

    Epic 26 Story 08 — production agents trigger this implicitly through
    ``end_session``. The smoke never opens SessionManager sessions, so we
    explicitly bump the counter between phases. Without this every engram
    keeps ``sessions_alive=0`` and the decay formula short-circuits to 1.0
    (= no amplification = facts can never clear the 0.7 promote threshold).
    """
    print("=" * 72)
    print(f"STEP: SESSION BOUNDARY — {label}")
    print("=" * 72)
    t0 = time.time()
    try:
        resp = _post_json(
            f"{base}/v1/default/banks/{bank_id}/sessions/advance",
            {},
            timeout=10.0,
        )
        new_count = resp.get("session_count")
        print(f"  bank.session_count → {new_count}")
    except Exception as exc:
        print(f"  session-advance failed: {exc}")
        new_count = None
    print()
    return PhaseReport(
        name=f"session-advance-{label}",
        duration_s=time.time() - t0,
        payload={"session_count": new_count, "label": label},
    )


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
        print(f"  C3 R5: schemas_scanned={r5.get('schemas_scanned', 0)} archived={len(r5.get('archived_ids') or [])}")

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
                print(
                    f"  strength: weak={sd.get('weak', 0)} moderate={sd.get('moderate', 0)} strong={sd.get('strong', 0)}"
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


def evaluate_summary(reports: list[PhaseReport], *, cluster_size: int = 5) -> int:
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

    # Promotion target scales with cluster size — 60% of the seeds reaching
    # buffer means each of 3 clusters has roughly cluster_size×0.6 engrams,
    # well above MIN_CLUSTER_SIZE=3 so HDBSCAN/Agglomerative can work.
    promote_target = max(9, int(3 * cluster_size * 0.6))
    # Each of the 3 themed clusters should yield one schema on a healthy run.
    schemas_target = 3 if cluster_size >= 5 else max(2, cluster_size // 2)

    checks: list[tuple[str, bool, str]] = [
        (
            f"C1 promoted ≥ {promote_target} engrams to buffer",
            (c1.get("consolidated", 0) >= promote_target),
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
            f"C2 minted ≥ {schemas_target} schemas",
            (c2_second.get("created", 0) >= schemas_target),
            f"created={c2_second.get('created', 0)}",
        ),
        (
            f"Cortex shows ≥ {schemas_target} schemas after C2",
            (schemas_after_c2 >= schemas_target),
            f"cortex_schemas={schemas_after_c2}",
        ),
        (
            f"Final cortex schemas == {schemas_target}",
            (final_schemas == schemas_target),
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


def run(
    bank_id: str,
    base_url: str,
    *,
    skip_reset: bool,
    skip_c3: bool,
    cluster_size: int = 5,
    style: str = "experience",
    recalls_per_memory: int | None = None,
) -> int:
    base = base_url.rstrip("/")
    seeds = build_seed_memories(cluster_size=cluster_size, style=style)
    if recalls_per_memory is not None:
        # Override the per-seed recall budget. Useful for probing how many
        # recalls a fact-tagged engram needs to clear the 0.7 promote
        # threshold (concept §5.4 strength × decay path).
        for s in seeds:
            s.recall_count = recalls_per_memory
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
    # Session boundary AFTER seed so created_at_session=0 engrams age by 1
    # before recalls start (concept §5.4 decay needs sessions_alive > 0).
    reports.append(phase_session_boundary(base, bank_id, "after-seed"))
    reports.append(phase_recall_loop(base, bank_id, seeds))
    # Second boundary so the recall-driven access_count gets one more session
    # to compare against in the decay denominator.
    reports.append(phase_session_boundary(base, bank_id, "after-recall"))

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

    return evaluate_summary(reports, cluster_size=cluster_size)


def main() -> int:
    parser = argparse.ArgumentParser(description="CLS pipeline end-to-end smoke test")
    parser.add_argument("--bank-id", default=DEFAULT_BANK)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--skip-reset", action="store_true")
    parser.add_argument("--skip-c3", action="store_true", help="skip the R3+R5 phase")
    parser.add_argument(
        "--scale",
        type=int,
        default=15,
        help=(
            "Total number of seed memories (split across 3 clusters). "
            "Default 15 (=5/cluster). Use 30, 60, 100 for larger runs — "
            "each memory takes ~60-80s on local Ollama, so 100 ≈ ~2h."
        ),
    )
    parser.add_argument(
        "--style",
        choices=["experience", "fact"],
        default="experience",
        help=(
            "Seed content style. 'experience' (default) uses episodic content "
            "tagged with 'experience' (promote threshold 0.35, concept §5.3). "
            "'fact' uses declarative world-fact content tagged with 'fact' "
            "(promote threshold 0.7 — empirical probe of strict-tag behavior)."
        ),
    )
    parser.add_argument(
        "--recalls-per-memory",
        type=int,
        default=None,
        help=(
            "Override the per-seed recall budget (default 6 = SeedMemory."
            "recall_count). Higher values probe the concept §5.4 strength × "
            "decay path — repeated recalls bump engram strength which feeds "
            "back into composite_score, eventually clearing even the 0.7 "
            "fact-tag promote threshold."
        ),
    )
    args = parser.parse_args()
    cluster_size = max(3, args.scale // 3)
    return run(
        args.bank_id,
        args.api_url,
        skip_reset=args.skip_reset,
        skip_c3=args.skip_c3,
        cluster_size=cluster_size,
        style=args.style,
        recalls_per_memory=args.recalls_per_memory,
    )


if __name__ == "__main__":
    sys.exit(main())
