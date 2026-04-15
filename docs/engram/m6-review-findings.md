# M6 Review — Concept vs Code

> **Datum:** 2026-04-08
> **Scope:** Vollständiges Review aller 18 Concept-Kapitel gegen die aktuelle Implementierung in `hindsight-api/hindsight_api/` nach Abschluss von Phase 6 (Epics 01-18).
> **Ziel:** Vor Beginn der Control Plane Epics (E19-E22) sicherstellen, dass der Code dem Konzept und dem neuro-biologischen Modell entspricht.
> **Methode:** 6 parallele Code-Reviews (Foundation, Ingestion, Session/Context, Retrieval, Long-term, Multi-Bank) + manueller System-Level-Integrationscheck.

---

## TL;DR

**Phase 6 ist substantiell vollständig.** 17 von 18 Kapiteln sind im Code implementiert und bilden das Konzept inkl. neuro-biologischer Vokabeln korrekt ab. Die End-to-End-Pipelines (Retain → Thalamus → Storage → Recall → Constructive Memory → Reconsolidation → NCR → Schema) sind verdrahtet.

**Es gibt 2 echte Lücken (KRITISCH):**

1. **`conflict_resolution` Modul ist nicht in `multi_bank_promoter.promote_batch()` integriert.** Bei B2 (Write Conflict Resolution beim Shared-Bank-Promote) wird zwar `check_novelty()` aufgerufen, aber bei REDUNDANT-Treffern wird nur `reinforce_shared()` ausgeführt — `detect_conflicts()` / `resolve_conflict()` werden nie aufgerufen. Widersprüche zwischen Agents bleiben dadurch im Shared Bank unsichtbar.

2. **`shared_bank_id` ist nicht über die HTTP API erreichbar.** Dual-Bank-Recall (B6) funktioniert intern, aber `RecallRequest` in `api/http.py` gibt den Parameter nicht weiter. Nur MCP/Interne Aufrufe können den Cross-Bank Query nutzen.

Beide Punkte sind klein und gut isoliert — schätzungsweise je 1-2 Stunden Arbeit. **Beides sollte vor M7 (Control Plane) gefixt sein**, weil das Control Plane sonst Features visualisieren würde, die im Backend nicht funktionieren.

Daneben gibt es eine Handvoll **MINOR**-Findings (Mirroring von Tags/Thalamus-Werten zu Neo4j-Properties, NCR Phase 3 Übergabe, periodic save_task) und einige Verbesserungs-Empfehlungen.

**False Alarms aus Sub-Agent-Reviews (im System-Check verifiziert, alles korrekt):**
- ✅ R4 `schema_links` IST in `retain/orchestrator.py:532,560` aufgerufen
- ✅ `co_activation_tracker.flush_to_neo4j()` IST in `recall_orchestrator.py:335` aufgerufen
- ✅ `association_window.flush_to_neo4j()` IST in `recall_orchestrator.py:342` aufgerufen

---

## Status pro Kapitel

