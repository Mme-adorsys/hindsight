# Story 05 — Multi-Bank Integration Tests

## User Story

Als Entwickler brauche ich Integration Tests die den kompletten Multi-Bank Lifecycle validieren: Agent-Isolation, Promotion, Conflict Resolution, Cross-Bank Query.

## Akzeptanzkriterien

- [ ] 2+ Agents mit jeweils eigenen Banks
- [ ] Engram-Lifecycle: Retain → Consolidation → Promotion → Shared
- [ ] Cross-Agent Isolation: Agent A kann Agent B's Dictionary nicht lesen
- [ ] Conflict Resolution: Merge + Contradiction bei Shared Write
- [ ] Cross-Bank Query: Agent findet eigene + Shared Ergebnisse

## Tasks

- [ ] **T1 — Multi-Agent Fixture:** 2 Agents (Agent-A, Agent-B) mit je Session + Dictionary Bank. 1 Shared Bank. 10 Engrams pro Agent mit teilweiser thematischer Überlappung.
- [ ] **T2 — Isolation Test:** Agent-A `recall_async()` → findet nur eigene + Shared Engrams, nicht Agent-B's. Und umgekehrt.
- [ ] **T3 — Promotion Lifecycle Test:** Agent-A Engram durchläuft: Retain → Buffer → Neocortex (nach NCR-Zyklen) → Shared Bank (nach Promotion). Prüfe: Engram in Shared Bank mit `source: agent_a`.
- [ ] **T4 — Conflict Resolution Test:** Agent-A und Agent-B promoten ähnliche Engrams. Erwartet: Merge oder Contradiction-Link. Shared Bank hat korrekte Daten.
- [ ] **T5 — Cross-Agent Convergence Test:** Agent-A und Agent-B haben unabhängig ähnliche Engrams → Convergence-Trigger → Beschleunigte Promotion.
- [ ] **T6 — Benchmark B Seed:** Simulated Agent Life: 1 Agent operiert über 10 simulierte Tage mit Retain + Recall + NCR. Tracking: Engram-Anzahl pro Layer über Zeit, Schema-Entstehung, Shared Bank Population.
