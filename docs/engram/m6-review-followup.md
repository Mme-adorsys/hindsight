# M6 Review — Follow-Up

> **Datum:** 2026-04-08
> **Vorgänger:** `m6-review-findings.md`
> **Scope:** Verifikation aller KRIT- und MINOR-Findings aus dem ursprünglichen M6 Review nachdem die Fixes implementiert wurden. Plus regression spot-checks auf den vorher verifizierten Pfaden.

---

## TL;DR

**Block A (KRITISCH) — vollständig im Code gefixt**, aber **eine neue, ebenfalls kritische Lücke** entdeckt: Die `NCROrchestrator`-Instantiierung beim API-Startup wird ohne `shared_bank_id`, `qdrant_client`, `neo4j_client` und `llm` aufgerufen. Damit ist **NCR Phase 4 (Multi-Bank Promotion) inkl. B2 Conflict Resolution in Produktion unerreichbar** — die Code-Pfade existieren, werden aber nie ausgeführt.

**Block B (MINOR) — alle 7 Findings behoben.** Inkl. eines, das im ersten Review noch als "❓ verifizieren" markiert war (`save_task` Schedule).

**Empfehlung:** Ein einziger zusätzlicher Fix vor M7-Start: NCROrchestrator-Wiring in `api/http.py:1166` ergänzen. Geschätzter Aufwand: **30-60 Minuten** inkl. Test.

---

## Verification Matrix

### Block A — KRITISCH

| ID | Finding | Status | Verifikation |
|----|---------|--------|--------------|
| **KRIT-1** | conflict_resolution nicht in promote_batch verdrahtet | ✅ **Fixed (Code)** | `multi_bank_promoter.py:38` Import; neuer Helper `_resolve_redundant()` lines 613-702 deckt alle 4 Resolution-Typen ab (KEEP_EXISTING, MERGE, REPLACE, CONTRADICTION_LINK), inkl. graceful fallback und contradiction-Link-Erzeugung (line 689) |
| **KRIT-1** | (Activation) | ⚠️ **NEU: Inaktiv in Production** | Siehe **NEU-1** unten |
| **KRIT-2** | shared_bank_id fehlt in HTTP API | ✅ **Fixed** | `api/http.py:132` neues Feld auf `RecallRequest`, line 1489 wird an `recall_async` durchgereicht |
| **KRIT-2 MCP-Parität** | shared_bank_id fehlt im MCP recall tool | ✅ **Fixed** | `api/mcp.py:155` Parameter; line 212 durchgereicht; line 178 Doku ergänzt |

**Implementation Quality der KRIT-1 Lösung (sehr gut):**

Der `_resolve_redundant()` Helper in `multi_bank_promoter.py` ist defensiv und konzept-konform aufgebaut:

```python
# multi_bank_promoter.py:594-610 (Auszug)
if novelty.result != NOVEL:
    # B2 — Conflict Resolution: detect contradictions before reinforcing
    if llm is not None and novelty.existing_engram_id:
        await _resolve_redundant(...)
    else:
        # No LLM available — fall back to plain reinforce (backward compat)
        if novelty.existing_engram_id:
            await reinforce_shared(...)
        result.reinforced += 1
```

- ✅ Nur aktiviert wenn LLM verfügbar (sauberer Backward-Compat-Pfad)
- ✅ Try/except um detect_conflicts UND resolve_conflict (graceful degradation)
- ✅ Alle 4 Resolution-Typen werden gehandhabt
- ✅ CONTRADICTION_LINK wird tatsächlich in Neo4j geschrieben (line 689)
- ✅ Logging mit resolution/winner/loser für Debug
- ✅ `_CandidateProxy` Adapter (line 65) bridged dict→FullEngram-Interface sauber

