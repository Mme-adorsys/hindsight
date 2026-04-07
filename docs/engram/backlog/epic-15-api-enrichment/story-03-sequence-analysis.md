# Story 03 — R0 Sequence Analysis Pipeline Step

## User Story

Als System brauche ich einen vorgelagerten Pipeline-Schritt der reichhaltigen Content (Konversationen, Zusammenfassungen, Narrative) analysiert und in strukturierte Einheiten zerlegt, damit die nachfolgenden Pipeline-Schritte (R1-R5) mit klar definierten Inputs arbeiten können.

## Kontext

Der Caller soll ermutigt werden, reichhaltigen Content zu schicken — ganze Konversationen, Zusammenfassungen von Sessions, Narrative wie "Was hat er wann und warum gemacht und was war der Outcome." R0 extrahiert daraus:
- Atomare Fakten
- Action→Effect Ketten
- Implizite Expectations und Outcomes
- Sequentielle Zusammenhänge

Die Extraktionstiefe skaliert mit dem Budget. R0 wird übersprungen wenn der Caller bereits strukturierte Daten liefert (expectation/outcome Felder explizit gesetzt).

**Neuroscience-Basis:** Der Hippocampus speichert keine Konversationssequenzen. Er extrahiert Episoden aus dem Erlebnisstrom und verknüpft sie multidimensional. R0 bildet diesen Extraktionsprozess ab.

## Bestehende Codebasis

- **Retain Orchestrator:** `engine/retain/orchestrator.py` — R0 wird VOR der bestehenden Fact Extraction (R1) eingefügt.
- **LLM Routing:** `engine/llm_routing.py` — R0 nutzt budget-abhängige Tiers (Low=SMALL, Mid=MEDIUM, High=LARGE).
- **RetainContentDict:** `engine/retain/types.py` — R0 produziert erweiterte RetainContentDicts als Output.

## Akzeptanzkriterien

- [ ] R0 Modul `engine/retain/sequence_analysis.py` mit `analyze_sequence()` Hauptfunktion
- [ ] Budget-abhängige Extraktionstiefe: Low (Fakten only), Mid (+ Action→Effect + Exp/Outcome), High (+ Schema + implizite Zusammenhänge)
- [ ] R0 Skip-Logik: wenn Input-Item bereits expectation ODER outcome hat → R0 überspringen für dieses Item
- [ ] Output: Liste von StructuredUnit-Objekten (fact, action_effect, experience) die in RetainContentDicts konvertiert werden
- [ ] LLM-Prompt Templates für jede Budget-Stufe (Low: simpel, Mid: mittel, High: ausführlich)
- [ ] Orchestrator-Integration: R0 wird vor R1 aufgerufen, Output wird als Input für R1 weitergegeben
- [ ] Metriken: Anzahl extrahierter Units pro Input-Item, Budget-Stufe, LLM Token Usage

## Tasks

- [x] **T1 — StructuredUnit Dataclass:** Enum `UnitType` (FACT, ACTION_EFFECT, EXPERIENCE). Dataclass `StructuredUnit` mit: content, unit_type, context, expectation?, outcome?, related_unit_ids?, confidence. Wird von R0 produziert.
- [x] **T2 — R0 Low Budget:** LLM-Prompt (SMALL Tier): "Extrahiere atomare Fakten aus folgendem Text." Output: Liste von StructuredUnit(FACT). Kein Reasoning über Zusammenhänge.
- [x] **T3 — R0 Mid Budget:** LLM-Prompt (MEDIUM Tier): "Extrahiere Fakten, identifiziere Action→Effect Ketten, und erkenne wo Erwartungen und tatsächliche Outcomes genannt werden." Output: Mix aus FACT, ACTION_EFFECT, EXPERIENCE Units.
- [x] **T4 — R0 High Budget:** LLM-Prompt (LARGE Tier): Wie Mid, zusätzlich: "Identifiziere implizite Zusammenhänge, unausgesprochene Erwartungen, und verborgene Kausalitäten." Output: Wie Mid, mit höherer Abdeckung und Confidence.
- [x] **T5 — Skip-Logik:** In Orchestrator: wenn MemoryItem.expectation oder MemoryItem.outcome gesetzt → R0 überspringen, Item direkt als StructuredUnit(EXPERIENCE) oder StructuredUnit(FACT) weiterleiten.
- [x] **T6 — Orchestrator Integration:** R0 vor R1 einbauen. R0 Output (Liste von StructuredUnits) wird in RetainContentDicts konvertiert und an R1 übergeben. Bestehender Flow bleibt für Items ohne R0 unverändert.
- [x] **T7 — Tests:** Unit-Tests pro Budget-Stufe (mocked LLM). Skip-Logik Test. Orchestrator-Integration Test (R0 → R1 Flow). Token Usage Tracking.
