# Story 01 — Session Cache Layer

## User Story

Als System brauche ich eine transiente Cache-Schicht die Session-spezifische Daten hält und bei Session-Ende geflushed wird, damit Working Memory von kurzlebigen Session-Daten getrennt ist.

## Kontext

Bisher wurde alles im Working Context gehalten und bei Session-Ende gelöscht. Der Session Cache übernimmt die transiente Rolle: er hält Daten die nur innerhalb einer Session relevant sind (Episodic Buffer, pending Inferences, Co-Activation Counts). Bei Session-Ende werden diese Daten verarbeitet und der Cache geleert.

## Bestehende Codebasis

- **WorkingContext:** `engine/session/working_context.py` — Hält aktuell alles (transient + persistent). Wird aufgeteilt.
- **SessionManager:** `engine/session/session_manager.py` — Session Lifecycle.

## Akzeptanzkriterien

- [ ] SessionCache Dataclass mit: episodic_buffer, pending_inferences, co_activation_counts, created_at
- [ ] SessionCache ist an genau eine Session gebunden (session_id)
- [ ] SessionCache wird bei Session-Start erzeugt (leer)
- [ ] SessionCache wird bei Session-Ende geflushed (Daten verarbeitet, Cache geleert)
- [ ] Episodic Buffer: append-only innerhalb der Session
- [ ] Pending Inferences: status-tracking (tentative → confirmed/rejected)
- [ ] SessionCache ist NICHT persistiert — rein in-memory

## Tasks

- [x] **T1 — SessionCache Dataclass:** Felder: session_id, episodic_buffer (list[Episode]), pending_inferences (list[Inference]), co_activation_counts (dict[tuple[str,str], int]), created_at. Methoden: add_episode(), add_inference(), update_inference_status(), record_co_activation().
- [x] **T2 — Cache Lifecycle:** SessionManager.create_session() → erstellt SessionCache. SessionManager.end_session() → ruft flush() auf, löscht Cache. Cache existiert nur so lange wie die Session.
- [x] **T3 — WorkingContext Aufspaltung:** Bestehende transiente Felder aus WorkingContext in SessionCache verschieben. WorkingContext behält nur persistente Felder (Goal Stack, Active Engrams, Confirmed Inferences).
- [x] **T4 — Tests:** Cache-Lifecycle Test (create → add → flush → empty). Cache ist nach Session-Ende leer. Episodic Buffer append-only. Inference Status Tracking.
