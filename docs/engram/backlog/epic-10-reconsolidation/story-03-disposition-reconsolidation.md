# Story 03 — Disposition-aware Reconsolidation (RF4)

## User Story

Als System soll die Agent-Disposition beeinflussen WIE Engrams bei Reconsolidation modifiziert werden.

## Kontext

Verschiedene Agent-Persönlichkeiten (Dispositions) sollten unterschiedlich mit widersprüchlicher Evidenz umgehen. Ein optimistischer Agent stärkt positive Engrams mehr. Ein analytischer Agent gewichtet Evidenz höher. Ein konservativer Agent behält bestehende Engrams bei Unsicherheit. Die Disposition modifiziert nicht WAS reconsolidiert wird (→ Priority Queue), sondern WIE.

## Bestehende Codebasis

- **Bank Disposition:** `retain/bank_utils.py` — `BankProfile` mit `disposition: dict`. Aktuell: Personality-Traits pro Bank.
- **reflect_async:** `memory_engine.py` — Wird in Story 01+02 bereits umgebaut.
- **LLM Prompt:** Wird in Story 02 T4 bereits erweitert.

## Akzeptanzkriterien

- [ ] Disposition fließt als Kontext in den Reconsolidation LLM-Prompt
- [ ] Unterschiedliche Dispositions erzeugen unterschiedliche Reconsolidation-Ergebnisse
- [ ] 3 Standard-Profiles: Analytical, Optimistic, Conservative
- [ ] Disposition beeinflusst Strength-Update: Analytical → stärkere Anpassung, Conservative → schwächere
- [ ] Ohne Disposition: Neutral (keine Bias)

## Tasks

- [ ] **T1 — Disposition Profiles:** In `reflect/` oder `session/`: Dataclass `DispositionProfile(name, evidence_weight: float, update_bias: float, contradiction_tolerance: float)`. 3 Defaults: Analytical (evidence=1.2, bias=0.0, contradiction=0.3), Optimistic (evidence=0.8, bias=+0.2, contradiction=0.5), Conservative (evidence=0.8, bias=0.0, contradiction=0.7).
- [ ] **T2 — LLM Prompt Enrichment:** Disposition als Kontext in den Reconsolidation-Prompt: "This agent tends to {disposition_description}. When evaluating conflicting evidence, apply this perspective." Prompt-Templates pro Disposition.
- [ ] **T3 — Strength Update Modulation:** Strength-Update aus Story 01 T4 wird durch Disposition moduliert: `strength_delta *= disposition.evidence_weight`. Contradiction Tolerance: Wenn LLM Widerspruch erkennt UND `similarity > disposition.contradiction_tolerance` → kein Strength-Reduction (Agent toleriert Widerspruch).
- [ ] **T4 — BankProfile → DispositionProfile Mapping:** Bestehende Hindsight `disposition: dict` → `DispositionProfile` konvertieren. Lookup: `get_disposition_profile(bank_profile) → DispositionProfile`. Fallback: Neutral (keine Modification).
- [ ] **T5 — Unit Tests:** Analytical Disposition → stärkere Evidenz-Gewichtung. Optimistic → positive Bias. Conservative → weniger Updates. Neutral Fallback. Contradiction Tolerance blockiert Strength-Reduction.
