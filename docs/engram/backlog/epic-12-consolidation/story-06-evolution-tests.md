# Story 06 — Knowledge Evolution Tests

## User Story

Als Entwickler brauche ich Tests die belegen, dass Engrams sich über mehrere NCR-Zyklen korrekt entwickeln: Decay, Stärkung, Promotion, Archivierung.

## Kontext

Ab Epic 12 beginnt laut Test-Policy die Knowledge-Evolution-Test-Phase + Benchmark B (Simulated Agent Life). Diese Tests validieren das Langzeitverhalten über simulierte Zeiträume.

## Akzeptanzkriterien

- [x] Multi-Cycle Test: 5+ NCR-Zyklen simuliert
- [x] Schwache Engrams werden archiviert (erwartete Anzahl validiert)
- [x] Starke Engrams werden promoviert (buffer → neocortex)
- [x] Strength-Verlauf über Zeit entspricht der Decay/Strengthen Formel
- [x] Kein Datenverlust: Archivierte Engrams sind noch findbar

## Tasks

- [x] **T1 — Multi-Cycle Test Fixture:** 30 Engrams mit verschiedenen Strength-Werten und Access-Patterns. Simuliere 5 NCR-Zyklen. Zwischen Zyklen: Einige Engrams werden accessed (simulate recall).
- [x] **T2 — Decay Trajectory Test:** Engram mit Strength 0.3 und 0 Access → Erwartete Strength nach 5 Zyklen berechnen → Validieren.
- [x] **T3 — Promotion Trajectory Test:** Engram mit Strength 0.3, regelmäßig accessed → Erwartet: Strength steigt, wird nach ~3 Zyklen promoted.
- [x] **T4 — Archive Test:** Engram mit Strength 0.1, nie accessed → Erwartet: Archiviert nach ~2 Zyklen. Noch via expliziter ID-Suche findbar.
- [x] **T5 — Lifecycle Report:** Generiere CSV/Report der Engram-Entwicklung über Zyklen: `(cycle, engram_id, strength, layer, access_count)`. Dient als Baseline für Benchmark B.
