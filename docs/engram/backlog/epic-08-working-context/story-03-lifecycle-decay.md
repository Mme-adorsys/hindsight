# Story 03 — Lifecycle & Decay

## User Story

Als System soll der Working Context über die Dauer einer Session einen natürlichen Decay haben, damit alte Aktivierungen schwächer werden und Platz für neue machen.

## Kontext

Biologisch: PFC-Aktivierungen sind flüchtig. Was nicht aktiv gehalten (rehearsed) wird, verliert an Stärke. Der Working Context simuliert das: Engrams die nicht erneut abgerufen werden sinken in niedrigere Tiers ab. Bei Session-Ende werden relevante Inhalte via Retain in Engrams überführt.

## Akzeptanzkriterien

- [ ] Periodischer Decay: Relevance Score sinkt mit der Zeit seit letzter Aktivierung
- [ ] Tier-Abstieg bei Score unter Threshold (Focus → Supporting → Peripheral → Entfernt)
- [ ] Session-Ende Flush: Relevante Working Context Inhalte → Retain Pipeline
- [ ] Inferenzen mit Status "confirmed" werden bei Flush zu Engrams
- [ ] Goals mit Status "completed" werden als Episoden logged

## Tasks

- [ ] **T1 — Decay Timer:** In `working_context.py`: `WorkingContext.apply_decay(elapsed_minutes: float)`. Für jedes Engram im Working Context: `relevance_score *= decay_factor^elapsed_minutes`. `decay_factor` = 0.95 (5% Verlust pro Minute). Mode-abhängig: Precision decayed schneller (Fokus enger), Exploration langsamer (breiter Kontext gewünscht).
- [ ] **T2 — Tier Demotion:** Nach Decay: Engrams unter Tier-Threshold absteigen lassen. Focus Threshold: 0.5 → Supporting. Supporting Threshold: 0.3 → Peripheral. Peripheral Threshold: 0.1 → Entfernt.
- [ ] **T3 — Session-Ende Flush:** `WorkingContext.flush() → list[RetainContent]`. Sammelt: Confirmed Inferenzen → als RetainContent mit Tag "inferred". Completed Goals → als Episode. Active Focus-Engrams → Access Count Update (wurden aktiv genutzt). Return: Liste für die Retain Pipeline.
- [ ] **T4 — Integration in SessionManager:** `SessionManager.end_session()`: Ruft `working_context.flush()` auf. Ergebnis → `retain_batch_async()` für Persistierung. Dann Working Context und Session verwerfen.
- [ ] **T5 — Unit Tests:** Decay über Zeit (Engram Score sinkt). Tier-Demotion bei niedrigem Score. Flush erzeugt korrekte RetainContent. Completed Goals als Episoden. Mode-abhängige Decay-Rate.
