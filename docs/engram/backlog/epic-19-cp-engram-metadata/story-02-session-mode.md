# Story 02 — Session Mode Selector in Recall und Reflect Views

## User Story

Als Operator will ich im Recall Analyzer und in der Reflect View den Session Mode wählen können (Precision, Exploration, Analogy, Validation), damit ich testen kann wie sich verschiedene Modi auf die Ergebnisse auswirken.

## Kontext

Die API akzeptiert `mode` auf `/recall` und `/reflect` seit der MCP Parameter Parity Implementierung. Die MCP Tools unterstützen ihn ebenfalls. Aber das CP UI bietet keinen Mode-Selector — Recall und Reflect laufen immer im Default-Modus (Precision). Für einen Operator ist es essenziell, die Modi direkt im UI testen und vergleichen zu können.

## Bestehende Codebasis

- **CP Recall View:** `src/components/search-debug-view.tsx` — Query-Input, Budget-Selector, Type-Filter, Trace-Toggle. Kein Mode-Selector.
- **CP Reflect View:** `src/components/think-view.tsx` — Query-Input, Budget-Selector, Context-Input. Kein Mode-Selector.
- **CP Client:** `src/lib/api.ts` — `recall()` und `reflect()` Methoden. `recall()` hat bereits `mode` im Interface aber optional. `reflect()` hat keinen `mode` Parameter.
- **CP API Routes:** `src/app/api/recall/route.ts`, `src/app/api/reflect/route.ts` — Proxy zur Dataplane.

## Akzeptanzkriterien

- [ ] Shared Component `session-mode-selector.tsx` existiert und ist wiederverwendbar
- [ ] 4 Modi werden angezeigt mit Kurzname + Beschreibung
- [ ] Default ist "Precision" (kein Mode = Precision)
- [ ] Recall View zeigt Mode Selector und sendet gewählten Mode an API
- [ ] Reflect View zeigt Mode Selector und sendet gewählten Mode an API
- [ ] CP API Routes reichen `mode` Parameter durch
- [ ] Ergebnisse ändern sich sichtbar je nach Mode (manuell verifizierbar)

## Tasks

- [x] **T1 — Shared Component: `session-mode-selector.tsx`** — Neues Component mit `SessionMode` Type (`"precision" | "exploration" | "analogy" | "validation"`). UI als Segmented Control oder Radio Group. Jeder Mode hat: Label, Kurztext (1 Satz), Icon. Props: `value: SessionMode`, `onChange: (mode: SessionMode) => void`. Default: precision.

- [x] **T2 — Recall View erweitern** — `search-debug-view.tsx`: Mode Selector einbauen (unterhalb des Query-Inputs oder neben Budget). State für `selectedMode`. Den Mode an `client.recall({ ..., mode: selectedMode })` übergeben. Nur senden wenn nicht "precision" (oder immer senden — konsistenter).

- [x] **T3 — Reflect View erweitern** — `think-view.tsx`: Mode Selector einbauen. State für `selectedMode`. Den Mode an `client.reflect({ ..., mode: selectedMode })` übergeben.

- [x] **T4 — CP API Routes erweitern** — `src/app/api/recall/route.ts`: `mode` aus dem Request Body extrahieren und an die Dataplane weiterleiten. `src/app/api/reflect/route.ts`: Ebenso `mode` durchreichen. In `api.ts` die `reflect()` Methode um `mode?: string` Parameter erweitern.
