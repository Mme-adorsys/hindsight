# Story 01 — ConstructedAnswer Data Model

## User Story

Als System brauche ich ein ConstructedAnswer Datenmodell das Facts, Inferences und Gaps klar trennt und Confidence + Mode-Einfluss dokumentiert.

## Kontext

Bisher liefert recall eine flache Liste von MemoryFacts. Die ConstructedAnswer ist reicher: Sie unterscheidet zwischen direkt abgerufenen Fakten (Facts), daraus abgeleiteten Schlussfolgerungen (Inferences), und identifizierten Wissenslücken (Gaps). Jedes Element hat eine eigene Confidence.

## Akzeptanzkriterien

- [x] ConstructedAnswer Pydantic Model mit facts, inferences, gaps, confidence, mode_influence
- [x] Fact: Referenz zum Quell-Engram mit Confidence
- [x] Inference: Abgeleitete Information mit supporting_engrams und Confidence
- [x] Gap: Identifizierte Wissenslücke mit Beschreibung und Relevanz
- [x] Mode-Influence dokumentiert welcher Mode die Construction beeinflusst hat
- [x] Backward-kompatibel: Kann zu flacher MemoryFact-Liste degradiert werden

## Tasks

- [x] **T1 — ConstructedFact Dataclass:** `engine/constructive/models.py`. `ConstructedFact(engram_id, content, confidence: float, source: Literal['direct', 'reconstructed'], tags, thalamus_scores)`. Direct = unmodifiziert aus Engram. Reconstructed = angepasst an Kontext.
- [x] **T2 — Inference Dataclass:** `Inference(content: str, confidence: float, supporting_engram_ids: list[str], inference_type: Literal['deduction', 'analogy', 'interpolation', 'extrapolation'], reasoning: str)`. Reasoning dokumentiert die Schlussfolgerung.
- [x] **T3 — Gap Dataclass:** `Gap(description: str, relevance: float, related_engram_ids: list[str], suggested_query: str | None)`. Suggested Query: Wie könnte das System die Lücke schließen.
- [x] **T4 — ConstructedAnswer Model:** `ConstructedAnswer(facts: list[ConstructedFact], inferences: list[Inference], gaps: list[Gap], overall_confidence: float, mode_influence: str, construction_metadata: dict)`. `to_memory_facts() → list[MemoryFact]` für backward compat.
- [x] **T5 — API Response Extension:** `RecallResultModel` um optionales `constructed_answer: ConstructedAnswer | None` erweitern. Wenn Construction aktiv → ConstructedAnswer befüllt. Wenn nicht → wie bisher.
- [x] **T6 — Unit Tests:** Model-Validierung (Pydantic). to_memory_facts() Degradation. Confidence-Berechnung. Alle Inference-Types.