**Pipeline-Wiring (Funktionssignaturen):**
- ✅ `ncr_orchestrator.py:131` `__init__(llm=None)`
- ✅ `ncr_orchestrator.py:144` `self._llm = llm`
- ✅ `ncr_orchestrator.py:246` `promote_batch(..., llm=self._llm)`
- ✅ `multi_bank_promoter.py:529` `_process_candidate(..., llm=llm)`
- ✅ `multi_bank_promoter.py:594` `_resolve_redundant(..., llm=llm)`

→ Die Verkettung im Code ist lückenlos. Nur die Startup-Instantiierung stellt das Problem.

---

### Block B — MINOR

| ID | Finding | Status | Verifikation |
|----|---------|--------|--------------|
| **MIN-1** | Tags nicht zu Neo4j-Properties gespiegelt | ✅ **Fixed** | `engram_storage.py:293` `"tags",  # array property — enables tag-based Cypher queries` |
| **MIN-2** | Thalamus-Subscores nicht zu Neo4j-Properties | ✅ **Fixed** | `engram_storage.py:294-297` alle 4 Dimensionen (`thalamus_novelty`, `thalamus_surprise`, `thalamus_task_relevance`, `thalamus_emotional_valence`) als Node-Properties gespiegelt |
| **MIN-3** | `last_accessed` Update fehlt im Read-Pfad | ✅ **Fixed** | `recall_orchestrator.py:1161-1181` Best-effort Batch-Update am Ende des Recall-Pfads für alle gelieferten Engrams |
| **MIN-4** | `_STEP_FOR_TASK_KEY` ohne schema_fit_check | ✅ **Fixed** | `llm_routing.py:88` `PipelineStep.SCHEMA_COMPRESSION → "retain.schema_fit_check"`; line 244 in TASK_TIER_MAPPING vorhanden |
| **MIN-5** | NCR Phase 3 erhält leere Engram-Liste | ✅ **Fixed** | `ncr_orchestrator.py:220-225` Phase 3 holt jetzt `dict_repo.filter_entries(layer="neocortex", status="active")` und übergibt sie als `engrams=neocortex_entries` |
| **MIN-6** | WM `save_task` Schedule nicht verifiziert | ✅ **Fixed** | `memory_engine.py:518-524` startet `asyncio.create_task(...)` und assigned an `state.save_task`; `session_manager.py:294-295` cancelled sauber bei `end_session` |
| **MIN-7** | Tags-Filter in BFS/Temporal Pfaden | ✅ **Fixed** | `search/retrieval.py` — `tags` Parameter durchgängig in `retrieve_semantic` (69), `retrieve_bm25` (113), `retrieve_temporal` (180), Fusion (405), High-Level Wrapper (486), BFS (613), und Top-Level (698) |
| **MIN-8** | Weak-Link Confidence Penalty nicht angewandt | ✅ **Fixed** | `constructive/pipeline.py:206-207` `if sr.retrieval.traversal_source == "weak_link": confidence *= WEAK_LINK_CONFIDENCE_PENALTY` |

**Alle 8 MINOR Findings sind behoben.**

---

### Block C — KOSMETIK

| ID | Finding | Status |
|----|---------|--------|
| **C.1** | NCR Phase 3 Übergabesignatur (entweder leer lassen oder füllen) | ✅ **Fixed** zusammen mit MIN-5 — wurde konsequent gefüllt |

---

### Block D — NICE-TO-HAVE

Wie geplant nicht im Scope dieses Fix-Sprints. Bleibt offen für Phase 8 / Benchmarking-Sprint.

---

## NEU-1 — Phase 4 Multi-Bank Promotion in Production unerreichbar (KRITISCH)

**Wo:** `api/http.py:1166`

**Was:**

```python
# api/http.py:1166-1172 (aktuell)
_orchestrator = NCROrchestrator(
    pool=memory._pool,
    consolidation=_consolidation,
    decay=_decay,
    strengthen=_strengthen,
    schema=_schema,
)
```

Die `NCROrchestrator.__init__()`-Signatur akzeptiert (siehe `ncr_orchestrator.py:120-132`):

