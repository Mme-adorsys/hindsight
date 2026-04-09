"""
Generic PipelineTracer for retain / recall / NCR pipelines (Observability Phase B).

Captures per-step inputs, outputs, rationale, status, and duration into a flat
``PipelineTrace`` structure that can be JSON-serialized and persisted (for retain
and NCR) or returned inline (for recall).

Usage
-----
::

    tracer = PipelineTracer(pipeline="retain", bank_id=bank_id)
    with tracer.step("r1_fact_extraction") as s:
        s.set_input({"num_contents": len(contents)})
        facts = await extract_facts(contents)
        s.set_output({"num_facts": len(facts)})
        s.set_rationale(f"LLM extracted {len(facts)} facts")

    # On exception: status = "error", error = str(exc), duration recorded,
    # then the exception is re-raised.

    trace = tracer.finalize()
    payload = trace.to_dict()  # JSON-safe dict

Nested ``step()`` blocks are explicitly flattened — each ``step()`` produces its
own ``TraceStep`` entry regardless of stack depth. Phase B's UI timeline is flat
on purpose; if callers want logical grouping they can use a ``group`` prefix in
the step name (``"phase2.consolidate"``).

Disabling
---------
Pass ``enabled=False`` (or set ``HINDSIGHT_API_TRACING_ENABLED=false``) and the
tracer becomes a cheap no-op that still lets ``with`` blocks execute normally.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal

ENV_TRACING_ENABLED = "HINDSIGHT_API_TRACING_ENABLED"

StepStatus = Literal["ok", "error", "skipped"]
PipelineKind = Literal["retain", "recall", "ncr"]


def _tracing_enabled_from_env() -> bool:
    """Return True unless HINDSIGHT_API_TRACING_ENABLED is explicitly set to a falsy value."""
    raw = os.getenv(ENV_TRACING_ENABLED)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass
class TraceStep:
    """A single step inside a pipeline trace."""

    name: str
    phase: str
    started_at: datetime
    duration_ms: float
    status: StepStatus = "ok"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    rationale: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phase": self.phase,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "rationale": self.rationale,
            "error": self.error,
        }


@dataclass
class PipelineTrace:
    """A complete pipeline trace with all recorded steps."""

    pipeline: PipelineKind
    bank_id: str
    started_at: datetime
    completed_at: datetime | None = None
    steps: list[TraceStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.completed_at is None:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds() * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "bank_id": self.bank_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
        }


class TraceStepContext:
    """Context manager returned by :meth:`PipelineTracer.step`.

    Captures timing automatically. Use :meth:`set_input`, :meth:`set_output`,
    and :meth:`set_rationale` inside the ``with`` block. On exception, the step
    is recorded with ``status="error"`` and the exception is re-raised.
    """

    __slots__ = (
        "_tracer",
        "_name",
        "_started_monotonic",
        "_started_at",
        "_inputs",
        "_outputs",
        "_rationale",
        "_status",
        "_error",
        "_noop",
    )

    def __init__(self, tracer: PipelineTracer | None, name: str, noop: bool) -> None:
        self._tracer = tracer
        self._name = name
        self._noop = noop
        self._started_monotonic: float = 0.0
        self._started_at: datetime = datetime.now(UTC)
        self._inputs: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}
        self._rationale: str | None = None
        self._status: StepStatus = "ok"
        self._error: str | None = None

    def __enter__(self) -> TraceStepContext:
        self._started_monotonic = time.monotonic()
        self._started_at = datetime.now(UTC)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        duration_ms = (time.monotonic() - self._started_monotonic) * 1000.0
        if exc is not None:
            self._status = "error"
            self._error = f"{exc_type.__name__ if exc_type else 'Exception'}: {exc}"
        if not self._noop and self._tracer is not None:
            self._tracer._record_step(
                TraceStep(
                    name=self._name,
                    phase=self._tracer.pipeline,
                    started_at=self._started_at,
                    duration_ms=duration_ms,
                    status=self._status,
                    inputs=dict(self._inputs),
                    outputs=dict(self._outputs),
                    rationale=self._rationale,
                    error=self._error,
                )
            )
        # Never suppress the exception.
        return False

    # --- Fluent setters -------------------------------------------------

    def set_input(self, data: dict[str, Any]) -> TraceStepContext:
        if not self._noop:
            self._inputs.update(data)
        return self

    def set_output(self, data: dict[str, Any]) -> TraceStepContext:
        if not self._noop:
            self._outputs.update(data)
        return self

    def set_rationale(self, text: str) -> TraceStepContext:
        if not self._noop:
            self._rationale = text
        return self

    def mark_skipped(self, reason: str) -> TraceStepContext:
        """Mark this step as skipped. Use when the step didn't actually run."""
        if not self._noop:
            self._status = "skipped"
            self._rationale = reason
        return self


