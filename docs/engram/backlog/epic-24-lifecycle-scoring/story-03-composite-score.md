# Story 03 — Composite Score Migration

## User Story

Als System soll der Composite Score von `recall_score + saliency_weight × saliency` auf `thalamus_overall × decay` umgestellt werden, wobei der Decay die neue logarithmische Formel mit Equilibrium Rate nutzt.

## Kontext

Der alte Composite Score addiert einen Recall-Frequency-Wert und einen Saliency-Boost. Der neue Composite ist ein Produkt aus dem initialen Thalamus-Geburtswert und einem Decay-Faktor der über oder unter 1.0 liegen kann:

```
decay     = log(1 + access_count) / log(1 + sessions_alive × r)
composite = thalamus_overall × decay
```

Entscheidende Eigenschaft: `decay > 1.0` = Verstärkung (häufiger genutzt als erwartet), `decay < 1.0` = Zerfall. Der Composite kann dadurch über den initialen Thalamus-Score hinauswachsen. Die alte Funktion `compute_composite_strength()` und `compute_recount_score()` in `scoring.py` werden ersetzt.

## Bestehende Codebasis

- **scoring.py:** `compute_recount_score(access_count, cycles_alive)` und `compute_composite_strength(emotional_valence, surprise, access_count, cycles_alive)`. Beide werden ersetzt.
- **ncr_decay.py:** Ruft aktuell eigene Decay-Formel auf (`strength × 0.9 × frequency_bonus`). Muss auf neuen Composite umsteigen.
- **ncr_strengthen.py:** Nutzt `compute_composite_strength()` für Promote-Entscheidung.
- **ThalamusScores:** `overall` Feld = der Geburtswert für den neuen Composite.
- **Engram Dictionary:** `strength` Feld in PostgreSQL — aktuell der alte Composite. Wird mit neuem Composite überschrieben.

## Akzeptanzkriterien

- [ ] Neue Funktion `compute_decay(access_count, sessions_alive, r)` implementiert
- [ ] Neue Funktion `compute_composite(thalamus_overall, access_count, sessions_alive, r)` implementiert
- [ ] decay bei sessions_alive=0 → 1.0 (frisches Engram, kein Zerfall)
- [ ] decay bei access_count=0, sessions_alive>0 → nähert sich 0 (ungenutzt = Zerfall)
- [ ] decay bei access_count >> expected → > 1.0 (Verstärkung)
- [ ] Alte Funktionen `compute_recount_score` und `compute_composite_strength` als deprecated markiert
- [ ] `strength` Feld in Engram Dictionary speichert ab jetzt den neuen Composite

## Tasks

- [ ] **T1 — compute_decay Funktion:** `compute_decay(access_count: int, sessions_alive: int, r: float) → float` in `scoring.py`. Formel: `log(1 + access_count) / log(1 + sessions_alive × r)`. Edge Cases: sessions_alive=0 → return 1.0 (Division durch log(1)=0 vermeiden). r ≤ 0 → return 1.0 (Guard). access_count=0 → return 0.0 (log(1)/log(x) = 0). Kein Clamping — Decay darf > 1.0 sein.
- [ ] **T2 — compute_composite Funktion:** `compute_composite(thalamus_overall: float, access_count: int, sessions_alive: int, r: float) → float`. Berechnet decay intern via `compute_decay()`, multipliziert mit `thalamus_overall`. Rückgabe clamped auf [0.0, 10.0] (Overflow-Guard wie in alter Funktion).
- [ ] **T3 — Alte Funktionen deprecaten:** `compute_recount_score()` und `compute_composite_strength()` mit `@deprecated` Decorator oder Docstring-Warning. Nicht sofort löschen — externe Aufrufer könnten existieren. Logging-Warning bei Aufruf.
- [ ] **T4 — Strength-Feld Semantik:** Dokumentation: `engram_dictionary.strength` enthält ab jetzt den neuen Composite Score. Initiale Strength bei Engram-Erstellung = `thalamus_scores.overall` (Geburtswert, decay=1.0).
- [ ] **T5 — Unit Tests:** compute_decay bei sessions_alive=0 → 1.0. compute_decay(5, 5, 0.5) → ~1.43 (Verstärkung). compute_decay(1, 100, 0.5) → ~0.18 (starker Verfall). compute_composite(0.9, 10, 5, 0.5) → > 0.9 (Verstärkung). compute_composite(0.9, 0, 100, 0.5) → nahe 0 (Verfall). Decay-Tabelle aus concept.md Kapitel 5.3 als Referenz validieren.
