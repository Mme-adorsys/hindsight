# Story 01 — Golden Dataset Design & Creation (Benchmark C)

## User Story

Als Entwickler brauche ich ein kuratiertes Dataset mit Ground Truth um das Gesamtsystem quantitativ zu bewerten.

## Kontext

Inspiriert von BEIR/MS MARCO, adaptiert für Engram-Modell. Das Dataset enthält: Input-Episoden, erwartete Engrams, erwartete Links, erwartete Schemas, erwartete Retrieval-Ergebnisse pro Mode, erwartete Konstruktionen.

## Akzeptanzkriterien

- [ ] Dataset deckt alle 4 Validierungsdimensionen ab
- [ ] Mindestens 100 Input-Episoden in 5+ thematischen Clustern
- [ ] Ground Truth für: Fact Extraction, Entity Resolution, Link Creation, Schema Formation
- [ ] Ground Truth für: Retrieval Ranking pro Mode (Precision, Exploration, Analogy, Validation)
- [ ] Ground Truth für: Construction Quality (erwartete Inferenzen + Gaps)
- [ ] Maschinenlesbar (JSON/JSONL Format)

## Tasks

- [ ] **T1 — Dataset Schema Definition:** JSON Schema für Golden Dataset. Felder: `episodes` (Input), `expected_engrams`, `expected_links`, `expected_schemas`, `retrieval_queries` (query + mode + expected_ranking), `construction_queries` (query + expected_facts + expected_inferences + expected_gaps). Versioned.
- [ ] **T2 — Thematische Cluster:** 5 Cluster erstellen: (1) Technisches Wissen (Programmierung, Architektur), (2) Persönliches (Präferenzen, Gewohnheiten), (3) Projekte (Timelines, Abhängigkeiten), (4) Widersprüchliches (sich widersprechende Fakten), (5) Zeitliches (sich änderndes Wissen über Monate).
- [ ] **T3 — Input-Episoden erstellen:** 100+ Episoden in den 5 Clustern. Variiert: Länge, Komplexität, Ambiguität, Kausalität. Einige Episoden widersprechen früheren (für Reconsolidation/Contradiction Testing).
- [ ] **T4 — Ground Truth annotieren:** Für jede Episode: Erwartete Engrams (Content + Tags + Thalamus Scores). Erwartete Links (Type + Weight). Für jeden Cluster: Erwartete Schemas nach N NCR-Zyklen.
- [ ] **T5 — Retrieval Ground Truth:** 30+ Retrieval-Queries mit erwartetem Ranking pro Mode. Precision-Query: Erwartete Top-3 Engrams. Exploration-Query: Erwartete Top-10 (inkl. schwacher Links). Analogy-Query: Erwartete Cross-Domain Treffer. Validation-Query: Erwartete Contradiction-Engrams.
- [ ] **T6 — Construction Ground Truth:** 15+ Queries mit erwarteten ConstructedAnswers. Facts + Inferences + Gaps annotiert. Mode-Einfluss dokumentiert.
- [ ] **T7 — Dataset Validation:** Cross-Review des Datasets (konsistent, keine Widersprüche in der Ground Truth selbst). Automated Checks: Schema-Validierung, Referenzielle Integrität.
