# Story 01 — Task-to-Tier Mapping (L1)

## User Story

Als Architekt brauche ich ein vollständiges Mapping aller LLM-Tasks zu Model-Tiers, damit jeder Subtask das passende Modell nutzt — günstig wo es geht, stark wo es muss.

## Kontext

Hindsight nutzt aktuell ein Modell pro Operation (retain_llm, reflect_llm). Innerhalb einer Operation nutzen alle Subtasks dasselbe Modell — Fact Extraction (komplex) und Dedup-Check (simpel) laufen auf dem gleichen LLM. Das verschwendet Kosten oder opfert Qualität. Die Lösung: Jeder Subtask wird einem von 3 Tiers zugewiesen.

## Akzeptanzkriterien

- [x] Vollständige Liste aller LLM-Subtasks im System dokumentiert
- [x] Jeder Subtask einem Tier zugewiesen (Small, Medium, Large)
- [x] Mapping als Konfigurationsdatei oder Konstante im Code
- [x] Begründung für jede Tier-Zuweisung dokumentiert

## Tasks

- [x] **T1 — Alle LLM-Subtasks inventarisieren:** Durchsuche den Hindsight-Code nach allen Stellen die LLM aufrufen. Bekannte: `fact_extraction.py` (Fact Extraction), `deduplication.py` (Dedup-Check), `link_creation.py` (Causal Links), `entity_processing.py` (Entity Resolution), `think_utils.py` (Reflect/Think), `observation_regeneration.py` (Observation Synthesis), `think_utils.py:extract_opinions_from_text` (Opinion Extraction). Neue aus Engram-Architektur: Thalamus Scoring, Schema Emergence Checks, Constructive Memory Inference.
- [x] **T2 — Tier-Zuweisung definieren:** Small (einfache Pattern-Matching, ja/nein): Dedup-Check, Thalamus Scoring (initiale Einschätzung). Medium (strukturierte Extraktion, moderate Komplexität): Entity Resolution, Observation Synthesis, Opinion Extraction, Schema-Fit-Check. Large (komplexe Reasoning, kausale Zusammenhänge): Fact Extraction, Causal Link Extraction, Reflect/Think, Constructive Memory Inference, Conflict Resolution.
- [x] **T3 — Mapping als Konfiguration anlegen:** Neues Modul `hindsight_api/engine/llm_routing.py`. Dictionary `TASK_TIER_MAPPING: Dict[str, ModelTier]` mit allen Subtasks. Enum `ModelTier(Enum): SMALL, MEDIUM, LARGE`.
- [x] **T4 — Dokumentation:** In-Code Docstring mit Begründung pro Tier-Zuweisung. Markdown-Kommentar im Modul der die Philosophie erklärt (Kosten vs Qualität Trade-off).
