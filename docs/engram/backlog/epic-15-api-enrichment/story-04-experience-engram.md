# Story 04 — Experience Engram & Link Types

## User Story

Als System will ich Expectation→Outcome Paare als Experience-Engrams speichern und mit passenden Graph-Links verknüpfen, damit das episodische Gedächtnis Vorhersagefehler tracken und daraus lernen kann.

## Kontext

Ein Experience-Engram speichert: "In Situation X habe ich Y erwartet, aber Z ist passiert." Das ist die Grundlage für:
- Prediction Error Detection bei Recall (Kapitel 10: Reconsolidation)
- Erwartungsanpassung über Zeit (Schema Evolution, Kapitel 13)
- Thalamus Surprise Score Berechnung (Epic 16)

Experience-Engrams werden über zwei neue Neo4j Link-Types verknüpft:
- **CAUSAL:** Action→Effect Ketten (A hat B verursacht)
- **PREDICTION_ERROR:** Expectation→Outcome Divergenz (Erwartung weicht von Realität ab)

## Bestehende Codebasis

- **Neo4j Client:** `engine/neo4j_client.py` — VALID_RELATIONSHIP_TYPES mit aktuell 8 Typen.
- **Link Creation:** `engine/retain/link_creation.py` — temporal_proximity, co_activation Links.
- **Engram Dictionary:** `engine/engram_dictionary.py` — insert_entry, update_entry.
- **Engram Storage:** `engine/engram_storage.py` — create() für 3-DB Write.

## Akzeptanzkriterien

- [ ] Experience-Engram als eigener Layer/Tag gespeichert (tag: "experience", kein neuer DB-Typ)
- [ ] Engram Dictionary Einträge für Experience-Engrams enthalten: content (zusammengefasst), expectation, outcome
- [ ] Neo4j: CAUSAL Link-Type mit directional semantics (from=cause, to=effect)
- [ ] Neo4j: PREDICTION_ERROR Link-Type mit weight = prediction_error_magnitude
- [ ] Link Creation erweitert: wenn StructuredUnit.unit_type == ACTION_EFFECT → CAUSAL Link erzeugen
- [ ] Link Creation erweitert: wenn StructuredUnit.unit_type == EXPERIENCE und expectation ≠ outcome → PREDICTION_ERROR Link erzeugen
- [ ] Prediction Error Magnitude berechnet als: `1.0 - cosine(embed(expectation), embed(outcome))`
- [ ] Experience-Engrams sind über Standard-Recall abrufbar (kein separater Endpunkt)

## Tasks

- [ ] **T1 — Neo4j Link Types erweitern:** CAUSAL und PREDICTION_ERROR zu VALID_RELATIONSHIP_TYPES hinzufügen. Schema-Constraints und Indexes.
- [ ] **T2 — Experience-Engram Speicherung:** In Engram Storage: wenn StructuredUnit.unit_type == EXPERIENCE → Content = "Expected: {expectation}. Outcome: {outcome}. Context: {context}". Tag "experience" hinzufügen. Expectation + Outcome als separate Felder in Dictionary.
- [ ] **T3 — CAUSAL Link Creation:** Neues Modul oder Erweiterung von link_creation.py. Wenn R0 ACTION_EFFECT Units produziert → CAUSAL Link zwischen Action-Engram und Effect-Engram erzeugen. Weight = Confidence aus R0.
- [ ] **T4 — PREDICTION_ERROR Link Creation:** Wenn Experience-Engram gespeichert wird UND `cosine(embed(expectation), embed(outcome)) < 0.8` (signifikante Divergenz) → PREDICTION_ERROR Link erzeugen. Weight = `1.0 - cosine_similarity` (= prediction_error_magnitude).
- [ ] **T5 — Tests:** Experience-Engram Speicherung mit korrekten Feldern. CAUSAL Link Direction. PREDICTION_ERROR Link Weight Berechnung. Kein PREDICTION_ERROR Link wenn Expectation ≈ Outcome. Recall-Kompatibilität (Experience-Engrams erscheinen in normalen Recall-Ergebnissen).
