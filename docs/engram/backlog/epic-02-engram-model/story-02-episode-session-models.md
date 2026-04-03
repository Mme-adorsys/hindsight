# Story 02 — Episode & Session Models

## User Story

Als System brauche ich Episode und Session als Datenmodelle, damit der Input ins Memory-System (Episode) und die aktuelle Steuerung (Session mit Mode) klar definiert sind.

## Kontext

Hindsight hat aktuell kein Episode- oder Session-Konzept. Input kommt als unstrukturierter Text via `retain_batch_async(content)`. Für die Engram-Architektur brauchen wir: Episode als strukturierten Input (action + context + outcome) und Session als transientes Steuerungsobjekt (mode, expectation, task_context). Session wird NICHT persistiert — sie lebt im Application Layer während einer Agent-Session.

## Bestehende Codebasis

- **Retain Input:** `hindsight_api/engine/retain/types.py` → `RetainContent { content: str, metadata: dict }`. Episode strukturiert diesen Input.
- **Retain Orchestrator:** `hindsight_api/engine/retain/orchestrator.py` — Nimmt RetainContent entgegen. Muss perspektivisch auch Episode akzeptieren.
- **MemoryEngine:** `hindsight_api/engine/memory_engine.py` → `retain_batch_async()` akzeptiert Text + Metadata. Session wird als optionaler Parameter hier durchgereicht.
- **Interface:** `hindsight_api/engine/interface.py` → MemoryEngineInterface. Session als optionaler Parameter bei retain/recall/reflect.
- **Config:** `hindsight_api/config.py` — Session-Defaults (z.B. default Mode) könnten hier definiert werden.

## Akzeptanzkriterien

- [ ] Episode Modell mit action, context, outcome definiert
- [ ] Session Modell mit mode, expectation, task_context definiert
- [ ] 4 Modi als Enum: Precision, Exploration, Analogy, Validation
- [ ] Session ist transient — kein ORM Model, kein DB-Schema
- [ ] Default-Session existiert (Mode=Precision, keine Expectation)
- [ ] Episode kann optional statt RetainContent an Retain-Pipeline übergeben werden
- [ ] Bestehender Text-Input (RetainContent) funktioniert weiterhin

## Tasks

- [ ] **T1 — RetrievalMode Enum definieren:** In `hindsight_api/engine/response_models.py` (oder neues Modul `hindsight_api/engine/types.py`): `RetrievalMode(Enum): PRECISION, EXPLORATION, ANALOGY, VALIDATION`. Default: PRECISION.
- [ ] **T2 — Episode Model definieren:** Pydantic Model: `Episode { action: str, context: str, outcome: str, timestamp: Optional[datetime] = None, metadata: Optional[dict] = None }`. Dazu Helper-Methode `to_retain_content() → RetainContent` die Episode in das bestehende Format konvertiert (action + context + outcome als strukturierter Text).
- [ ] **T3 — Session Model definieren:** Pydantic Model (NICHT SQLAlchemy — transient): `Session { mode: RetrievalMode = RetrievalMode.PRECISION, current_expectation: Optional[str] = None, task_context: Optional[str] = None, session_id: UUID = Field(default_factory=uuid4), started_at: datetime = Field(default_factory=datetime.utcnow) }`. Factory-Methode `Session.default()` für Standard-Session.
- [ ] **T4 — Session als optionaler Parameter:** In `hindsight_api/engine/interface.py` die Signaturen von `retain_batch_async`, `recall_async`, `reflect_async` um optionalen Parameter `session: Optional[Session] = None` erweitern. In `memory_engine.py` den Parameter durchreichen (noch keine Logik — die kommt in Epic 06+07).
- [ ] **T5 — Unit Tests:** Episode Erstellung + to_retain_content() Konvertierung. Session Defaults. RetrievalMode Enum Werte. Session als Parameter an Interface-Methoden (Signatur-Check).
