# Story 06 — C1/C2 Phasen-Migration

## User Story

Als System sollen alle Consolidation-Phasen (C1, C2a, C2b) den neuen Composite Score (`thalamus_overall × decay`) verwenden statt der alten Formeln, damit eine einheitliche Scoring-Metrik über den gesamten Lifecycle gilt.

## Kontext

Aktuell verwenden C1, C2a und C2b jeweils unterschiedliche Scoring-Ansätze:
- **C1 (Session → Buffer):** `strength = thalamus_scores.overall * 0.5 + base_strength`
- **C2a (Decay):** `strength × 0.9 × frequency_bonus`
- **C2b (Strengthen/Promote):** `compute_composite_strength(ev, surprise, access_count, cycles_alive)`

Nach der Migration nutzen alle drei denselben Composite: `thalamus_overall × decay`. Es gibt keine separate Decay-Formel mehr — der Composite wird bei jedem Cycle neu berechnet und ersetzt den gespeicherten `strength`-Wert.

Diese Story ist die Integration-Story: sie setzt voraus dass Stories 01-05 implementiert sind und verdrahtet die neuen Funktionen mit den bestehenden Consolidation-Phasen.

## Bestehende Codebasis

- **C1:** `engine/consolidation/consolidation1.py` → `Consolidation1Service.run()`. Setzt initiale Strength.
- **C2a Decay:** `engine/consolidation/ncr_decay.py` → `NcrDecayService.run()`. Berechnet Decay, setzt `status='archived'`.
- **C2b Strengthen:** `engine/consolidation/ncr_strengthen.py` → `NcrStrengthenService.run()`. Berechnet Composite, prüft Promote-Threshold und Hard Gates.
- **NCR Orchestrator:** `engine/ncr/ncr_orchestrator.py` → Ruft C2a und C2b sequentiell auf.
- **Neue Funktionen (aus Stories 01-05):** `compute_composite()`, `compute_equilibrium_rate()`, `compute_bank_factor()`, `get_promote_threshold(tags)`, `compute_min_access()`, `passes_hard_gates()`.

## Akzeptanzkriterien

- [ ] C1: Initiale Strength = `thalamus_scores.overall` (Geburtswert, decay=1.0 bei sessions_alive=0)
- [ ] C2a: Composite = `compute_composite(thalamus_overall, access_count, sessions_alive, r)` für jedes Engram. Composite < Archive-Threshold → archive. Reactivation für archived Engrams (Story 05).
- [ ] C2b: Composite > `get_promote_threshold(tags)` + `passes_hard_gates()` → promote. Buffer-Downgrade bei Composite < Threshold (Story 05).
- [ ] `r` wird bei jedem C2-Cycle frisch berechnet (nicht gecached) — bank_size kann sich ändern
- [ ] Neuer Strength-Wert nach jedem C2-Cycle = aktueller Composite (wird in Engram Dictionary persistiert)
- [ ] NCR Report enthält: Anzahl promoted, archived, reactivated, downgraded + Composite-Distribution
- [ ] Alte Scoring-Funktionen werden in keiner Consolidation-Phase mehr aufgerufen

## Tasks

- [ ] **T1 — C1 Migration:** `consolidation1.py` → `run()`: Initiale Strength = `thalamus_scores.overall` (kein `* 0.5 + base_strength` mehr). `created_at_session = bank.session_count`. Kein Composite-Berechnung nötig — bei sessions_alive=0 ist decay=1.0, also composite=thalamus_overall.
- [ ] **T2 — C2a Migration (Decay/Archive):** `ncr_decay.py` → `run()`: Für jedes aktive Engram: `r = compute_equilibrium_rate(thalamus_scores, mode, bank_size)`. `composite = compute_composite(thalamus_overall, access_count, sessions_alive, r)`. `engram.strength = composite` (persistieren). Composite < `ARCHIVE_THRESHOLD_WM` (Working Memory) oder `ARCHIVE_THRESHOLD_BUFFER` (Buffer) → archive. Reactivation-Check für archived Engrams (aus Story 05 T2).
- [ ] **T3 — C2b Migration (Strengthen/Promote):** `ncr_strengthen.py` → `run()`: Für jedes Working-Memory-Engram: Composite bereits in C2a berechnet (aus `strength` lesen). `passes_hard_gates(access_count, novelty, bank_size)` → nein? Skip. `composite ≥ get_promote_threshold(tags)` → promote to buffer. Für Buffer-Engrams: Buffer-Downgrade-Check (aus Story 05 T3). Buffer → Neocortex: `composite ≥ promote_threshold + ncr_cycles_survived ≥ 2`.
- [ ] **T4 — NCR Report erweitern:** `NcrReport` Dataclass erweitern: `reactivated_count: int`, `downgraded_count: int`, `composite_distribution: dict` (Histogram-Buckets: <0.1, 0.1-0.3, 0.3-0.5, 0.5-0.7, 0.7-1.0, >1.0). Logging in NCR Orchestrator nach jedem Phase-Run.
- [ ] **T5 — Alte Imports entfernen:** Alle Imports von `compute_recount_score`, `compute_composite_strength`, `SALIENCY_WEIGHT` in Consolidation-Modulen durch neue Funktionen ersetzen. Grep durch gesamte Codebase: keine aktiven Aufrufer der alten Funktionen mehr.
- [ ] **T6 — Integration Tests:** Vollständiger NCR-Cycle mit neuem Scoring: Engram mit hohem Thalamus + viel Access → promoted. Engram mit niedrigem Thalamus + kein Access → archived. Archived Engram mit Recall → reactivated im nächsten Cycle. Buffer Engram ohne Access → downgraded. Composite-Werte konsistent über C2a und C2b. Kein Aufruf alter Scoring-Funktionen in Logs.
