# Story 01 — Mode Configuration Profiles

## User Story

Als System brauche ich für jeden der 4 Modi ein vollständiges Konfigurationsprofil, damit alle nachgelagerten Systeme mode-abhängig parametrisiert werden können.

## Kontext

Jeder Mode (Precision, Exploration, Analogy, Validation) definiert ein Set von Parametern das sich durch die gesamte Pipeline zieht: Retain-Thresholds, Retrieval-Patterns, Traversal-Depth, Construction-Stil, Reconsolidation-Aggressivität. Diese Profile werden als statische Konfiguration definiert — die Werte kommen aus dem concept.md und können später per Config überschrieben werden.

## Bestehende Codebasis

- **RetrievalMode:** `hindsight_api/engine/retain/types.py` (aus Epic 02) — Enum mit PRECISION, EXPLORATION, ANALOGY, VALIDATION.
- **HindsightConfig:** `hindsight_api/config.py` — Bestehendes Config-Pattern mit Env-Vars und Defaults. Alle hardcoded Thresholds leben hier.
- **MPFPConfig:** In der Search-Pipeline verwendet für Pattern-Konfiguration.

## Akzeptanzkriterien

- [x] Für jeden Mode existiert ein `ModeConfig` Profil mit allen relevanten Parametern
- [x] Default-Werte entsprechen der concept.md Tabelle (Abschnitt 7)
- [x] ModeConfig ist immutable (frozen dataclass) — Änderungen erzeugen neue Instanzen
- [x] Profile sind per HindsightConfig überladbar (Env-Vars für Custom-Thresholds)
- [x] Ein `get_mode_config(mode: RetrievalMode) → ModeConfig` liefert das Profil

## Tasks

- [x] **T1 — ModeConfig Dataclass:** Neues Modul `hindsight_api/engine/session/mode_config.py`. Frozen Dataclass `ModeConfig` mit Feldern: `strength_pre_filter: float` (Minimum Engram Strength), `thalamus_boost_dimension: str | None` (welche Thalamus-Dimension geboostet wird), `weak_link_policy: Literal['ignore', 'follow', 'prefer']`, `traversal_depth: Literal['shallow', 'medium', 'deep']`, `construction_style: Literal['conservative', 'creative', 'cross_domain', 'evidence_based']`, `reconsolidation_level: Literal['minimal', 'moderate', 'schema_update', 'aggressive']`, `scoring_weights: ScoringWeights` (CE, RRF, Temporal, Recency, Strength, Thalamus).
- [x] **T2 — ScoringWeights Dataclass:** Im selben Modul: `ScoringWeights` mit den 6 Gewichten aus der Scoring-Formel (concept.md Abschnitt 8). Defaults pro Mode: Precision → hohe CE + Relevance-Boost, Exploration → niedrige Thresholds + Novelty-Boost, Analogy → Schema-Links, Validation → Surprise + Causal.
- [x] **T3 — Default Profile Registry:** Dict `MODE_PROFILES: dict[RetrievalMode, ModeConfig]` mit den 4 Default-Profilen. Werte direkt aus concept.md Tabelle: Precision (strength≥0.5, task_relevance boost, ignore weak, shallow), Exploration (strength≥0.1, novelty boost, follow weak, deep), Analogy (strength≥0.3, no boost, prefer weak, medium), Validation (strength≥0.3, surprise boost, ignore weak, medium).
- [x] **T4 — Config Override:** In `config.py`: Optionale Env-Vars für Mode-Parameter (z.B. `ENGRAM_PRECISION_STRENGTH_THRESHOLD=0.6`). Pattern: `ModeConfig.with_overrides(**overrides) → ModeConfig` gibt neue Instanz mit überschriebenen Werten zurück.
- [x] **T5 — get_mode_config():** Funktion die Mode → ModeConfig resolved. Prüft erst HindsightConfig auf Overrides, fällt dann auf Defaults zurück.
- [x] **T6 — Unit Tests:** Alle 4 Default-Profile validieren (Werte aus concept.md). Override-Mechanismus testen. Immutability testen (frozen). get_mode_config mit und ohne Overrides.