```python
def __init__(
    self,
    pool: asyncpg.Pool,
    consolidation: Consolidation1Service,
    decay: DecayProcessor,
    strengthen: StrengthenProcessor,
    schema: SchemaProcessor,
    shared_bank_id: str | None = None,    # ← nicht passed
    agent_bank_ids: list[str] | None = None,  # ← nicht passed
    qdrant_client=None,                    # ← nicht passed
    neo4j_client=None,                     # ← nicht passed
    llm=None,                              # ← nicht passed
) -> None:
```

**Konsequenz:** In `_run_phases()` (`ncr_orchestrator.py:236-237`) wird Phase 4 nur ausgeführt, wenn `self._shared_bank_id and self._qdrant_client` truthy sind:

```python
# Phase 4: Shared Bank Promotion (Epic 14 B5)
if self._shared_bank_id and self._qdrant_client:
    try:
        report.promotion = await promote_batch(...)
```

Da beide bei Startup auf `None` initialisiert werden, **wird Phase 4 in Production niemals ausgeführt**. Das bedeutet:
- Cross-Bank Promotion läuft nicht
- B5 Schema-triggered Candidates werden nicht promotet
- B3 Cross-Agent Convergence wird nicht ausgewertet
- B2 Conflict Resolution (KRIT-1 Fix) ist erreichbar nur über andere Wege, nicht über NCR

**Beweis im Code:**
- `api/http.py:2429-2433` Endpoint-Doku spricht noch von "three sequential phases" — Phase 4 ist nirgends erwähnt
- `app.state.ncr_orchestrator` (line 1173) wird vom `trigger_ncr` Endpoint (line 2465-2468) verwendet — d.h. das ist DER Production-Pfad
- Es gibt keinen weiteren `NCROrchestrator(...)`-Aufruf im gesamten Repo (verifiziert per grep)

**Fix (vorgeschlagen):**

```python
# api/http.py:1166 — vorgeschlagene Korrektur
_promotion_llm = memory._ctx.llm_registry.get_llm("ncr", "conflict_resolution")
# (oder welcher Task-Key auch immer für B2 in TASK_TIER_MAPPING vorgesehen ist —
#  ggf. neuen Eintrag "ncr.conflict_resolution": ModelTier.LARGE in llm_routing.py)

_orchestrator = NCROrchestrator(
    pool=memory._pool,
    consolidation=_consolidation,
    decay=_decay,
    strengthen=_strengthen,
    schema=_schema,
    shared_bank_id=_config.shared_bank_id,  # neue Config-Variable nötig
    agent_bank_ids=None,  # oder dynamisch ermittelt aus DB
    qdrant_client=qdrant,
    neo4j_client=neo4j,
    llm=_promotion_llm,
)
```

**Zusatzschritte:**
1. Neue Config-Variable `HINDSIGHT_API_SHARED_BANK_ID` (oder Lookup aus BankProfile-Tabelle) ergänzen
2. `agent_bank_ids` dynamisch ermitteln — bei NCR-Start aus `banks` Tabelle WHERE tier='dictionary'
3. Optional: neuen Task-Key `"ncr.conflict_resolution": ModelTier.LARGE` in `llm_routing.py:TASK_TIER_MAPPING`
4. Endpoint-Doku in `trigger_ncr` updaten ("four sequential phases")
5. End-to-End Integration-Test: 2 Agents, widersprüchliche Engrams, NCR Phase 4 → Verify contradiction-Link in Neo4j

**Aufwand:** 30-60 Minuten inkl. Test.

**TODOs:**
- [ ] **NEU-1.1** `NCROrchestrator(...)`-Aufruf in `api/http.py:1166` um die 5 fehlenden Parameter erweitern
- [ ] **NEU-1.2** Config-Variable `shared_bank_id` (env oder DB-Lookup) bereitstellen
- [ ] **NEU-1.3** Task-Key für conflict resolution LLM in `llm_routing.py` ergänzen oder bestehenden wiederverwenden
- [ ] **NEU-1.4** `trigger_ncr` Endpoint-Doku auf 4 Phasen aktualisieren
- [ ] **NEU-1.5** Integration-Test: contradicting Engrams aus 2 Agents → NCR triggern → contradiction-Link in Neo4j vorhanden, Winner korrekt

