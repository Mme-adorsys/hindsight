# Story 02 — NCR Phase 1: Decay

## User Story

Als System soll NCR Phase 1 schwache Engrams abschwächen und unter dem Decay-Threshold archivieren.

## Kontext

Biologisch: SWS/Sharp-Wave Ripples — schwache synaptische Verbindungen werden abgebaut. Im System: Engrams mit niedriger Strength und wenig Zugriffen werden weiter geschwächt. Unter einem Threshold werden sie archiviert (nicht gelöscht — archivierte Engrams können bei expliziter Suche noch gefunden werden).

## Akzeptanzkriterien

- [ ] Decay-Formel: `new_strength = strength * decay_rate * frequency_bonus`
- [ ] decay_rate = 0.9 (10% Verlust pro NCR-Zyklus, konfigurierbar)
- [ ] frequency_bonus: Häufig zugegriffene Engrams decayen langsamer
- [ ] Archive-Threshold: strength < 0.05 → status='archived'
- [ ] Archivierte Engrams: In Neo4j als `archived=true` markiert, in Qdrant-Collection belassen (aber niedrig priorisiert)
- [ ] Logging: Anzahl decayed, archived, unchanged

## Tasks

- [ ] **T1 — DecayProcessor:** `engine/consolidation/ncr_decay.py`. Klasse `DecayProcessor(engram_repo, neo4j_client, config)`. Methode `process(bank_id) → DecayResult`. Lädt alle Buffer + Neocortex Engrams → Decay-Formel → Update Strength → Archive wenn < Threshold.
- [ ] **T2 — Decay-Formel:** `frequency_bonus = 1.0 + log(1 + access_count) / 10`. Engrams mit access_count=0 → reiner decay_rate. access_count=100 → decay_rate * 1.2 (kaum Decay). `days_since_access = (now - last_accessed).days`. Extra Decay wenn > 30 Tage: `new_strength *= 0.95^(days_since_access/30)`.
- [ ] **T3 — Archivierung:** Engrams mit Strength < 0.05: `layer → 'archived'` in Dictionary. Neo4j: `archived=true` Property setzen. Qdrant: Payload-Update `{"archived": true}`. Links bleiben erhalten (Schema Formation kann archived Engrams noch berücksichtigen).
- [ ] **T4 — Batch Processing:** In Batches von 200 verarbeiten. Transaktion pro Batch (Fehler in einem Batch blockiert nicht andere). Logging: `{total, decayed, archived, unchanged, errors}`.
- [ ] **T5 — Unit Tests:** Decay-Formel mit verschiedenen Strength/Access-Kombinationen. Archivierung bei Threshold. Frequency-Bonus dämpft Decay. Alte Engrams decayen stärker.
