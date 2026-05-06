# Story 18 — Cleanup alte ncr_decay/ncr_strengthen/schema_processor

## User Story

Als Codebasis soll ich die alten Module der 5-Phasen-Pipeline (`ncr_decay.py`, `ncr_strengthen.py`, `schema_processor.py`) entfernen, damit nur noch die neue 3-Phasen-Architektur (C1, C2, C3) im Code lebt — kein toter Code, keine zwei parallelen Wahrheiten.

## Kontext

Die alte Architektur ist durch die Stories 01–17 ersetzt. Wir entfernen jetzt die alten Module, damit der Code-State eindeutig ist. Die Logik wandert in `consolidation1.py` (C1, leicht angepasst), `c2_*.py` (Pattern Recognition + Decay-Re-Eval), `c3_schema_restructure.py` (R3 + R5).

## Bestehende Codebasis

- **`engine/consolidation/ncr_decay.py`:** alte C2a-Logik — entfernen (Decay-Re-Eval ist jetzt in `c2_decay.py`).
- **`engine/consolidation/ncr_strengthen.py`:** alte C2b-Logik (buffer→neocortex Promotion) — entfernen (gibt's nicht mehr).
- **`engine/consolidation/schema_processor.py`:** alte Schema-Compression-Hook — entfernen (R1+R2 in C2, R3+R5 in C3).
- **`engine/consolidation/ncr_orchestrator.py`:** umbauen auf 3-Phasen-Schema mit Aufrufen an `c1`, `c2`, `c3`.
- **Tests:** alte Tests anpassen oder löschen.

## Akzeptanzkriterien

- [ ] `ncr_decay.py`, `ncr_strengthen.py`, `schema_processor.py` gelöscht
- [ ] Alle Importe darauf entfernt; Compile-Check sauber
- [ ] `ncr_orchestrator.py` ruft nur noch `c1`, `c2`, `c3` auf
- [ ] API-Endpoints (`POST /v1/default/banks/{bank_id}/ncr/trigger?phase=...`) akzeptieren nur noch `phase ∈ {c1, c2, c3}`
- [ ] Alte Migrations bleiben (DB-Schema), aber kein laufender Code referenziert sie
- [ ] Doku in `engine/consolidation/README.md` (falls vorhanden) auf neue Struktur umgestellt
- [ ] Tests grün

## Tasks

- [ ] **T1 — Code löschen:** `git rm ncr_decay.py ncr_strengthen.py schema_processor.py`.
- [ ] **T2 — Importe bereinigen:** Codebasis durchsuchen (`grep -r "from engine.consolidation.ncr_decay"`, etc.), alle Importe entfernen oder umbiegen auf neue Module.
- [ ] **T3 — Orchestrator umbauen:** `ncr_orchestrator.py::run_full_cycle(bank_id)` ruft nur noch `c1`, `c2`, `c3` (in dieser Reihenfolge), C3 nur bei jedem 7. C2-Lauf (Default).
- [ ] **T4 — API-Endpoints:** Phase-Enum auf `{c1, c2, c3}` reduziert, alte Werte (`c2a`, `c2b`, etc.) erzeugen 400-Bad-Request mit Hinweis auf neue Phasen.
- [ ] **T5 — Tests bereinigen:** Tests die `ncr_decay`/`ncr_strengthen`/`schema_processor` referenzieren entweder löschen (wenn obsolet) oder umbauen auf neue Module.
- [ ] **T6 — Doku:** Falls `engine/consolidation/README.md` existiert → Update auf 3-Phasen-Struktur.