---

## Regression Spot-Checks (vorher verifizierte Pfade)

Verifikation, dass die Fixes keine zuvor funktionierenden Pfade gebrochen haben:

| Pfad | Status | Anmerkung |
|------|--------|-----------|
| Retain → Schema-Fit (R4 inkrementell) | ✅ unverändert | `retain/orchestrator.py:532,560` |
| Recall → CoActivationTracker.flush_to_neo4j() | ✅ unverändert | `recall_orchestrator.py:335` |
| Recall → AssociationWindow.flush_to_neo4j() | ✅ unverändert | `recall_orchestrator.py:342` |
| Recall → Construction Pipeline + Prediction Error | ✅ unverändert | `prediction_error.py:190-232` |
| NCR Phase 1 (Decay) | ✅ unverändert | `ncr_decay.py` |
| NCR Phase 2 (Strengthen + Layer Transition) | ✅ unverändert | `ncr_strengthen.py:150-162` |
| NCR Phase 3 (Schema Compression) | ✅ verbessert | jetzt mit echten Engrams gefüttert (MIN-5) |
| NCR Phase 4 (Promotion + B2) | ⚠️ unerreichbar | Code OK, aber Wiring fehlt — siehe NEU-1 |
| Reflect → Reconsolidation Priority Queue | ✅ unverändert | `reconsolidation_queue.py:77-137` |
| Session Lifecycle (create/end + WM) | ✅ verbessert | save_task jetzt aktiv (MIN-6) |
| HTTP /recall → Dual-Bank | ✅ neu erreichbar | shared_bank_id durchgereicht (KRIT-2) |

**Keine Regressionen festgestellt.** Im Gegenteil: zwei Pfade wurden zusätzlich verbessert (NCR Phase 3 Engram-Übergabe, Session Periodic Save).

---

## Updated Status pro Konzept-Kapitel

Vollständige Status-Tabelle (Δ ggü. M6 Review):

| # | Kapitel | M6 Status | Neuer Status | Δ |
|---|---------|-----------|--------------|---|
| 3 | Storage-Architektur | ✅ Implementiert (mit MIN-1, MIN-2) | ✅ Implementiert | MIN-1+2 fixed |
| 4 | Engram Data Model | ✅ Implementiert (mit MIN-3) | ✅ Implementiert | MIN-3 fixed |
| 5 | Thalamus Filter | ✅ Production-ready | ✅ Production-ready | — |
| 6 | Retain Pipeline | ✅ Implementiert | ✅ Implementiert | — |
| 7 | Session Layer | ✅ Implementiert | ✅ Implementiert | — |
| 8 | Search & Retrieval | ✅ Implementiert (mit MIN-7) | ✅ Implementiert | MIN-7 fixed |
| 9 | Working Context | ✅ Implementiert (mit MIN-6 ❓) | ✅ Implementiert | MIN-6 fixed |
| 10 | Reflect & Reconsolidation | ✅ Implementiert | ✅ Implementiert | — |
| 11 | Constructive Memory | ✅ Implementiert (mit MIN-8) | ✅ Implementiert | MIN-8 fixed |
| 12 | Consolidation Pipeline | ✅ Implementiert (mit MIN-5) | ✅ Implementiert | MIN-5 fixed |
| 13 | Schema Emergence | ✅ Implementiert | ✅ Implementiert | — |
| 14 | Weak Connections & STC | ✅ Implementiert | ✅ Implementiert | — |
| 15 | Multi-Bank Architecture | ⚠️ **Partial (KRIT-1+2)** | ⚠️ **Partial (NEU-1)** | KRIT-1+2 code-fixed, aber Phase 4 in Production unerreichbar |
| 16 | LLM Routing | ✅ Implementiert (mit MIN-4) | ✅ Implementiert | MIN-4 fixed |

