# Story 02 — Bank Profile View erweitern: System Configuration

## User Story

Als Operator will ich im Bank Profile sehen welcher LLM Provider und welches Model meine Bank nutzt, ob alle Datenbanken verbunden sind und wie der NCR konfiguriert ist, damit ich den System-Zustand ohne CLI prüfen kann.

## Kontext

Das Bank Profile (`bank-profile-view.tsx`) zeigt aktuell: Name, Disposition (3 Traits: Skepticism, Literalism, Empathy), Background, Stats (Memory Count, etc.), Operations (letzte Async-Ops). Es fehlt jegliche Information über die technische Infrastruktur. Der Operator muss aktuell Env-Vars oder Logs prüfen um zu wissen welches Model aktiv ist.

## Bestehende Codebasis

- **CP Component:** `src/components/bank-profile-view.tsx` — Sections: Profile Header, Disposition Sliders, Background Text, Stats Cards, Operations Table.
- **CP Client:** `src/lib/api.ts` — `getBankProfile()`, `getBankStats()`. Neuer `getSystemConfig()` aus Story 01.

## Akzeptanzkriterien

- [ ] Neue Section "System Configuration" unterhalb der bestehenden Stats
- [ ] LLM Config als Info-Cards: Provider + Primary Model + Tier-Routing (Small/Medium/Large → Model)
- [ ] Infrastructure als Status-Badges: PostgreSQL, Qdrant, Neo4j — jeweils grün (connected) oder rot (disconnected)
- [ ] NCR Config: Enabled/Disabled Badge + Interval in Stunden
- [ ] Embedding + Reranker Model als Info-Text
- [ ] Daten werden beim Page-Load geladen (parallel zum Bank Profile)
- [ ] Loading State und Error Handling

## Tasks

- [ ] **T1 — System Config Section Component** — Neues Sub-Component `system-config-section.tsx` (oder inline in `bank-profile-view.tsx`). Ruft `client.getSystemConfig()` beim Mount auf. Zeigt Loading-Skeleton während des Fetches.

- [ ] **T2 — LLM Config Cards** — 3 Cards nebeneinander: (1) "Primary Model" — Provider + Model, (2) "Tier Routing" — Tabelle Small→Model, Medium→Model, Large→Model, (3) "Embedding & Reranker" — Model-Namen. Tailwind styling konsistent mit bestehenden Stats Cards.

- [ ] **T3 — Infrastructure Status Badges** — Horizontal: 3 Badges für PostgreSQL, Qdrant, Neo4j. Grüner Dot + "Connected" oder roter Dot + "Disconnected". Bei Disconnect: Warn-Styling (rote Border).

- [ ] **T4 — NCR Config Anzeige** — Badge "NCR Enabled" (grün) oder "NCR Disabled" (grau). Daneben: "Interval: Xh". Falls disabled: Hinweis-Text.

- [ ] **T5 — Integration in bank-profile-view.tsx** — Die neue Section unterhalb der bestehenden Stats-Section einfügen. Sicherstellen dass das Layout konsistent ist.