| # | Kapitel | Status | Files | Kritische TODOs |
|---|---------|--------|-------|----------------|
| 3 | Storage-Architektur | ✅ Implementiert | `engram_storage.py`, `engram_dictionary.py`, `qdrant_client.py`, `neo4j_client.py` | Tags + Thalamus-Subscores zu Neo4j mirrorn (MINOR) |
| 4 | Engram Data Model | ✅ Implementiert | `response_models.py`, `engram_types.py`, alembic migrations | `last_accessed` Update bei `read_engram` (MINOR) |
| 5 | Thalamus Filter | ✅ Implementiert | `thalamus.py`, `retain_orchestrator.py` | — (production-ready) |
| 6 | Retain Pipeline (R0-R5) | ✅ Implementiert | `retain/orchestrator.py` + alle R0-R5 Module | Schema-Strength Persistenz nach Retain-Fit prüfen |
| 7 | Session Layer | ✅ Implementiert | `session/session_manager.py`, `session/mode_config.py` | — |
| 8 | Search & Retrieval | ✅ Implementiert | `recall_orchestrator.py`, `search/*` | Tags-Filter in BFS/Temporal-Pfade prüfen |
| 9 | Working Context (+Epic 18) | ✅ Implementiert | `session/working_context.py`, `session/working_memory.py`, `session/session_cache.py` | Periodic save_task Verdrahtung verifizieren (MINOR) |
| 10 | Reflect & Reconsolidation | ✅ Implementiert | `reflect_orchestrator.py`, `reflect/*` | Disposition-Effekt empirisch validieren |
| 11 | Constructive Memory | ✅ Implementiert | `constructive/pipeline.py`, `constructive/prediction_error.py`, `constructive/models.py` | Weak-Link-Confidence-Penalty (0.85×) wirklich angewandt? |
| 12 | Consolidation Pipeline (NCR) | ✅ Implementiert | `consolidation/ncr_orchestrator.py`, `consolidation/consolidation1.py`, `ncr_decay.py`, `ncr_strengthen.py` | Phase 3 erhält leere Engram-Liste (kosmetisch) |
| 13 | Schema Emergence (5 GoL Regeln) | ✅ Implementiert | `consolidation/schema_clustering.py`, `schema_maturation.py`, `schema_competition.py`, `retain/schema_links.py` | R4 inkrementell ✅ verifiziert |
| 14 | Weak Connections & STC | ✅ Implementiert | `session/co_activation_tracker.py`, `session/association_window.py`, `retain/link_creation.py` | Flush-Aufrufe ✅ verifiziert |
| 15 | Multi-Bank Architecture | ⚠️ **Partial** | `consolidation/multi_bank_promoter.py`, `consolidation/conflict_resolution.py`, `search/bank_merge.py`, `retain/bank_utils.py` | **B2 + B6 — siehe KRITISCH** |
| 16 | LLM Routing (3 Tiers) | ✅ Implementiert | `engine/llm_routing.py`, `engine/bank_model_config.py` | `_STEP_FOR_TASK_KEY` deckt schema_fit_check nicht ab |
| 17 | Benchmarking | ⏳ Geplant für E23 | — | — (Phase 8) |
| 1, 2, 18 | Kontext / Überblick / Referenzen | n/a | — | — |

---

## KRITISCHE Findings

### KRIT-1 — Conflict Resolution nicht in Multi-Bank Promotion verdrahtet

**Concept-Referenz:** Kapitel 15, B2 (Write Conflict Resolution).

> Wenn zwei Engrams zum Shared Bank konsolidiert werden und Semantic Similarity ≥ 0.85:
> - Kein inhaltlicher Widerspruch → **Merge**: stärkeres Engram wird Basis, schwächeres liefert Kontext
> - Inhaltlicher Widerspruch → **Höherer Score gewinnt**, schwächeres Engram wird über **contradiction-Link** verbunden
> - Bei gleichem Score → **Neueres Engram gewinnt** (Recency)

**Code-Realität:**

`engine/consolidation/conflict_resolution.py` enthält die vollständige B2-Logik:
- `detect_conflicts()` (lines 79-144)
- `resolve_conflict()` (lines 258-355)
- `merge_engrams()` (lines 179-210)
- `create_contradiction_link()` (lines 218-250)

**Aber:** `engine/consolidation/multi_bank_promoter.py` importiert `conflict_resolution` nicht und ruft keine seiner Funktionen auf (verifiziert via grep — 0 Treffer). Stattdessen:
- `check_novelty()` → NOVEL → `promote_to_shared()`
- `check_novelty()` → REDUNDANT → `reinforce_shared()` (nur Strength erhöhen, kein Konflikt-Check)

**Konsequenz:** Wenn Agent A das Engram "DB liegt in EU" hat und Agent B "DB liegt in US", werden beide unter dem Schwellenwert 0.85 als REDUNDANT eingestuft, das ältere wird einfach reinforced — der Widerspruch wird nicht erkannt, kein contradiction-Link angelegt, keine Information über Disagreement bewahrt. Das ist genau das Szenario, das B2 verhindern soll.

**Fix (vorgeschlagen):**
```python
# In multi_bank_promoter.promote_batch():
if novelty == NOVELTY.REDUNDANT:
    conflicts = await conflict_resolution.detect_conflicts(
        new_engram, existing_engram
    )
    if conflicts:
        await conflict_resolution.resolve_conflict(
            new_engram, existing_engram, conflicts
        )
    else:
        await reinforce_shared(existing_engram, new_engram)
```

