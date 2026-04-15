# Story 02 — Equilibrium Rate r

## User Story

Als System soll jedes Engram eine individuelle Equilibrium Rate `r` erhalten, die bestimmt wie viele Abrufe pro Session erwartet werden damit das Engram stabil bleibt. Die Rate soll sich aus dem Session-Mode, den Thalamus-Dimensionen und der Bankgröße berechnen.

## Kontext

Die Equilibrium Rate ist der Schlüssel zum individuellen Decay. Ohne `r` zerfallen alle Engrams gleich schnell — unabhängig davon ob sie task-relevant, überraschend oder emotional bedeutsam sind. Mit `r` bekommen verschiedene Engrams unterschiedliche Erwartungen:

- **Task-relevante Engrams** (hohe Task-Relevance) → höheres `r` → müssen sich durch Nutzung beweisen
- **Überraschende/neue Engrams** (hohe Novelty/Surprise/Valence) → niedrigeres `r` → bekommen mehr Zeit
- **Engrams in kleinen Banks** → höheres `r` → kompensiert inflationierte Abrufwahrscheinlichkeit
- **Engrams in großen Banks** → niedrigeres `r` → faire Chance trotz Wettbewerb

Formel: `r = r_base(mode) × demand / protection × bank_factor`

## Bestehende Codebasis

- **ThalamusScores:** `engine/engram_types.py` → `ThalamusScores` mit novelty, surprise, task_relevance, emotional_valence, overall.
- **Engram Dictionary:** `engram_dictionary` Tabelle → Hat `thalamus_scores` (JSON), `session_mode`.
- **Bank Model:** `models.py` → Bank mit Engram-Count verfügbar über `AdminOperations.get_bank_stats()`.
- **Scoring:** `engine/consolidation/scoring.py` → Hier wird die neue Funktion eingefügt.

## Akzeptanzkriterien

- [ ] Funktion `compute_equilibrium_rate(thalamus_scores, mode, bank_size)` implementiert
- [ ] `r_base` ist mode-abhängig: Precision=0.8, Validation=0.6, Analogy=0.4, Exploration=0.3
- [ ] `demand = 1 + α × task_relevance` (α default 0.5)
- [ ] `protection = 1 + β × (novelty + surprise + emotional_valence) / 3` (β default 0.5)
- [ ] `bank_factor = log(1 + reference_size) / log(1 + bank_size)` (reference_size default 1000)
- [ ] `r` ist immer > 0 (mathematisch garantiert durch Formelstruktur)
- [ ] Alle Parameter (α, β, reference_size, r_base pro Mode) sind als Konstanten konfigurierbar
- [ ] Ergebnis stimmt mit Berechnungsbeispielen in concept.md Kapitel 5 überein

## Tasks

- [ ] **T1 — r_base Konfiguration:** `MODE_R_BASE: dict[str, float]` in `engine/consolidation/scoring.py`. Keys: precision=0.8, validation=0.6, analogy=0.4, exploration=0.3. Fallback: 0.5 für unbekannte Modes.
- [ ] **T2 — Scoring-Parameter:** Konstanten `DEMAND_ALPHA = 0.5`, `PROTECTION_BETA = 0.5`, `REFERENCE_BANK_SIZE = 1000` in `scoring.py`. Optional: Env-Var Overrides (Pattern wie Thalamus-Thresholds in `thalamus.py`).
- [ ] **T3 — bank_factor Funktion:** `compute_bank_factor(bank_size: int, reference_size: int = REFERENCE_BANK_SIZE) → float`. Formel: `log(1 + reference_size) / log(1 + bank_size)`. Guard: bank_size < 1 → return 2.0 (maximale Kompensation). Wird auch in Story 04 (Hard Gates) wiederverwendet.
- [ ] **T4 — compute_equilibrium_rate Funktion:** Signatur: `compute_equilibrium_rate(thalamus_scores: ThalamusScores, mode: str | None, bank_size: int) → float`. Berechnet demand, protection, bank_factor und kombiniert zu `r_base × demand / protection × bank_factor`. Rückgabe immer > 0 (mathematisch garantiert, aber expliziter Guard `max(0.001, r)`).
- [ ] **T5 — Unit Tests:** r für task-relevanten Fakt (task_rel=0.8, rest niedrig) → r > r_base. r für überraschende Erkenntnis (novelty=0.9, surprise=0.8, valence=0.7) → r < r_base. r für Routinefakt (alles ~0.2) → r ≈ r_base. bank_factor bei bank_size=50 → ~1.76. bank_factor bei bank_size=50000 → ~0.64. Alle Berechnungsbeispiele aus concept.md validieren.
