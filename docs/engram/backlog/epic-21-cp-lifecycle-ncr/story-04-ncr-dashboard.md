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

- [ ] Neuer Sidebar-Eintrag "Consolidation" mit Icon (z.B. `RefreshCw` von lucide-react)
- [ ] `?view=consolidation` zeigt das NCR Dashboard
- [ ] "Run NCR Now" Button mit Confirmation Dialog
- [ ] Loading State während NCR läuft (kann Minuten dauern)
- [ ] Last Run Summary: 4 Cards (Consolidation, Decay, Strengthen, Schema) mit Zahlen
- [ ] Run History: Tabelle der letzten 20 Runs mit Zeitstempel, Dauer, Trigger-Type, Ergebnis-Summary
- [ ] Fehler-Anzeige: Expandable Error List wenn ein Run Errors hatte
- [ ] Cooldown-Hinweis: Wenn letzter Run < 5min her, Warnung anzeigen (nicht blockieren)

## Tasks

- [ ] **T1 — Sidebar erweitern** — In `sidebar.tsx` neues Nav-Item: `{ id: "consolidation", label: "Consolidation", icon: RefreshCw }`. NavItem Type erweitern.

- [ ] **T2 — Router erweitern** — In `page.tsx` neuen `{view === "consolidation" && ...}` Block. Titel "Consolidation Dashboard", Beschreibung, `<NCRDashboardView />` einbinden.

- [ ] **T3 — Component: `ncr-dashboard-view.tsx`** — Neues Component. Sections:
  - **Trigger Section:** "Run NCR Now" Button. Klick → Confirmation Dialog ("This will run the full consolidation pipeline. Continue?"). Bei Bestätigung: `client.triggerNCR(bankId)`. Loading Spinner während der Ausführung. Nach Erfolg: Last Run Summary aktualisieren.
  - **Last Run Summary:** 4 Cards nebeneinander: Consolidation (total/consolidated), Decay (total/decayed/archived), Strengthen (total/promoted), Schema (created/strengthened/deleted). Daten vom neuesten Run in History.
  - **Run History Table:** Spalten: Timestamp, Duration, Trigger (manual/scheduled Badge), Summary (one-line: "12 consolidated, 15 decayed, 8 promoted, 2 schemas"). Expandable Row für Details + Errors.

- [ ] **T4 — Error Handling** — NCR kann lange dauern (Minuten). Timeout auf 5min setzen. Bei Timeout: Hinweis "NCR is still running in the background". Bei API-Error: Error-Toast mit Details. Bei NCR-Errors im Report: Rot markierte Error-Section im Last Run Summary.

- [ ] **T5 — Cooldown-Logik** — Beim Laden prüfen: War der letzte Run < 5min her? Wenn ja: Gelber Hinweis "Last run was X minutes ago. Running NCR too frequently may not produce meaningful changes." Button bleibt klickbar (kein Hard-Block).