**TODOs:**
- [ ] **KRIT-1.1** Import + Aufruf von `conflict_resolution.detect_conflicts()` / `resolve_conflict()` in `multi_bank_promoter.promote_batch()` ergänzen
- [ ] **KRIT-1.2** Integration-Test schreiben: Zwei Agents schreiben widersprüchliche Engrams → Promotion → contradiction-Link in Neo4j vorhanden, korrekter Winner gewählt
- [ ] **KRIT-1.3** B2-Threshold (0.85) und Score-Tiebreak (Recency) als ENV/Config exposen für Tuning

---

### KRIT-2 — `shared_bank_id` nicht in HTTP API exponiert

**Concept-Referenz:** Kapitel 15, B6 (Cross-Bank Query) + B4 (Shared-to-Agent Feedback Loop).

> Default Dual-Query: Jeder `recall_async` geht parallel an Agent Session Bank (PostgreSQL) + Shared Memory Bank (Qdrant + Neo4j).

**Code-Realität:**

`engine/recall_orchestrator.py` line 115 akzeptiert `shared_bank_id: str | None = None` korrekt und führt bei Vorhandensein dual-bank query mit `asyncio.gather()` durch (lines 532-560). `bank_merge.merge_parallel_results()` (lines 150-219) macht das Mode-abhängige Weighting korrekt.

**Aber:** `api/http.py` `RecallRequest` Pydantic-Model hat kein `shared_bank_id` Feld, und der Endpoint übergibt es auch nicht (verifiziert via grep — 0 Treffer im http.py-File). Damit ist der Parameter nur über MCP / interne Aufrufe / Tests zugänglich.

**Konsequenz:** Phase-7 Control Plane Views, die "Cross-Bank Query" zeigen sollen (Epic 19, Memory Detail Panel mit `source: shared` Marker), würden Daten aus einem Pfad anzeigen, den die HTTP API nicht ansteuern kann.

**Fix:** RecallRequest um `shared_bank_id: str | None = None` erweitern, Endpoint durchreichen, optional Default in BankProfile setzen ("wenn diese Bank ein default_shared_bank konfiguriert hat, automatisch verwenden").

**TODOs:**
- [ ] **KRIT-2.1** `shared_bank_id` Feld zu `RecallRequest` in `api/http.py` hinzufügen und an `recall_async()` durchreichen
- [ ] **KRIT-2.2** Optionales `default_shared_bank` Feld in BankProfile (für automatisches Routing ohne Caller-Eingriff)
- [ ] **KRIT-2.3** MCP-Parität: Falls MCP-Tool `recall` den Parameter ebenfalls nicht hat, ergänzen
- [ ] **KRIT-2.4** End-to-End Test: HTTP `/recall` mit `shared_bank_id` → Result mit `source: agent` UND `source: shared` Markern

---

## MINOR Findings

### MIN-1 — Tags nicht zu Neo4j-Node-Properties gespiegelt
**Kapitel:** 3 (Storage). **Datei:** `engine/engram_storage.py:280`

Neo4j-Node bekommt nur `{strength, layer, abstraction_level, status, thalamus_overall}`. Tags sind nur in Qdrant-Payload-Index gespeichert. Damit sind Tags-basierte Cypher-Queries (z.B. "alle Engrams mit Tag X im 2-hop Umfeld") nicht möglich.

**TODO:** Tags als Array-Property zu Neo4j-Node ergänzen, optional ein Index drauf.

---

### MIN-2 — Einzelne Thalamus-Dimensionen nicht in Neo4j
**Kapitel:** 3 (Storage). **Datei:** `engine/engram_storage.py:280`

`novelty`, `surprise`, `task_relevance`, `emotional_valence` sind nur in PostgreSQL Dictionary. Für Cypher-Queries wie "alle Engrams mit hohem Surprise im Schema-Cluster" müsste man join-en. Einfacher: als Properties spiegeln.

**TODO:** Bei `update_metadata()` und `create_engram()` die 4 Subscores ebenfalls auf den Neo4j-Node spiegeln.

---

### MIN-3 — `last_accessed` Update bei Read
**Kapitel:** 4 (Engram Data Model). **Datei:** `engine/engram_dictionary.py:373-385`

`update_access()` existiert, wird aber nicht automatisch bei jedem `read_engram` aufgerufen. Damit driftet `last_accessed` von der Realität ab und beeinflusst die NCR-Decay-Berechnung falsch.

**TODO:** `read_engram()` (oder besser: der Recall-Pfad nach finaler Result-Selektion) sollte die gelesenen Engram-IDs in einer Batch-`update_access()` aktualisieren.

