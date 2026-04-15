# Story 05 — Bidirektionaler Lifecycle

## User Story

Als System sollen archivierte Engrams bei Recall-Reactivation in den aktiven Pool zurückkehren können und Buffer-Engrams bei mangelnder Nutzung in Working Memory oder Archive zurückfallen können, damit der Lifecycle dynamisch und bidirektional ist.

## Kontext

Der bisherige Lifecycle ist unidirektional: Working Memory → Buffer → Neocortex, mit Archive als Endstation. Das ist biologisch falsch — das Gehirn reaktiviert Erinnerungen und bewertet sie neu. Der neue Lifecycle ist bidirektional:

- **Archive → Working Memory / Buffer:** Ein archiviertes Engram das bei einem Recall getroffen wird (z.B. gezielte Suche) bekommt `access_count += 1`. Beim nächsten Consolidation-Cycle wird der Composite neu berechnet. Wenn er über dem Archive-Threshold liegt, wird das Engram reaktiviert. Liegt er sogar über dem Promote-Threshold, kann es direkt in den Buffer.
- **Buffer → Working Memory:** Ein Buffer-Engram das über mehrere Sessions nicht abgerufen wird, dessen Composite unter den Promote-Threshold fällt, wird zurück in Working Memory verschoben.
- **Neocortex:** Einmal konsolidiert ist ein Engram sicher. Neocortex-Engrams werden nur in späteren Phasen (C3 Schema Compression) umstrukturiert, aber nicht degradiert.

## Bestehende Codebasis

- **Recall Orchestrator:** `engine/recall_orchestrator.py` → Inkrementiert `access_count` bei Recall. Filtert aktuell nach `status='active'` — archivierte Engrams werden nicht durchsucht.
- **Engram Storage:** `engine/engram_storage.py` → Qdrant Payload mit `layer` und `status` Feldern.
- **NCR Decay:** `engine/consolidation/ncr_decay.py` → Setzt `status='archived'` wenn Strength unter Threshold. Kein Rückweg implementiert.
- **NCR Strengthen:** `engine/consolidation/ncr_strengthen.py` → Promote Buffer → Neocortex. Kein Downgrade Buffer → WM.

## Akzeptanzkriterien

- [ ] Archivierte Engrams sind bei Recall durchsuchbar (optionale Erweiterung des Suchradius)
- [ ] Recall auf archiviertem Engram inkrementiert `access_count` und setzt `last_accessed`
- [ ] Reactivation bei nächstem C2-Cycle: Composite > Archive-Threshold → `status='active'`, `layer='working_memory'`
- [ ] Reactivation mit hohem Composite: Composite > Promote-Threshold + Hard Gates → direkt `layer='buffer'`
- [ ] Buffer-Downgrade: Buffer-Engram mit Composite < Promote-Threshold (seines Tags) → `layer='working_memory'`
- [ ] Neocortex-Engrams sind von Downgrade ausgenommen
- [ ] Lifecycle-Transitionen werden geloggt (von_layer, nach_layer, composite_score, trigger)

## Tasks

- [ ] **T1 — Recall auf Archived Engrams:** `recall_orchestrator.py` erweitern: Optionaler `include_archived: bool = False` Parameter. Wenn True: Qdrant-Suche ohne `status='active'` Filter. Archived-Treffer bekommen `access_count += 1` und `last_accessed = now()` via bestehender `increment_access_count()`. Markierung: `reactivation_candidate = True` im Recall-Result.
- [ ] **T2 — Reactivation im C2-Cycle:** Neuer Schritt in `ncr_decay.py` (oder eigenes Modul `reactivation.py`): Nach Decay-Berechnung: Alle Engrams mit `status='archived'` prüfen. Composite neu berechnen. Composite > `ARCHIVE_THRESHOLD_WM` → `status='active'`, `layer=None` (Working Memory). Composite > Promote-Threshold (tag-abhängig) + Hard Gates → `status='active'`, `layer='buffer'`.
- [ ] **T3 — Buffer-Downgrade:** Neuer Schritt in `ncr_strengthen.py`: Vor Promote-Check: Alle Buffer-Engrams prüfen. Composite < `get_promote_threshold(tags)` → `layer=None` (zurück in Working Memory). Nur wenn `ncr_cycles_in_buffer ≥ 2` (Karenzzeit — frisch promotete Engrams bekommen mindestens 2 Cycles).
- [ ] **T4 — Neocortex-Guard:** Expliziter Guard in Decay und Downgrade: `if engram.layer == 'neocortex': skip`. Defensiv programmiert — auch wenn die Logik es nicht erfordert, verhindert ein Bug das versehentliche Degradieren von Neocortex-Engrams.
- [ ] **T5 — Lifecycle-Logging:** Neues Log-Event bei jeder Layer-Transition: `logger.info("Lifecycle transition: engram=%s from=%s to=%s composite=%.3f trigger=%s")`. Trigger-Typen: "promote", "archive", "reactivate", "downgrade". Optional: Persistierung in `lifecycle_events` Tabelle für NCR Dashboard (Epic 21).
- [ ] **T6 — Unit Tests:** Archived Engram mit access_count Bump → reactivated nach C2. Buffer Engram ohne Zugriffe → downgraded nach mehreren Cycles. Neocortex Engram → nie degradiert. Reactivation direkt in Buffer wenn Composite hoch genug + Hard Gates bestanden. Lifecycle-Log enthält alle Transitionen.
