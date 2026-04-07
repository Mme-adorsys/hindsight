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

- [ ] flush() Methode auf SessionManager die Cache → WM + Retain Pipeline verarbeitet
- [ ] Episodic Buffer Einträge werden als RetainContentDicts an Retain Pipeline übergeben
- [ ] Inferences: confirmed → WM.confirmed_inferences, tentative → WM mit confidence * 0.5, rejected → verworfen
- [ ] Co-Activation Counts → Neo4j CO_ACTIVATED Links (batch write)
- [ ] Active Engrams Tiers: aktuelle Tier-Zuordnung wird in WM übernommen
- [ ] Session ID wird zu WM.session_history hinzugefügt (FIFO, max 20)
- [ ] Flush ist atomisch: entweder komplett oder gar nicht (Fehler → Retry, kein Teilergebnis)
- [ ] Metriken: Anzahl geflusher Episoden, Inferences, Co-Activation Links

## Tasks

- [ ] **T1 — Flush-Orchestrator:** `session_manager.flush_session(session_id)` Methode. Reihenfolge: 1) Inferences verarbeiten, 2) Active Engrams übernehmen, 3) Session History updaten, 4) WM speichern, 5) Episodic Buffer → Retain Pipeline, 6) Co-Activation → Neo4j, 7) Cache löschen.
- [ ] **T2 — Inference-Verarbeitung:** Confirmed → direkt in WM. Tentative → confidence *= 0.5, dann in WM (Warnung loggen). Rejected → nicht in WM. Deduplizierung: wenn gleiche Inference bereits in WM → confidence updaten statt duplizieren.
- [ ] **T3 — Episodic Buffer → Retain:** Buffer-Einträge in RetainContentDicts konvertieren. An retain_batch_async() übergeben (async, non-blocking). Fehler loggen aber Flush nicht abbrechen.
- [ ] **T4 — Co-Activation → Neo4j:** Co-Activation Counts aus Cache → write_links_to_neo4j() als CO_ACTIVATED Links. Batch write. Weight = count / max_count (normalisiert auf 0.0-1.0).
- [ ] **T5 — Atomicity & Error Handling:** WM-Save in Transaction. Wenn Retain Pipeline oder Neo4j fehlschlägt → Warnung loggen, WM-Save bleibt bestehen. Retry-Logik für transiente Fehler.
- [ ] **T6 — Tests:** Flush End-to-End Test (Cache mit Daten → flush → WM korrekt, Cache leer). Inference-Status Routing. Episodic Buffer → Retain Aufrufe verifizieren. Co-Activation → Neo4j verifizieren. Fehler-Resilience Test (Retain Pipeline down → WM trotzdem gespeichert).