---

### MIN-4 — `_STEP_FOR_TASK_KEY` deckt nicht alle Pipeline-Steps ab
**Kapitel:** 16 (LLM Routing). **Datei:** `engine/llm_routing.py:92-96`

Reverse-Mapping `PIPELINE_STEP_TASK_KEY` → 7 Steps, aber `retain.schema_fit_check` fehlt im Mapping. Damit fällt das resolve auf TASK_TIER_MAPPING zurück (funktioniert), aber Budget-Profil-Override greift dort nicht.

**TODO:** `_STEP_FOR_TASK_KEY` um schema_fit_check ergänzen oder ein PipelineStep-Enum-Element hinzufügen.

---

### MIN-5 — NCR Phase 3 erhält leere Engram-Liste
**Kapitel:** 12 (Consolidation). **Datei:** `engine/consolidation/ncr_orchestrator.py:223`

Phase 3 wird mit `engrams=[]` aufgerufen. Der `SchemaProcessor` queriet Neo4j eh selbst, also funktional kein Problem — aber die Übergabesignatur ist irreführend.

**TODO:** Entweder Liste rausnehmen oder konsequent füllen.

---

### MIN-6 — Periodic `save_task` für Working Memory
**Kapitel:** 9 (Working Context, Epic 18). **Datei:** `engine/session/session_manager.py:121`

`SessionState.save_task: asyncio.Task | None` Feld existiert. In `create_session()` wird es nicht offensichtlich angelegt. End-of-session flush (`end_session()` line 273) ist verdrahtet, aber periodische Saves während laufender Session möglicherweise nicht. Gefahr: bei Crash → letzte ~Session verloren.

**TODO:** Verifizieren ob save_task in create_session geschedult wird (`asyncio.create_task(self._periodic_save(...))`). Falls nicht: ergänzen, Default-Intervall 60s, abbrechen in `end_session`.

---

### MIN-7 — Tags-Filter in BFS/Temporal Retrieval
**Kapitel:** 8 (Search & Retrieval). **Datei:** `engine/search/retrieval.py`

Sub-Agent hat verifiziert dass Semantic + BM25 das Tags-Filter durchreichen. BFS und Temporal-Pfade sind nicht explizit geprüft worden.

**TODO:** In `retrieval.py` BFS und Temporal-Methoden auf `tags`-Parameter prüfen, ergänzen falls fehlt.

---

### MIN-8 — Weak-Link Confidence Penalty in Construction
**Kapitel:** 11 (Constructive Memory). **Datei:** `engine/constructive/pipeline.py:44`

`WEAK_LINK_CONFIDENCE_PENALTY = 0.85` ist definiert, aber im `_extract_facts()` nicht eindeutig angewandt. Wenn ein Fact aus einem co_activated/temporal_proximity Link kommt, sollte seine Confidence niedriger sein.

**TODO:** Pipeline-Code prüfen: Wird `traversal_source='weak_link'` propagiert und führt es zu Confidence × 0.85?

---

## Verbesserungs-Empfehlungen (NICE-TO-HAVE, nicht blockierend für M7)

### NICE-1 — Disposition in NCR Decay
RF4-Logik (Disposition beeinflusst Reconsolidation) gilt aktuell nur im Reflect-Pfad. Konzeptionell könnte ein "konservativer" Agent Engrams langsamer decayen lassen (mehr Bewahrung), ein "analytischer" Agent schneller. Erweiterung von `ncr_decay.py` um Disposition-Faktor.

### NICE-2 — PE-Registry Carry-Forward
Aktuell ist `prediction_error_registry` per-Session. Ein Engram, das in Session 1 einen PE verursacht hat, ist in Session 2 nicht mehr priorisiert für Reconsolidation. Bio-realistischer wäre Cross-Session-Persistenz mit Decay.

### NICE-3 — Schema Strength Auditing
R4 inkrementiert Schema.strength bei Retain. R5 decayed Schemas. Dazwischen ist kein Audit-Trail. Empfehlung: einfache Tabelle `schema_history(schema_id, ts, delta, source)` für Debug + spätere Schema-Analytics.

### NICE-4 — Decay-Parameter Forgetting-Curve
Der Decay-Faktor `0.95^(days/30)` in `ncr_decay.py` ist eine grobe Abschätzung der Ebbinghaus-Forgetting-Curve. Mit Benchmark B (Simulated Agent Life, Epic 23) sollten die Parameter empirisch fein-getunt werden.

