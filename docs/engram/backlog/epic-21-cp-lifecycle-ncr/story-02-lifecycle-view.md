# Story 02 — Engram Lifecycle View

## User Story

Als Operator will ich auf einen Blick sehen wie viele Engrams in jedem Layer liegen und wie die Strength-Verteilung aussieht, damit ich beurteilen kann ob die Konsolidierung meiner Memory Bank gesund verläuft.

## Kontext

Das Concept definiert 3 Engram-Layer: Working Memory (layer=null, frisch), Buffer (nach C1 Consolidation), Neocortex (nach NCR/C2, Langzeitwissen). Ein gesundes System hat eine Verteilung: viele in Working Memory (neue Inputs), weniger im Buffer (wartend auf NCR), wenige aber starke im Neocortex. Ein ungesundes System hätte z.B. alles im Working Memory (NCR läuft nicht) oder alles schwach im Buffer (NCR promoted nicht).

## Bestehende Codebasis

- **CP Sidebar:** `src/components/sidebar.tsx` — 6 Nav-Items. Muss um "Engrams" erweitert werden.
- **CP Router:** `src/app/banks/[bankId]/page.tsx` — View-Switch basierend auf `?view=`. Muss `"engrams"` View hinzufügen.
- **CP Client:** `src/lib/api.ts` — `getEngramStats()` aus Story 01.

## Akzeptanzkriterien

- [ ] Neuer Sidebar-Eintrag "Engrams" mit Layer-Icon (z.B. `Layers` von lucide-react)
- [ ] `?view=engrams` zeigt die Engram Lifecycle View
- [ ] 3 Layer Cards nebeneinander: Working Memory, Buffer, Neocortex — je Count + Avg Strength
- [ ] Flow-Visualisierung: Pfeile oder Sankey-artige Darstellung Working Memory → Buffer → Neocortex
- [ ] Strength Distribution als Histogram oder Stacked Bar (weak/moderate/strong)
- [ ] Auto-Refresh alle 30 Sekunden (oder manueller Refresh-Button)
- [ ] Loading Skeleton bei initialem Load

## Tasks

- [ ] **T1 — Sidebar erweitern** — In `sidebar.tsx` neues Nav-Item: `{ id: "engrams", label: "Engrams", icon: Layers }`. NavItem Type in `sidebar.tsx` und `page.tsx` um `"engrams"` erweitern.

- [ ] **T2 — Router erweitern** — In `page.tsx` neuen `{view === "engrams" && ...}` Block. Titel "Engram Lifecycle", Beschreibung, `<EngramLifecycleView />` einbinden.

- [ ] **T3 — Component: `engram-lifecycle-view.tsx`** — Neues Component. Lädt `client.getEngramStats(bankId)` beim Mount. 3 Layer Cards (top row): Working Memory (grau), Buffer (gelb), Neocortex (grün). Jede Card zeigt Count, Avg Strength als Progress Bar, Prozent vom Total. Unterhalb: Flow-Visualisierung mit Pfeilen (CSS-basiert, kein Charting-Library nötig) und Strength Distribution als 3-Segment Bar (weak=rot, moderate=gelb, strong=grün).

- [ ] **T4 — Refresh-Mechanismus** — Auto-Refresh via `setInterval` (30s) oder manueller Button. Sanftes Update (kein Flicker — vorherige Daten behalten bis neue da sind).
