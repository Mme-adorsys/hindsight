# Epic 24 — Lifecycle Scoring Overhaul

> Neues Scoring-System: Thalamus-Geburtswert × Decay mit individueller Equilibrium Rate, sessions-basiertem Aging und bidirektionalem Lifecycle.

## Ziel

Das bestehende Scoring-System (recall_score + saliency_weight × saliency) wird durch ein biologisch inspiriertes Lifecycle-Scoring ersetzt. Der neue **Composite Score** (`thalamus_overall × decay`) ist die einzige Metrik für alle Lifecycle-Entscheidungen: Promotion, Archivierung und Reactivation. Der Decay basiert auf dem Verhältnis von tatsächlichen zu erwarteten Abrufen, mit einer individuellen Equilibrium Rate pro Engram. Das Aging wird von `op_count` auf `sessions_alive` umgestellt. Der Lifecycle wird bidirektional: archivierte Engrams können zurückkehren, Buffer-Engrams können ausaltern.

## Bestehende Codebasis

- **Consolidation Scoring:** `engine/consolidation/scoring.py` — Aktuell: `recall_score + SALIENCY_WEIGHT × saliency`. Wird komplett ersetzt.
- **NCR Decay:** `engine/consolidation/ncr_decay.py` — Aktuell: `strength × 0.9 × frequency_bonus`. Wird ersetzt durch neue Decay-Formel.
- **NCR Strengthen:** `engine/consolidation/ncr_strengthen.py` — Promote-Logik basiert auf altem Composite Score. Muss auf neuen Composite migrieren.
- **Thalamus Filter:** `engine/thalamus.py` — ThalamusScores (Novelty, Surprise, Task-Relevance, Emotional Valence). Bleibt bestehen, liefert den Geburtswert.
- **Engram Dictionary:** PostgreSQL `engram_dictionary` Tabelle — Hat `strength`, `access_count`, `last_accessed`. Braucht neue Felder.
- **Bank Model:** `models.py` — Hat `op_count`. Braucht `session_count`.
- **Recall Orchestrator:** `engine/recall_orchestrator.py` — Inkrementiert `access_count` bei Recall.
- **NCR Orchestrator:** `engine/ncr/ncr_orchestrator.py` — Koordiniert C1/C2 Phasen.

## Scope

- Neues Aging-Modell: `sessions_alive` ersetzt `cycles_alive` / `op_count`
- Equilibrium Rate `r` mit Demand/Protection-Pattern und Bank-Size-Normalisierung
- Neuer Composite Score: `thalamus_overall × decay`
- Tag-abhängige Promote-Thresholds und bankgrößen-normalisierte Hard Gates
- Bidirektionaler Lifecycle: Archive-Reactivation und Buffer-Aging
- Migration aller C1/C2 Phasen auf neue Formel

## Nicht in Scope

- Thalamus Filter selbst (bleibt unverändert aus Epic 04)
- C3 Schema Compression (bleibt aus Epic 13)
- C4 Shared Bank Promotion (bleibt aus Epic 14)
- Control Plane UI-Anpassungen (separates Epic)

## Abhängigkeiten

- Epic 04 (Thalamus Filter) — ThalamusScores werden als Geburtswert verwendet
- Epic 12 (Consolidation Pipeline) — C1/C2 Phasen werden migriert
- Epic 06 (Session Layer) — Session-Events für sessions_alive Tracking

## Referenzen

- `concept.md` → Kapitel 5 (Thalamus Filter & Engram Lifecycle Scoring)
- `concept.md` → Kapitel 12 (Consolidation Pipeline)
- `engram-lifecycle-scoring.md` → Standalone-Konzept mit Formel-Beispielen

## Stories

1. [Sessions-Alive Taktgeber](story-01-sessions-alive.md)
2. [Equilibrium Rate r](story-02-equilibrium-rate.md)
3. [Composite Score Migration](story-03-composite-score.md)
4. [Tag-abhängige Thresholds & normalisierte Hard Gates](story-04-thresholds-hardgates.md)
5. [Bidirektionaler Lifecycle](story-05-bidirectional-lifecycle.md)
6. [C1/C2 Phasen-Migration](story-06-phase-migration.md)
