# Story 17 — Recall Mode-abhängige Schema/Engram-Gewichtung

## User Story

Als Recall-Pipeline soll ich abhängig vom Session-Mode unterschiedlich zwischen Schema- und Engram-Treffern gewichten — Precision priorisiert Schemas (Allgemeinheit), Exploration priorisiert Engrams (Spezifik) — damit der Mode die Antwort-Charakteristik prägt.

## Kontext

Beide Treffertypen kommen aus derselben Vektor-Search (Story 15) mit Cosine-Score. Die Mode-Gewichtung modifiziert den finalen Recall-Score: Schema-Hits werden mit `w_schema(mode)`, Engram-Hits mit `w_engram(mode)` multipliziert. Im Mode `Precision` boostet das Schemas; in `Exploration` Engrams.

## Bestehende Codebasis

- **Session Mode Config:** `engine/session/mode_config.py` (aus Epic 06) mit Mode-spezifischen Gewichten.
- **Recall Scoring:** Bestehende Formel aus Kapitel 8 + Epic 07.
- **HybridRetriever:** liefert RetrievalHits mit kind und score (aus Story 15).

## Akzeptanzkriterien

- [x] In `mode_config.py` neue Felder `w_schema: float = 1.0`, `w_engram: float = 1.0` auf `ModeConfig` (frozen dataclass — additive, keine Breaking-Change für bestehende Konstruktoraufrufe).
- [x] Default-Werte verbatim aus Spec:
  - Precision: `w_schema=1.2`, `w_engram=0.9` (Schema-Allgemeinheit dominiert)
  - Exploration: `w_schema=0.8`, `w_engram=1.2` (Engram-Spezifik dominiert)
  - Analogy: `w_schema=1.1`, `w_engram=1.0` (leichte Schema-Neigung)
  - Validation: `w_schema=1.0`, `w_engram=1.0` (neutral)
- [x] `HybridRetriever.retrieve(..., mode=...)` multipliziert nach Enrichment den Score pro Hit-Kind und sortiert in-place absteigend (Stable-Sort — bei Ties bleibt Qdrant-Reihenfolge).
- [x] `mode=None`-Aufrufe verhalten sich identisch zur Story-15-Baseline (kein Re-Sort).
- [x] 5 neue Unit-Tests (drift-guard, Precision-Schema-Promotion, Exploration-Engram-Promotion, Validation-neutral, Re-Sort-Overtake); Integration-Test verschoben auf Block E (Story 19/20 E2E).

## Tasks

- [x] **T1 — Mode-Config-Erweiterung:** Felder `w_schema`/`w_engram` mit Default 1.0 hinzugefügt; per-Mode-Werte verbatim aus Spec; existing `with_overrides()` deckt Per-Bank-Overrides ab (additive Felder werden vom dataclasses.replace automatisch unterstützt).
- [x] **T2 — Re-Ranking im HybridRetriever:** `retrieve()` nimmt optional `mode: RetrievalMode | None`; static `_apply_mode_weighting` macht `score *= w_kind` und ein stable `list.sort(reverse=True)`. Unbekannte Modes fallen still auf 1.0/1.0 zurück und sortieren nur — keine harten Fehler.
- [x] **T3 — Konfigurierbarkeit:** Per-Bank-Overrides laufen über die bestehende `ModeConfig.with_overrides`-Schiene; SessionLayer reicht den Mode an die Retrieve-Schicht weiter, kein neuer Pfad nötig. Nach Block-E-Verdrahtung kann eine Bank `MODE_PROFILES[Mode].with_overrides(w_schema=...)` setzen.
- [x] **T4 — Unit-Tests:** 5 Tests in `tests/test_hybrid_retriever.py::TestModeWeighting` — drift-guard pinnt alle 4 Spec-Werte; Precision/Exploration zeigen Promotion bei gleichem Raw-Score; Validation hält Qdrant-Ordnung; Re-Sort beweist Overtake (Schema 0.85·1.2=1.02 > Engram 0.95·0.9=0.855).
