# Epic 15 — API & Retain Enrichment

> Reichhaltiger Input, intelligente Extraktion. Der Caller liefert Kontext, das System strukturiert.

## Ziel

Erweiterung der Retain- und Recall-API um strukturierte Felder (Expectation, Outcome, Tags) sowie Unterstützung für reichhaltigen, unstrukturierten Input (Konversationen, Zusammenfassungen, Narrative). Die Retain Pipeline extrahiert budget-abhängig Fakten, Action→Effect-Ketten, Expectation→Outcome-Paare und erzeugt multidimensionale Graph-Links. Der Caller schickt lieber mehr als weniger — das System macht die Arbeit.

## Design-Entscheidungen

**Neuroscience-Basis:** Der Hippocampus bekommt einen Erlebnisstrom, nicht vorstrukturierte Daten. Er extrahiert Episoden, verknüpft sie multidimensional (semantisch, temporal, kausal, emotional) und speichert keine Konversationssequenzen. Eine Konversationssequenz wäre nur ein Zeitstrahl (Linked List) — der echte Graph entsteht durch Assoziationen in verschiedene Richtungen.

**Zwei Input-Modi:**
1. **Strukturiert:** Caller liefert Content + optionale Felder (Context, Expectation, Outcome, Tags). Pipeline-Step R0 wird übersprungen.
2. **Reichhaltig:** Caller liefert Konversation/Zusammenfassung/Narrative als Content. Pipeline-Step R0 (Sequence Analysis) extrahiert Struktur durch LLM-Reasoning.

**Budget steuert Extraktionstiefe:**
- **Low:** Fakten extrahieren, Basic-Links (temporal, entity)
- **Mid:** Fakten + Action→Effect Ketten + Expectation/Outcome Paare erkennen
- **High:** Alles von Mid + Entity Disambiguation + Schema-Links + tiefes Reasoning über implizite Zusammenhänge

**Expectation & Outcome:**
- Optional — nicht jeder Fakt hat eine Erwartung ("Wir treffen uns um 11 zum Padel" ist ein reiner Fakt)
- Dual-Purpose: (1) Score-Berechnung für Thalamus, (2) Experience-Engram Speicherung
- Können explizit vom Caller kommen ODER implizit von R0 aus dem Content extrahiert werden
- Expectation→Outcome wird als Experience-Engram gespeichert (episodisches Gedächtnis)

## Bestehende Codebasis (Hindsight)

**Relevante Dateien:**
- `hindsight-api/hindsight_api/api/http.py` — MemoryItem, RetainRequest, RecallRequest, RecallResponse. Aktuelle Felder: content, context, timestamp, metadata, document_id, entities.
- `hindsight-api/hindsight_api/engine/retain/orchestrator.py` — Retain Orchestrator. R0 wird VOR der bestehenden Fact Extraction eingefügt.
- `hindsight-api/hindsight_api/engine/retain/types.py` — ExtractedFact, ProcessedFact, RetainContentDict. Müssen um expectation, outcome, tags erweitert werden.
- `hindsight-api/hindsight_api/engine/memory_engine.py` → `retain_batch_async()` — Einstiegspunkt.
- `hindsight-api/hindsight_api/engine/retain/link_creation.py` — Neue Link-Types: CAUSAL (Action→Effect), PREDICTION_ERROR (Expectation→Outcome divergence).

## Scope

- Retain-API um Expectation, Outcome, Tags erweitern
- Recall-API um Expectation, Tags erweitern (query → question Rename)
- Neuer Pipeline-Step R0: Sequence Analysis (budget-abhängig)
- Experience-Engram Typ für Expectation→Outcome Paare
- Neue Neo4j Link-Types: CAUSAL, PREDICTION_ERROR
- Budget-abhängige Extraktionstiefe in der Retain Pipeline

## Nicht in Scope

- Thalamus Score Berechnung mit neuen Feldern (→ Epic 16)
- Model-Konfiguration pro Schritt (→ Epic 17)
- Working Memory Änderungen (→ Epic 18)

## Abhängigkeiten

- Epic 05 (Retain Pipeline) — R0 wird vor R1 eingefügt
- Epic 02 (Engram Data Model) — Engram Dictionary um expectation, outcome Felder erweitern
- Epic 01 (Neo4j) — Neue Relationship-Types

## Referenzen

- `concept.md` → Abschnitt 6 (Retain Pipeline) — wird um R0 erweitert
- `concept.md` → Abschnitt 5 (Thalamus Filter) — Input-Felder für Score-Berechnung

## Stories

1. [Retain API Erweiterung](story-01-retain-api.md)
2. [Recall API Erweiterung](story-02-recall-api.md)
3. [R0 Sequence Analysis Pipeline Step](story-03-sequence-analysis.md)
4. [Experience Engram & Link Types](story-04-experience-engram.md)