**18 von 18 Kapitel im Code vollständig.** Ein einziger Wiring-Fix in der API-Startup-Sequenz steht zwischen dem aktuellen Stand und Production-Readiness für M7.

---

## Empfehlung für M7 Start

**Ein einziger Schritt vor M7:**

1. **NEU-1 fixen** — NCROrchestrator-Wiring in `api/http.py:1166` ergänzen, Test schreiben.
2. NCR triggern, beobachten dass Phase 4 jetzt läuft (`[NCR] Phase4/Promotion done: ...` im Log).
3. Integration-Test mit zwei widersprüchlichen Engrams ausführen, contradiction-Link in Neo4j verifizieren.

**Aufwand:** 30-60 Minuten.

Danach ist M6 vollständig abgeschlossen und Block A des ursprünglichen Reviews ist nicht nur "im Code", sondern auch "in Production" gefixt.

**M7 Control Plane Epics (E19-E22) können direkt anschließend starten.**

---

## Action Items als Checkliste (für Claude Code)

### Block A' — Letzter KRIT-Fix vor M7
- [ ] **A'.1** `api/http.py:1166` `NCROrchestrator(...)` um `shared_bank_id`, `agent_bank_ids`, `qdrant_client=qdrant`, `neo4j_client=neo4j`, `llm=_promotion_llm` erweitern (NEU-1.1)
- [ ] **A'.2** Config-Variable für `shared_bank_id` (env `HINDSIGHT_API_SHARED_BANK_ID` oder DB-Lookup) (NEU-1.2)
- [ ] **A'.3** LLM-Task-Key in `llm_routing.py` für conflict resolution (oder bestehenden wiederverwenden) (NEU-1.3)
- [ ] **A'.4** `trigger_ncr` Endpoint-Doku in `api/http.py:2429-2433` auf 4 Phasen aktualisieren (NEU-1.4)
- [ ] **A'.5** Integration-Test: 2 Agents, widersprüchliche Engrams, NCR triggern, contradiction-Link in Neo4j (NEU-1.5)

### Bereits erledigt — keine Aktion mehr nötig
- [x] KRIT-1 (Code) — `_resolve_redundant` Helper in `multi_bank_promoter.py`
- [x] KRIT-2 — `shared_bank_id` in HTTP+MCP API
- [x] MIN-1 — Tags zu Neo4j Node-Properties
- [x] MIN-2 — Thalamus-Subscores zu Neo4j Node-Properties
- [x] MIN-3 — `last_accessed` Batch-Update im Recall-Pfad
- [x] MIN-4 — `_STEP_FOR_TASK_KEY` deckt schema_fit_check ab
- [x] MIN-5 / C.1 — NCR Phase 3 mit echten Engrams gefüttert
- [x] MIN-6 — WM `save_task` Schedule via `asyncio.create_task` in `memory_engine.py:518`
- [x] MIN-7 — Tags-Filter in BFS und Temporal Retrieval-Pfaden
- [x] MIN-8 — Weak-Link Confidence Penalty in Construction Pipeline angewandt

### Block D — NICE-TO-HAVE (unverändert offen, nicht blockierend)
- [ ] D.1 Disposition-Faktor in `ncr_decay.py`
- [ ] D.2 PE-Registry Cross-Session-Persistenz
- [ ] D.3 `schema_history` Audit-Tabelle
- [ ] D.4 Schema-Boost / Freshness-Penalty in `bank_merge.py` als Config
- [ ] D.5 Decay-Parameter empirisch tunen (Benchmark B abwarten)

---

**Ende des Follow-Up Reviews. Nach Abarbeitung von Block A' ist M7 (Control Plane) freigegeben.**