class PipelineTracer:
    """Collects a flat list of :class:`TraceStep` entries for one pipeline run.

    Thread-safety: not thread-safe. Each pipeline invocation creates its own
    tracer instance.
    """

    def __init__(
        self,
        pipeline: PipelineKind,
        bank_id: str,
        enabled: bool | None = None,
    ) -> None:
        self.pipeline: PipelineKind = pipeline
        self.bank_id = bank_id
        self._enabled = enabled if enabled is not None else _tracing_enabled_from_env()
        self._started_at = datetime.now(UTC)
        self._completed_at: datetime | None = None
        self._steps: list[TraceStep] = []
        self._metadata: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def step(self, name: str) -> TraceStepContext:
        """Return a context manager that records a single step."""
        return TraceStepContext(self if self._enabled else None, name, noop=not self._enabled)

    def set_metadata(self, key: str, value: Any) -> None:
        if self._enabled:
            self._metadata[key] = value

    def record_step(
        self,
        name: str,
        duration_ms: float,
        *,
        started_at: datetime | None = None,
        status: StepStatus = "ok",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        rationale: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record a pre-computed TraceStep directly.

        Complementary to :meth:`step` — use this when the step body already
        has its own timing/logging and wrapping it in a ``with`` block would
        force unwanted indentation churn. The caller is responsible for
        passing an accurate ``duration_ms``.
        """
        if not self._enabled:
            return
        self._steps.append(
            TraceStep(
                name=name,
                phase=self.pipeline,
                started_at=started_at or datetime.now(UTC),
                duration_ms=duration_ms,
                status=status,
                inputs=inputs or {},
                outputs=outputs or {},
                rationale=rationale,
                error=error,
            )
        )

    def _record_step(self, step: TraceStep) -> None:
        self._steps.append(step)

    def finalize(self) -> PipelineTrace:
        """Freeze the trace by setting completed_at and returning a copy.

        The returned :class:`PipelineTrace` is a deep-ish copy — ``steps`` and
        ``metadata`` are new lists/dicts, so later mutation on the tracer does
        not affect the returned snapshot. Step ``inputs``/``outputs`` are still
        dict references (values are expected to be immutable-ish primitives).
        """
        self._completed_at = datetime.now(UTC)
        return PipelineTrace(
            pipeline=self.pipeline,
            bank_id=self.bank_id,
            started_at=self._started_at,
            completed_at=self._completed_at,
            steps=[replace(s) for s in self._steps],
            metadata=dict(self._metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the current (possibly unfinalized) trace to a JSON-safe dict."""
        completed = self._completed_at or datetime.now(UTC)
        trace = PipelineTrace(
            pipeline=self.pipeline,
            bank_id=self.bank_id,
            started_at=self._started_at,
            completed_at=completed,
            steps=list(self._steps),
            metadata=dict(self._metadata),
        )
        # Round-trip through json to catch non-serializable values early at
        # callsites that explicitly ask for a dict.
        payload = trace.to_dict()
        json.dumps(payload, default=str)
        return payload
