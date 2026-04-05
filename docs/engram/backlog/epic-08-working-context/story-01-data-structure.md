# Story 01 — WorkingContext Data Structure

## User Story

Als System brauche ich eine WorkingContext Datenstruktur die Goal Stack, 3-Tier Active Engrams, Episodic Buffer und Inference Layer zusammenhält.

## Kontext

Das WorkingContext ist das PFC-Äquivalent im System. Es ist kein Speicher, sondern ein Workspace: Es hält Referenzen zu aktiven Engrams (nicht Kopien), verwaltet Ziele, und sammelt laufende Inferenzen. Alles transient.

## Akzeptanzkriterien

- [x] WorkingContext Dataclass mit allen 4 Komponenten
- [x] Goal Stack: Push/Pop Semantik, Ziele haben Priority und Status
- [x] Active Engrams: 3 Tiers (focus: 3-5, supporting: 5-10, peripheral: 10-20)
- [x] Episodic Buffer: Chronologische Liste der aktuellen Session-Episoden
- [x] Inference Layer: Laufende Schlussfolgerungen mit Confidence

## Tasks

- [x] **T1 — Goal Dataclass:** Neues Modul `engine/session/working_context.py`. `Goal(id, description, priority: float, status: Literal['active', 'completed', 'abandoned'], created_at, parent_goal_id: str | None)`. Goals können hierarchisch sein (Sub-Goals).
- [x] **T2 — ActiveEngrams Dataclass:** `ActiveEngrams(focus: list[EngamRef], supporting: list[EngramRef], peripheral: list[EngramRef])`. `EngramRef` ist leichtgewichtig: `(engram_id, strength, relevance_score, activated_at)`. Nicht das volle Engram-Objekt — nur Referenzen.
- [x] **T3 — Inference Dataclass:** `Inference(id, content: str, confidence: float, supporting_engram_ids: list[str], created_at, status: Literal['tentative', 'confirmed', 'rejected'])`. Inferenzen sind hypothetisch und können durch neue Evidenz bestätigt oder verworfen werden.
- [x] **T4 — WorkingContext Dataclass:** Zusammenführung: `WorkingContext(session_id, goal_stack: list[Goal], active_engrams: ActiveEngrams, episodic_buffer: list[Episode], inference_layer: list[Inference], created_at, last_updated)`.
- [x] **T5 — Capacity Limits:** Constants: `MAX_FOCUS = 5`, `MAX_SUPPORTING = 10`, `MAX_PERIPHERAL = 20`. Enforcement: Wenn Limit erreicht → schwächstes Element in nächsten Tier verschieben oder verwerfen.
- [x] **T6 — Unit Tests:** Goal Push/Pop. Active Engrams Capacity Limits. Tier-Overflow (Focus voll → nach Supporting). Inference Lifecycle (tentative → confirmed).
