"""
Unit tests for the generic :class:`PipelineTracer` used by retain/recall/NCR
pipelines (Observability Phase B, Item B1).

Covers:
- Step timing + output capture
- Exception handling (status=error, re-raise)
- Sequential flat schema (nested ``with`` blocks still produce flat steps)
- ``finalize()`` returns a snapshot that is unaffected by later mutation
- ``to_dict()`` is JSON-serializable end-to-end
- Disabled tracer is a no-op (no steps recorded, ``with`` block still runs)
"""

from __future__ import annotations

import json
import time

import pytest

from hindsight_api.engine.tracer import PipelineTracer, TraceStepContext


def test_step_captures_timing_and_output() -> None:
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")

    with tracer.step("r1_fact_extraction") as s:
        assert isinstance(s, TraceStepContext)
        s.set_input({"num_contents": 3})
        time.sleep(0.005)
        s.set_output({"num_facts": 8})
        s.set_rationale("LLM extracted 8 facts from 3 contents")

    trace = tracer.finalize()
    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.name == "r1_fact_extraction"
    assert step.phase == "retain"
    assert step.status == "ok"
    assert step.inputs == {"num_contents": 3}
    assert step.outputs == {"num_facts": 8}
    assert step.rationale == "LLM extracted 8 facts from 3 contents"
    assert step.error is None
    # Timer must have progressed — allow for a generous floor to be robust on CI.
    assert step.duration_ms >= 1.0


def test_step_context_manager_catches_error() -> None:
    tracer = PipelineTracer(pipeline="ncr", bank_id="test-bank")

    with pytest.raises(RuntimeError, match="boom"):
        with tracer.step("phase2_strengthen") as s:
            s.set_input({"num_buffer": 10})
            raise RuntimeError("boom")

    trace = tracer.finalize()
    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.status == "error"
    assert step.error is not None
    assert "RuntimeError" in step.error
    assert "boom" in step.error
    # Input recorded before the exception should still be present.
    assert step.inputs == {"num_buffer": 10}
    # Duration was still measured.
    assert step.duration_ms >= 0.0


def test_nested_steps_are_flattened_not_nested() -> None:
    """Phase B uses a flat schema — nested ``with`` blocks still produce siblings."""
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")

    with tracer.step("outer"):
        with tracer.step("inner"):
            pass

    trace = tracer.finalize()
    assert [s.name for s in trace.steps] == ["inner", "outer"]
    # Both recorded, no hierarchy on the TraceStep itself.
    assert all(s.phase == "retain" for s in trace.steps)


def test_finalize_returns_snapshot_unaffected_by_later_mutation() -> None:
    tracer = PipelineTracer(pipeline="recall", bank_id="test-bank")

    with tracer.step("query_analysis") as s:
        s.set_output({"intent": "factual"})

    trace = tracer.finalize()
    assert len(trace.steps) == 1

    # Mutating the tracer after finalize must not leak into the snapshot.
    with tracer.step("retrieval_parallel") as s:
        s.set_output({"num_results": 5})

    assert len(trace.steps) == 1
    assert trace.steps[0].name == "query_analysis"
    assert trace.steps[0].outputs == {"intent": "factual"}


def test_to_dict_is_json_serializable() -> None:
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")
    tracer.set_metadata("operation_id", "op-123")

    with tracer.step("r5_insert") as s:
        s.set_input({"num_to_insert": 8})
        s.set_output({"unit_ids": ["a", "b", "c"]})
        s.set_rationale("persisted 8 engrams")

    payload = tracer.to_dict()
    # json.dumps must round-trip without raising.
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    assert decoded["pipeline"] == "retain"
    assert decoded["bank_id"] == "test-bank"
    assert decoded["metadata"] == {"operation_id": "op-123"}
    assert len(decoded["steps"]) == 1
    step = decoded["steps"][0]
    assert step["name"] == "r5_insert"
    assert step["status"] == "ok"
    assert step["inputs"] == {"num_to_insert": 8}
    assert step["outputs"] == {"unit_ids": ["a", "b", "c"]}
    assert isinstance(step["started_at"], str)
    assert isinstance(step["duration_ms"], (int, float))


def test_disabled_tracer_is_noop() -> None:
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank", enabled=False)
    assert tracer.enabled is False

    with tracer.step("r1_fact_extraction") as s:
        # Fluent setters must still work (returning self), but store nothing.
        s.set_input({"num_contents": 3}).set_output({"num_facts": 8}).set_rationale("x")

    trace = tracer.finalize()
    assert trace.steps == []
    assert trace.metadata == {}

    # to_dict also produces an empty step list.
    payload = tracer.to_dict()
    assert payload["steps"] == []


def test_mark_skipped_records_skipped_status() -> None:
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")

    with tracer.step("r0_sequence_analysis") as s:
        s.mark_skipped("no budget configured")

    trace = tracer.finalize()
    assert len(trace.steps) == 1
    assert trace.steps[0].status == "skipped"
    assert trace.steps[0].rationale == "no budget configured"


def test_tracer_respects_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINDSIGHT_API_TRACING_ENABLED", "false")
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")
    assert tracer.enabled is False

    monkeypatch.setenv("HINDSIGHT_API_TRACING_ENABLED", "true")
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank")
    assert tracer.enabled is True

    # Explicit argument beats env.
    monkeypatch.setenv("HINDSIGHT_API_TRACING_ENABLED", "false")
    tracer = PipelineTracer(pipeline="retain", bank_id="test-bank", enabled=True)
    assert tracer.enabled is True
