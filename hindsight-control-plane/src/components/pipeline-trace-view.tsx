"use client";

import { useState } from "react";
import type { PipelineTrace, PipelineTraceStep } from "@/lib/api";

/**
 * Observability Phase B, Item B7 — renders a PipelineTrace as a vertical
 * timeline with one row per TraceStep. Designed to be reused from:
 *   - Memory Detail Panel (retain traces)
 *   - NCR Dashboard (consolidation traces)
 *   - Search Debug View (recall traces)
 *
 * Each row: status dot + step name + duration badge + rationale sub-line,
 * with an expand toggle that reveals the raw inputs/outputs JSON.
 */

interface PipelineTraceViewProps {
  trace: PipelineTrace;
  /** Compact mode shrinks padding and hides the header — useful inside dashboards. */
  compact?: boolean;
}

function statusClasses(status: PipelineTraceStep["status"]): string {
  switch (status) {
    case "error":
      return "bg-red-500";
    case "skipped":
      return "bg-gray-400 dark:bg-gray-500";
    case "ok":
    default:
      return "bg-emerald-500";
  }
}

function formatDuration(ms: number): string {
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function pipelineBadgeClasses(pipeline: PipelineTrace["pipeline"]): string {
  switch (pipeline) {
    case "retain":
      return "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30";
    case "recall":
      return "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/30";
    case "ncr":
      return "bg-purple-500/10 text-purple-700 dark:text-purple-300 border-purple-500/30";
    default:
      return "bg-gray-500/10 text-gray-700 dark:text-gray-300 border-gray-500/30";
  }
}

function TraceStepRow({ step }: { step: PipelineTraceStep }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails =
    (step.inputs && Object.keys(step.inputs).length > 0) ||
    (step.outputs && Object.keys(step.outputs).length > 0);

  return (
    <div className="border-l-2 border-muted pl-4 py-2 relative">
      <span
        className={`absolute -left-[7px] top-3 w-3 h-3 rounded-full ${statusClasses(
          step.status
        )} ring-2 ring-background`}
        aria-hidden
      />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-sm font-semibold truncate">{step.name}</span>
          <span
            className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
              step.status === "error"
                ? "bg-red-500/10 text-red-700 dark:text-red-300 border border-red-500/30"
                : step.status === "skipped"
                  ? "bg-gray-500/10 text-gray-600 dark:text-gray-400 border border-gray-500/30"
                  : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30"
            }`}
          >
            {step.status}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs tabular-nums text-muted-foreground">
            {formatDuration(step.duration_ms)}
          </span>
          {hasDetails && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
            >
              {expanded ? "hide" : "details"}
            </button>
          )}
        </div>
      </div>

      {step.rationale && (
        <div className="text-xs text-muted-foreground mt-1 flex gap-1.5">
          <span className="text-muted-foreground/60">↳</span>
          <span>{step.rationale}</span>
        </div>
      )}

      {step.error && (
        <div className="text-xs text-red-600 dark:text-red-400 mt-1 font-mono break-all">
          {step.error}
        </div>
      )}

      {expanded && hasDetails && (
        <div className="mt-2 space-y-2">
          {Object.keys(step.inputs).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                Inputs
              </div>
              <pre className="text-[11px] bg-muted/40 rounded p-2 overflow-x-auto font-mono">
                {JSON.stringify(step.inputs, null, 2)}
              </pre>
            </div>
          )}
          {Object.keys(step.outputs).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                Outputs
              </div>
              <pre className="text-[11px] bg-muted/40 rounded p-2 overflow-x-auto font-mono">
                {JSON.stringify(step.outputs, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PipelineTraceView({ trace, compact = false }: PipelineTraceViewProps) {
  const stepCount = trace.steps?.length ?? 0;
  const errorCount = trace.steps?.filter((s) => s.status === "error").length ?? 0;
  const totalDuration = trace.duration_ms ?? 0;

  return (
    <div className={`rounded-lg border bg-card ${compact ? "p-3" : "p-4"}`}>
      {!compact && (
        <div className="flex items-center justify-between mb-3 pb-3 border-b">
          <div className="flex items-center gap-2">
            <span
              className={`text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded border ${pipelineBadgeClasses(
                trace.pipeline
              )}`}
            >
              {trace.pipeline}
            </span>
            <span className="text-xs text-muted-foreground">{stepCount} steps</span>
            {errorCount > 0 && (
              <span className="text-xs text-red-600 dark:text-red-400">
                · {errorCount} error{errorCount === 1 ? "" : "s"}
              </span>
            )}
          </div>
          <span className="text-xs tabular-nums text-muted-foreground">
            {formatDuration(totalDuration)}
          </span>
        </div>
      )}

      {stepCount === 0 ? (
        <div className="text-xs text-muted-foreground italic text-center py-4">
          No steps recorded
        </div>
      ) : (
        <div className="space-y-0">
          {trace.steps.map((step, idx) => (
            <TraceStepRow key={`${step.name}-${idx}`} step={step} />
          ))}
        </div>
      )}

      {compact && (
        <div className="flex items-center justify-between mt-2 pt-2 border-t text-xs text-muted-foreground">
          <span>
            {stepCount} steps
            {errorCount > 0 && (
              <span className="text-red-600 dark:text-red-400">
                {" "}
                · {errorCount} error{errorCount === 1 ? "" : "s"}
              </span>
            )}
          </span>
          <span className="tabular-nums">{formatDuration(totalDuration)}</span>
        </div>
      )}
    </div>
  );
}
