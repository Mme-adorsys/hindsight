# Story 04 — Tag-abhängige Thresholds & normalisierte Hard Gates

## User Story

Als System sollen die Promote-Thresholds tag-abhängig sein (Facts strenger als Experiences/Opinions) und die Hard Gates bankgrößen-normalisiert, damit kleine und große Banks fair behandelt werden.

## Kontext

**Tag-abhängige Promote-Thresholds:** Facts sind "billige" Informationseinheiten — viele kommen rein, die meisten sind Routine. Sie brauchen einen höheren Schwellenwert (0.7). Experiences und Opinions sind bereits verarbeitetes Wissen und brauchen einen niedrigeren Schwellenwert (0.4).

**Normalisierte Hard Gates:** In kleinen Banks (50 Engrams) hat jedes Engram eine hohe Recall-Wahrscheinlichkeit — `access_count` steigt schneller. Die bestehende Hard Gate `access_count ≥ 5` wäre unfair leicht. Normalisierung: `min_access = base_min_access × bank_factor`.

Ersetzt die bestehenden `MODE_PROMOTE_THRESHOLDS` und den festen `MIN_ACCESS_FOR_PROMOTE = 5`.

## Bestehende Codebasis

- **scoring.py:** `MODE_PROMOTE_THRESHOLDS` (mode-abhängig: precision=0.8, exploration=0.5), `MIN_ACCESS_FOR_PROMOTE = 5`, `MIN_NOVELTY_FOR_PROMOTE = 0.2`, `get_promote_threshold(mode)`.
- **Engram Tags:** `engram_dictionary` hat `tags` (JSON Array). Tags enthalten Kategorien wie "fact", "experience", "opinion".
- **bank_factor:** Aus Story 02 — `compute_bank_factor()`.

## Akzeptanzkriterien

- [ ] Neue Promote-Thresholds: fact=0.7, experience=0.4, opinion=0.4 (ersetzt mode-abhängige Thresholds)
- [ ] Funktion `get_promote_threshold(tags)` nutzt Tag-Kategorie statt Mode
- [ ] Fallback: kein passendes Tag → 0.7 (konservativ, wie fact)
- [ ] `min_access = ceil(base_min_access × bank_factor)` mit base_min_access=5
- [ ] min_access bei bank_size=50 → 9 (strenger), bei bank_size=50000 → 3 (milder)
- [ ] Novelty-Gate bleibt unverändert: novelty ≥ 0.2
- [ ] Alte `MODE_PROMOTE_THRESHOLDS` als deprecated markiert

## Tasks

- [ ] **T1 — Tag-Promote-Thresholds:** `TAG_PROMOTE_THRESHOLDS: dict[str, float]` in `scoring.py`. Keys: "fact"=0.7, "experience"=0.4, "opinion"=0.4. `DEFAULT_PROMOTE_THRESHOLD = 0.7` als Fallback.
- [ ] **T2 — get_promote_threshold Migration:** Signatur ändern: `get_promote_threshold(tags: list[str] | None) → float`. Logik: Iteriere über Tags, finde erste Übereinstimmung mit `TAG_PROMOTE_THRESHOLDS`. Kein Match → `DEFAULT_PROMOTE_THRESHOLD`. Bei mehreren Matches: niedrigster Threshold gewinnt (zugunsten des Engrams).
- [ ] **T3 — Normalisierte min_access:** Neue Funktion `compute_min_access(bank_size: int, base: int = 5) → int`. Formel: `ceil(base × compute_bank_factor(bank_size))`. Minimum: 1 (kann nie auf 0 sinken). Nutzt `compute_bank_factor()` aus Story 02.
- [ ] **T4 — Gate-Check Funktion:** `passes_hard_gates(access_count, novelty, bank_size) → bool`. Prüft: `access_count ≥ compute_min_access(bank_size)` AND `novelty ≥ MIN_NOVELTY_FOR_PROMOTE`. Ersetzt die bisherigen inline-Checks in ncr_strengthen.py.
- [ ] **T5 — Alte Thresholds deprecaten:** `MODE_PROMOTE_THRESHOLDS` und alte `get_promote_threshold(mode)` Signatur als deprecated markieren. Logging-Warning bei Aufruf.
- [ ] **T6 — Unit Tests:** fact-Tag → threshold 0.7. experience-Tag → threshold 0.4. Kein Tag → 0.7 (Fallback). min_access bei bank_size=50 → 9. min_access bei bank_size=1000 → 5. min_access bei bank_size=50000 → 3. passes_hard_gates bei access_count=3, bank_size=50000 → True. passes_hard_gates bei access_count=3, bank_size=50 → False.