### NICE-5 — Schema-Boost / Freshness-Penalty Tuning
`bank_merge.py:99,102` hat hardcoded `+0.2` Schema-Boost und `×0.95` Freshness-Penalty. Beides sollten ENV/Config-Variablen sein, damit man im Benchmark tunen kann.

---

## End-to-End Flow Verification

Manuell verifiziert über grep + targeted Reads:

| Pfad | Status | Files |
|------|--------|-------|
| `retain` → Thalamus Gate → Storage (3 DBs) | ✅ | `thalamus.py:209` Gate, `engram_storage.py:143` Write, sequenzielle Compensation |
| `retain` → Schema-Fit-Check (R4 inkrementell) | ✅ | `retain/orchestrator.py:532,560` (`check_schema_fit_batch`, `write_schema_links`) |
| `retain` → Co-Activation/Temporal Proximity Links | ✅ | `retain/link_creation.py` MERGE-Statements |
| `recall` → Mode-aware MPFP / EngramRetriever | ✅ | `engram_retrieval.py:70-100` Mode-Resolve |
| `recall` → Dual-Bank Query (intern) | ✅ | `recall_orchestrator.py:532-560` `asyncio.gather` |
| `recall` → Co-Activation Flush nach Result | ✅ | `recall_orchestrator.py:335,342` flush_to_neo4j |
| `recall` → Construction Pipeline | ✅ | `recall_orchestrator.py:344-398` |
| `recall` → Prediction Error Detection → Mode Shift + PE Registry | ✅ | `prediction_error.py:190-232` |
| `reflect` → Reconsolidation (Priority Queue) | ✅ | `reconsolidation_queue.py:77-137` |
| `reflect` → Semantic Trigger (≥0.6) | ✅ | `semantic_trigger.py:39` |
| `NCR` → Consolidation 1 (WM → Buffer) | ✅ | `consolidation1.py:155,175-187` |
| `NCR` → Phase 1 Decay → Phase 2 Strengthen → Phase 3 Schema | ✅ | `ncr_orchestrator.py:186-232` |
| `NCR` → Phase 2 Layer Transition (buffer → neocortex) | ✅ | `ncr_strengthen.py:150-162` |
| `NCR` → Phase 4 Multi-Bank Promotion | ⚠️ | `ncr_orchestrator.py:234-253` ruft `promote_batch`, ABER ohne Conflict Resolution (KRIT-1) |
| `recall` → HTTP API | ⚠️ | `shared_bank_id` fehlt im Request-Schema (KRIT-2) |
| `session` → WM Persistenz beim End | ✅ | `session_manager.py:273` `end_session` |
| `session` → WM Periodic Save | ❓ | Field existiert, Schedule nicht verifiziert (MIN-6) |

---

## Bio-Fidelity Assessment

| Bio-Konzept | Code-Mapping | Status |
|------------|-------------|--------|
| Hippocampus | Pre-Engram Buffer + Engram Dictionary (`layer='buffer'`) | ✅ |
| Neocortex | Schema Store + Meta-Engrams (`layer='neocortex'`) | ✅ |
| Thalamus (4D Filter) | `engine/thalamus.py` mit 4 Dimensionen, deterministisch, embedding-basiert | ✅ |
| CA1 Mismatch | Novelty = `1.0 - max_similarity` (`thalamus.py:229+`) | ✅ |
| Noradrenalin (Surprise) | Surprise = expectation↔outcome cosine + Plastizitäts-Multiplikator | ✅ |
| PFC Top-Down | Task-Relevance + Session.task_context + Working Context Goal Stack | ✅ |
| Amygdala (Emotion) | Emotional Valence × `VALENCE_AMPLIFICATION (1.5x)` durch PE | ✅ |
| Dopamin (Pos. PE) | `apply_prediction_error_feedback()` mit Strength-Bonus bei pos. PE | ✅ |
| LTP Early | Pre-Engram Buffer Entry, fragil, dedupable | ✅ |
| LTP Late | Konsolidiertes Engram (nach NCR Phase 2) | ✅ |
| STC (Frey & Morris 1997) | `association_window.py` 5min Fenster, Focus+Supporting only | ✅ |
| SWS / Sharp-Wave Ripples | NCR Phase 1+2 (Decay + Strengthen) | ✅ |
| REM Sleep | NCR Phase 3 (Schema Compression / Game of Life R1+R2+R3+R5) | ✅ |
| Hebbian Learning | `co_activation_tracker.py` Counter → Threshold → Link-Bildung | ✅ |
| Pattern Separation (DG) | Score-aware Deduplication mit Strength-Bonus | ✅ |
| Pattern Completion (CA3) | Qdrant Semantic Trigger + Neo4j Traversal | ✅ |
| Forgetting Curve (Ebbinghaus) | NCR Decay Formel `0.95^(days/30)` | ✅ Approximativ |
| Reconsolidation Window (Nader) | Semantic Trigger ≥0.6 öffnet Lability | ✅ |

