# Story 04 — NCR Dashboard View

## User Story

Als Operator will ich den NCR manuell auslösen, die Ergebnisse sehen und die History der letzten Runs einsehen können, damit ich die Konsolidierung meiner Memory Bank überwachen und bei Bedarf eingreifen kann.

## Kontext

Der NCR (Nightly Consolidation Run) ist der zentrale Langzeitprozess: Er bewegt Engrams durch die Layer (Decay, Strengthen, Schema Compression). Die API hat `POST /ncr/trigger` bereits, aber es gibt kein UI. Der Operator muss aktuell curl-Befehle nutzen.

## Bestehende Codebasis

- **CP Sidebar:** `src/components/sidebar.tsx` — wird in Story 02 bereits um "Engrams" erweitert. Hier kommt "Consolidation" dazu.
- **CP Router:** `src/app/banks/[bankId]/page.tsx` — muss `"consolidation"` View hinzufügen.
- **CP Client:** `src/lib/api.ts` — `triggerNCR()` und `getNCRHistory()` aus Story 03.

## Akzeptanzkriterien

- [x] Neuer Sidebar-Eintrag "Consolidation" mit `RefreshCw`-Icon
- [x] `?view=consolidation` zeigt das NCR Dashboard
- [x] "Run NCR Now" Button mit Confirmation Dialog (`AlertDialog` aus `@/components/ui/alert-dialog`)
- [x] Loading State während NCR läuft (spinning Icon, Button disabled, Label "Running NCR...")
- [x] Last Run Summary: 4 Cards (Consolidation / Decay / Strengthen / Schema) mit Metrics aus `history[0]._stats`
- [x] Run History: Tabelle der letzten 20 Runs mit Zeitstempel, Duration, Trigger-Badge, Summary
- [x] Expandable Row-Details mit JSON-Dumps aller Phase-Stats + rote Error-Section bei Errors
- [x] Cooldown-Hinweis wenn letzter Run < 5min her (gelber Warn-Box, Button bleibt klickbar)

## Tasks

- [x] **T1 — Sidebar erweitern** — `sidebar.tsx`: `NavItem`-Type um `"consolidation"` erweitert (multi-line union), `RefreshCw`-Import und Nav-Item zwischen "Engrams" und "Memory Bank" eingefügt.

- [x] **T2 — Router erweitern** — `page.tsx`: `NavItem`-Type aligned, `NCRDashboardView`-Import und `{view === "consolidation" && ...}`-Block mit Titel "Consolidation Dashboard" und Beschreibung eingefügt.

- [x] **T3 — Component: `ncr-dashboard-view.tsx`** — Neues Component mit `useBank()` + `useState` für `history/loading/triggering/error/dialogOpen/expanded`. Trigger-Section mit `AlertDialog` Confirmation. State-Entkopplung: nach erfolgreichem Trigger wird die History neu geladen, `lastRun = history[0]` speist die 4 Summary-Cards. Gradient-Cards mit Metrics (total/consolidated/decayed/archived/promoted/created/strengthened/deleted). Run History Table mit `Fragment`-gewrappten expandable Rows (ChevronRight/Down Toggle), JSON-Dumps pro Phase im Expand + roter Error-Section mit Bullet-Liste. Robust gegen fehlende Stats via `numFromStats`-Helper.

- [x] **T4 — Error Handling** — Inline-Error-Panel (rote Border+Background+AlertTriangle) im Trigger-Card. 5-min Timeout ist bereits auf der `/api/ncr/trigger` CP-Proxy-Route via AbortController implementiert (Story 03), liefert 504 mit Hinweis "may still be running". Dashboard fängt HTTP-Errors ab und zeigt sie inline. Pro History-Row ein Status-Icon (CheckCircle grün oder AlertTriangle rot mit Error-Count) und im Expand die volle Error-Liste.

- [x] **T5 — Cooldown-Logik** — `useMemo` vergleicht `Date.now() - history[0].started_at` gegen 5 min. Zeigt gelbe Warn-Box mit Minuten-Angabe und zusätzlichem Hinweis auf die Server-Rate-Limit (1h). Button bleibt klickbar — nicht hard-blockiert. Bei 429 vom Server greift automatisch der Error-Panel aus T4.
