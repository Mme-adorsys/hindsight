# Story 03 — NCR Phase 2: Strengthen

## User Story

Als System soll NCR Phase 2 häufig aktivierte Buffer-Engrams stärken und in den Neocortex-Layer promoten.

## Kontext

Biologisch: SWS-Replay stärkt wichtige Erinnerungen. Im System: Buffer-Engrams mit hoher Strength und häufigem Zugriff werden in den Neocortex-Layer promoten (layer='buffer' → layer='neocortex'). Das ist ein Property-Update, keine physische Datenkopie.

## Akzeptanzkriterien

- [ ] Promotion-Kriterien: layer='buffer' AND strength ≥ 0.4 AND access_count ≥ 3 AND überlebt ≥ 2 NCR-Zyklen
- [ ] Promotion: layer='buffer' → layer='neocortex' in Dictionary
- [ ] Strength-Boost bei Promotion: +0.1 (Konsolidierung stärkt)
- [ ] Tracking: ncr_cycles_survived Counter auf Engram
- [ ] Nicht-promovierte Buffer-Engrams: ncr_cycles_survived +1

## Tasks

- [ ] **T1 — StrengthenProcessor:** `engine/consolidation/ncr_strengthen.py`. Klasse `StrengthenProcessor(engram_repo, config)`. Methode `process(bank_id) → StrengthenResult`. Lädt Buffer-Engrams → prüft Promotion-Kriterien → Promoted/Updated.
- [ ] **T2 — Promotion-Kriterien:** Konfigurierbare Thresholds: `promotion_strength_threshold=0.4`, `promotion_access_threshold=3`, `promotion_ncr_cycles=2`. Alle drei müssen erfüllt sein.
- [ ] **T3 — Layer-Promotion:** Bei Promotion: Dictionary `layer = 'neocortex'`. Strength += 0.1. `promoted_at` Timestamp. Neo4j: Node-Property `layer` updaten. Qdrant: Payload `layer` updaten.
- [ ] **T4 — NCR Cycle Counter:** Neues Feld `ncr_cycles_survived: int` auf Engram Dictionary. Wird bei jedem NCR +1 (für nicht-archivierte Engrams). Resettable bei Content-Update (Reconsolidation).
- [ ] **T5 — Unit Tests:** Promotion bei Erfüllung aller Kriterien. Keine Promotion bei zu niedriger Strength. Keine Promotion bei zu wenig Access. ncr_cycles_survived Inkrement. Strength-Boost bei Promotion.