**Bewertung:** Bio-Fidelity ist durchgehend hoch. Terminologie wird konsistent in Docstrings und Variablennamen verwendet. Mapping ist überall dokumentiert (siehe `engram_types.py:32-50`, `multi_bank_promoter.py:16-20`, `session_manager.py:9-10`).

---

## Empfehlung für M7 Start

**Nicht starten** mit Control Plane Epics, bevor:

1. **KRIT-1** gefixt ist (Conflict Resolution Integration in promote_batch). Sonst zeigt das CP Schema/Memory Views, in denen Cross-Agent-Widersprüche unsichtbar bleiben.
2. **KRIT-2** gefixt ist (shared_bank_id in HTTP API). Sonst kann das CP Memory Detail Panel keine `source: shared` Daten anzeigen.

**Geschätzter Aufwand für beide:** 4-6 Stunden inkl. Tests. Klein genug, um vor M7 als kurzes "M6.5 Hardening Sprint" zu erledigen.

Danach (parallel oder sequenziell):
- MIN-1, MIN-2 (Tags + Thalamus zu Neo4j mirrorn) — empfehlenswert vor E19, weil das CP diese Felder anzeigt
- MIN-6 (save_task verifizieren) — empfehlenswert für Robustheit
- MIN-3 (`last_accessed` update) — empfehlenswert weil sonst NCR Decay falsche Daten bekommt

Die übrigen MINOR Findings können auch nach M7 kommen.

---

## Anhang: Datei-Inventar (Phase 6 Code)

```
engine/
├── engram_storage.py          (3-DB hybrid CRUD + compensation)
├── engram_dictionary.py       (PostgreSQL Dictionary CRUD)
├── engram_types.py            (BankTier, ThalamusScores, RetrievalMode)
├── thalamus.py                (4-dim Scoring, deterministisch)
├── llm_routing.py             (3 Tiers, Budget Profiles, L1-L3)
├── bank_model_config.py       (Per-Bank Profile + Cache)
├── memory_engine.py           (Facade)
├── retain_orchestrator.py     (Top-Level Retain Wrapper)
├── recall_orchestrator.py     (Top-Level Recall + Construction Wrapper)
├── reflect_orchestrator.py    (Reconsolidation Loop)
├── working_memory_repo.py     (WM JSONB Persistence)
│
├── retain/
│   ├── orchestrator.py        (R0-R5 Pipeline)
│   ├── sequence_analysis.py   (R0, Epic 15)
│   ├── fact_extraction.py     (Tag + Score Extraction)
│   ├── embedding_processing.py (Augmentierte Embeddings)
│   ├── deduplication.py       (Score-aware)
│   ├── entity_processing.py   (LLM Disambiguation)
│   ├── link_creation.py       (7 Link-Typen)
│   ├── schema_links.py        (R4 inkrementell)
│   ├── experience_links.py    (Action-Effect, Prediction-Error)
│   └── neo4j_link_writer.py
│
├── session/
│   ├── session_manager.py     (Dual Control, 4 Modi, Lifecycle)
│   ├── mode_config.py         (4 Mode-Profile)
│   ├── working_context.py     (3-Tier Active Engrams + Goals + Inferences)
│   ├── working_memory.py      (Epic 18 Persistence)
│   ├── session_cache.py       (Transient Cache)
│   ├── co_activation_tracker.py (Hebbian, recall-side)
│   └── association_window.py  (STC, retain-side)
│
├── search/
│   ├── retrieval.py           (Semantic / BM25 / Temporal / BFS)
│   ├── engram_retrieval.py    (Hybrid Qdrant + Neo4j)
│   ├── mpfp_retrieval.py      (Mode-aware Patterns)
│   ├── scoring.py             (6-dim Combined Score)
│   ├── reranking.py
│   ├── fusion.py              (RRF)
│   └── bank_merge.py          (Dual-Bank Result Fusion)
│
├── constructive/
│   ├── pipeline.py            (Construction)
│   ├── models.py              (ConstructedAnswer, Inference, Gap)
│   └── prediction_error.py    (Detection + Feedback)
│
├── reflect/
│   ├── reconsolidation_queue.py (Priority Queue, RF1)
│   ├── semantic_trigger.py    (RF3, ≥0.6)
│   ├── disposition_profile.py (RF4)
│   └── prediction_error_registry.py
│
└── consolidation/
    ├── ncr_orchestrator.py    (4-Phasen + Lock)
    ├── consolidation1.py      (WM → Buffer)
    ├── ncr_decay.py           (Phase 1, SWS)
    ├── ncr_strengthen.py      (Phase 2, Layer Transition)
    ├── schema_clustering.py   (R1)
    ├── schema_maturation.py   (R2 + R3)
    ├── schema_competition.py  (R5)
    ├── schema_processor.py    (NoOp Stub)
    ├── engram_schema_processor.py (Phase 3 Orchestrator)
    ├── multi_bank_promoter.py (B3 + B5, ⚠️ ohne B2)
    └── conflict_resolution.py (B2, ⚠️ nicht aufgerufen)
```

