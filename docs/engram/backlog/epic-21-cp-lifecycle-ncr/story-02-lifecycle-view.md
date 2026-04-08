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

- [x] Neuer Sidebar-Eintrag "Engrams" mit Layer-Icon (`Layers` von lucide-react)
- [x] `?view=engrams` zeigt die Engram Lifecycle View
- [x] 3 Layer Cards nebeneinander: Working Memory, Buffer, Neocortex — je Count + Avg Strength
- [x] Flow-Visualisierung: Pfeile (CSS, `ArrowRight`-Icon zwischen den Cards auf md+ Screens)
- [x] Strength Distribution als Stacked Bar (weak=rot, moderate=gelb, strong=grün) mit Legende
- [x] Auto-Refresh alle 30 Sekunden + zusätzlicher manueller Refresh-Button
- [x] Loading Skeleton bei initialem Load

## Tasks

- [x] **T1 — Sidebar erweitern** — `sidebar.tsx`: `NavItem`-Type um `"engrams"` erweitert, `Layers`-Import und Nav-Item `{ id: "engrams", label: "Engrams", icon: Layers }` eingefügt (zwischen "Entities" und "Memory Bank" platziert).

- [x] **T2 — Router erweitern** — `page.tsx`: `NavItem`-Type aligned, `EngramLifecycleView`-Import und `{view === "engrams" && ...}`-Block mit Titel "Engram Lifecycle" und Beschreibung eingefügt.

- [x] **T3 — Component: `engram-lifecycle-view.tsx`** — Neues Component mit `useBank()` + `client.getEngramStats(bankId)`. 3 Layer Cards (Working Memory grau/slate, Buffer gelb/amber, Neocortex grün/emerald) zeigen Count, Prozent vom Total, Avg-Strength-Bar mit Referenzlinie bei 0.4 (Epic 12 Promotion-Schwelle) und Kurzbeschreibung. `ArrowRight`-Icons zwischen den Cards als Flow-Visualisierung. Strength Distribution als 3-Segment Stacked Bar mit Tooltips + Legende. Loading-Skeleton mit Card/animate-pulse, Error-State mit rotem Hinweis. Pattern folgt `system-config-section.tsx` aus Epic 20.

- [x] **T4 — Refresh-Mechanismus** — `useEffect` + `setInterval(30_000)`, silent-Refresh-Mode (behält alte Daten im State) + manueller Button mit `RefreshCw`-Spinner. Cleanup via `clearInterval` in Effect-Return. `useCallback` für stabile Dependencies.
