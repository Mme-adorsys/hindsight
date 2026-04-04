# Story 02 — Session Lifecycle & Dual Control

## User Story

Als Agent möchte ich eine Session starten, den Mode explizit setzen können, und automatische Mode-Shifts basierend auf System-Signalen erhalten.

## Kontext

Die Session ist transient — sie wird bei Beginn einer Agent-Interaktion erzeugt und bei Ende verworfen. Dual Control bedeutet: Der Mode kann explizit gesetzt werden (Agent/User entscheidet) UND das System kann den Mode automatisch shiften basierend auf Signalen (Surprise, schwache Matches, Widersprüche). Automatische Shifts sind Vorschläge — sie überschreiben den expliziten Mode nur wenn kein expliziter gesetzt wurde.

## Bestehende Codebasis

- **Session Dataclass:** `hindsight_api/engine/retain/types.py` (aus Epic 02) — `Session` mit `mode: RetrievalMode`, `task_context: str`, `current_expectation: str | None`.
- **Episode Dataclass:** `hindsight_api/engine/retain/types.py` (aus Epic 02) — `Episode` mit `action`, `context`, `outcome`.
- **ModeConfig:** `hindsight_api/engine/session/mode_config.py` (aus Story 01) — Profil pro Mode.

## Akzeptanzkriterien

- [x] SessionManager erstellt neue Sessions mit Default-Mode (Precision)
- [x] Expliziter Mode-Set überschreibt automatischen Mode
- [x] Automatische Mode-Shift Signale werden verarbeitet: high_surprise → Validation, weak_matches → Exploration, contradiction → Validation
- [x] Session hält Episode-Historie (episodic_buffer) für die Dauer der Session
- [x] Session Mode-History wird geloggt (für Debugging, nicht persistiert)
- [x] Session ist thread-safe (concurrent Agent-Requests auf dieselbe Session)

## Tasks

- [x] **T1 — SessionManager Klasse:** Neues Modul `hindsight_api/engine/session/session_manager.py`. Klasse `SessionManager` mit: `create_session(mode=RetrievalMode.PRECISION, task_context="", expectation=None) → Session`, `get_session(session_id) → Session`, `end_session(session_id)`. Sessions in-memory Dict (nicht persistiert). Session-ID als UUID.
- [x] **T2 — Explicit Mode Control:** `SessionManager.set_mode(session_id, mode: RetrievalMode)`. Setzt `session.mode` und `session._explicit_mode = True`. Wenn explicit, werden automatische Shifts geblockt (nur geloggt, nicht angewendet).
- [x] **T3 — Automatic Mode Shift Engine:** `SessionManager.process_signal(session_id, signal: ModeSignal)`. Enum `ModeSignal`: `HIGH_SURPRISE`, `WEAK_MATCHES`, `CONTRADICTION`, `PREDICTION_ERROR`, `STRONG_MATCHES`. Mapping: HIGH_SURPRISE → Validation, WEAK_MATCHES → Exploration, CONTRADICTION → Validation, PREDICTION_ERROR → Validation, STRONG_MATCHES → Precision. Nur anwenden wenn `_explicit_mode == False`.
- [x] **T4 — Episode Buffer:** Session hält `episodes: list[Episode]` als In-Memory Buffer. `SessionManager.add_episode(session_id, episode: Episode)`. Wird bei Session-Ende verworfen (relevante Episodes fließen über Retain in Engrams).
- [x] **T5 — Mode History Log:** Session hält `mode_history: list[ModeTransition]` mit `ModeTransition(from_mode, to_mode, trigger, timestamp)`. Für Debugging und Metrics. Nicht persistiert.
- [x] **T6 — Thread Safety:** Session-Operations mit asyncio.Lock pro Session. Concurrent Mode-Sets und Signal-Processing dürfen sich nicht überschreiben. Test: Parallele set_mode und process_signal Aufrufe.
- [x] **T7 — Unit Tests:** Session erstellen mit Default-Mode. Explicit Mode-Set. Automatic Shift bei HIGH_SURPRISE. Explicit Mode blockt Automatic Shift. Episode Buffer Lifecycle. Mode History korrekt aufgezeichnet.
