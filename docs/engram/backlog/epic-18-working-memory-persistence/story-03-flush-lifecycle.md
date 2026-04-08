# Story 03 — Flush-Prozess & Session Lifecycle

## User Story

Als System will ich bei Session-Ende den Cache-Inhalt korrekt verarbeiten — transiente Daten in die Retain Pipeline schicken und persistente Daten im Working Memory aktualisieren.

## Kontext

Der Flush-Prozess ist die Brücke zwischen transientem Cache und persistentem Working Memory. Er entscheidet was wohin fließt:
- Episodic Buffer → Retain Pipeline (wird zu Engrams)
- Confirmed Inferences → Working Memory (überlebt)
- Tentative Inferences → Working Memory mit niedrigem Confidence (überlebt mit Warnung)
- Rejected Inferences → verworfen
- Co-Activation Counts → Neo4j Links (wird zu Graph-Struktur)
- Active Engram Tiers → Working Memory (werden beibehalten, ggf. mit Decay)

## Bestehende Codebasis

- **SessionCache:** Aus Story 01 (transiente Schicht).
- **WorkingMemory:** Aus Story 02 (persistente Schicht).
- **Retain Pipeline:** `engine/retain/orchestrator.py` — Episodic Buffer Einträge fließen hier rein.
- **Neo4j Link Writer:** `engine/retain/neo4j_link_writer.py` — Co-Activation Links.

## Akzeptanzkriterien

- [x] flush() Methode auf MemoryEngine (flush_session_async) die Cache → WM + Retain Pipeline verarbeitet
- [x] Episodic Buffer Einträge werden als RetainContentDicts an Retain Pipeline übergeben
- [x] Inferences: confirmed → WM.confirmed_inferences, tentative → WM mit confidence * 0.5, rejected → verworfen
- [x] Co-Activation Counts → Neo4j CO_ACTIVATED Links (batch write, normalized weight)
- [x] Active Engrams Tiers: aktuelle Tier-Zuordnung wird in WM übernommen (via end_session_async)
- [x] Session ID wird zu WM.session_history hinzugefügt (FIFO, max 20, via end_session_async)
- [x] Error resilience: Retain/Neo4j failure = warning, WM-Save bleibt bestehen
- [x] Metriken: flushed_episodes, flushed_inferences, flushed_co_activations im Return-Dict

## Tasks

- [x] **T1 — Flush-Orchestrator:** `MemoryEngine.flush_session_async(session_id, bank_id, request_context)`. Reihenfolge: 1) Inferences verarbeiten, 2) Active Engrams + WM-Save via end_session_async, 3) Episodic Buffer → Retain Pipeline, 4) Co-Activation → Neo4j, 5) Cache geleert via end_session.
- [x] **T2 — Inference-Verarbeitung:** Confirmed → direkt in WM. Tentative → confidence *= 0.5. Rejected → nicht in WM. Dedup: höhere Confidence gewinnt.
- [x] **T3 — Episodic Buffer → Retain:** Episode.to_retain_content() → retain_batch_async(). Fehler loggen, Flush nicht abbrechen.
- [x] **T4 — Co-Activation → Neo4j:** _co_activation_to_links() normalisiert auf 0.0-1.0. write_links_to_neo4j() als CO_ACTIVATED. Best-effort, Fehler = Warning.
- [x] **T5 — Error Handling:** Retain/Neo4j Fehler → Warning loggen, WM-Save unberührt. _get_neo4j_client() gibt None wenn nicht konfiguriert → graceful skip.
- [x] **T6 — Tests:** 20 Unit-Tests: Inference-Routing, Dedup, Normalisierung, Episodic→Retain, Co-Activation→Neo4j, Fehler-Resilience (Retain down, Neo4j down), kein Neo4j-Client.