---

## Action Items als Checkliste (für Claude Code)

### Block A — KRITISCH (vor M7 Start)
- [ ] **A.1** `multi_bank_promoter.promote_batch()` um `conflict_resolution.detect_conflicts()` + `resolve_conflict()` ergänzen (KRIT-1.1)
- [ ] **A.2** Integration-Test schreiben: contradicting Engrams aus 2 Agents → resolve → contradiction-Link in Neo4j (KRIT-1.2)
- [ ] **A.3** `RecallRequest` (api/http.py) um `shared_bank_id: str | None` Feld erweitern und an `recall_async` durchreichen (KRIT-2.1)
- [ ] **A.4** MCP-Tool `recall` ebenfalls auf shared_bank_id prüfen, falls fehlt ergänzen (KRIT-2.3)
- [ ] **A.5** End-to-End Test: HTTP `/recall` mit shared_bank_id → Result mit `source: agent` und `source: shared` (KRIT-2.4)

### Block B — MINOR (vor oder parallel zu M7)
- [ ] **B.1** Neo4j-Node-Properties um `tags` (Array) erweitern, `update_metadata()` und `create_engram()` mirrorn lassen (MIN-1)
- [ ] **B.2** Neo4j-Node-Properties um 4 Thalamus-Subscores erweitern (MIN-2)
- [ ] **B.3** `last_accessed` Batch-Update am Ende des Recall-Pfades (für gelesene Engram-IDs) (MIN-3)
- [ ] **B.4** `_STEP_FOR_TASK_KEY` um schema_fit_check ergänzen (MIN-4)
- [ ] **B.5** WM `save_task` Schedule in `create_session_async` verifizieren bzw. ergänzen (MIN-6)
- [ ] **B.6** Tags-Filter in BFS- und Temporal-Retrieval-Pfaden prüfen (MIN-7)
- [ ] **B.7** Weak-Link Confidence Penalty (0.85×) im Construction-Pipeline-Pfad verifizieren (MIN-8)

### Block C — KOSMETIK (nicht blockierend)
- [ ] **C.1** NCR Phase 3 Übergabesignatur bereinigen (leere Liste oder konsequent füllen) (MIN-5)

### Block D — NICE-TO-HAVE (eigener kleiner Sprint vor Phase 8)
- [ ] **D.1** Disposition-Faktor in `ncr_decay.py`
- [ ] **D.2** PE-Registry Cross-Session-Persistenz mit Decay
- [ ] **D.3** `schema_history` Audit-Tabelle
- [ ] **D.4** Schema-Boost / Freshness-Penalty in `bank_merge.py` als Config (für Benchmark)
- [ ] **D.5** Decay-Parameter-Tuning gegen Forgetting-Curve (Benchmark B abwarten)

---

**Ende des Reviews. Nach Abarbeitung von Block A ist M7 (Control Plane) freigegeben.**
