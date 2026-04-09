"use client";

import { useState, useEffect, useCallback, useMemo, Fragment, type ComponentType } from "react";
import { useBank } from "@/lib/bank-context";
import { client } from "@/lib/api";
import type { NCRRunHistoryItem, PipelineTrace } from "@/lib/api";
import { PipelineTraceView } from "./pipeline-trace-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  RefreshCw,
  Play,
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Activity,
  TrendingDown,
  TrendingUp,
  Layers,
} from "lucide-react";

const COOLDOWN_WARNING_MS = 5 * 60 * 1000; // 5 minutes — soft warning only
const HISTORY_LIMIT = 20;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const rest = Math.round(seconds - mins * 60);
  return `${mins}m ${rest}s`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRelative(iso: string | null): string {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  if (diffMs < 0) return "just now";
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
}

function numFromStats(stats: Record<string, unknown> | null, key: string): number {
  if (!stats) return 0;
  const v = stats[key];
  return typeof v === "number" ? v : 0;
}

function runSummaryLine(run: NCRRunHistoryItem): string {
  const parts: string[] = [];
  if (run.consolidation_stats)
    parts.push(`${numFromStats(run.consolidation_stats, "consolidated")} consolidated`);
  if (run.decay_stats) parts.push(`${numFromStats(run.decay_stats, "decayed")} decayed`);
  if (run.strengthen_stats)
    parts.push(`${numFromStats(run.strengthen_stats, "promoted")} promoted`);
  if (run.schema_stats) parts.push(`${numFromStats(run.schema_stats, "created")} schemas`);
  return parts.length > 0 ? parts.join(" · ") : "no changes";
}

// ---------------------------------------------------------------------------
// Summary Card
// ---------------------------------------------------------------------------

interface SummaryCardProps {
  title: string;
  icon: ComponentType<{ className?: string }>;
  accent: string;
  metrics: { label: string; value: number | string }[];
}

function SummaryCard({ title, icon: Icon, accent, metrics }: SummaryCardProps) {
  return (
    <div className={`bg-gradient-to-br ${accent} border rounded-xl p-4 h-full`}>
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1.5 rounded-lg bg-foreground/5">
          <Icon className="w-4 h-4" />
        </div>
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          {title}
        </p>
      </div>
      <div className="space-y-1.5">
        {metrics.map((m) => (
          <div key={m.label} className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-muted-foreground">{m.label}</span>
            <span className="text-sm font-semibold font-mono">{m.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function NCRDashboardView() {
  const { currentBank } = useBank();

  const [history, setHistory] = useState<NCRRunHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const loadHistory = useCallback(
    async (options: { silent?: boolean } = {}) => {
      if (!currentBank) return;
      if (!options.silent) setLoading(true);
      try {
        const data = await client.getNCRHistory(currentBank, HISTORY_LIMIT);
        setHistory(data.runs || []);
        setError(null);
      } catch (err) {
        console.error("Failed to load NCR history:", err);
        setError(err instanceof Error ? err.message : "Failed to load NCR history");
      } finally {
        setLoading(false);
      }
    },
    [currentBank]
  );

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const handleTrigger = async () => {
    if (!currentBank) return;
    setDialogOpen(false);
    setTriggering(true);
    setError(null);
    try {
      await client.triggerNCR(currentBank);
      // Refresh history to pick up the newly persisted run
      await loadHistory({ silent: true });
    } catch (err) {
      console.error("Failed to trigger NCR:", err);
      setError(err instanceof Error ? err.message : "Failed to trigger NCR");
    } finally {
      setTriggering(false);
    }
  };

  const toggleExpanded = (runId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  };

  const lastRun: NCRRunHistoryItem | null = history[0] ?? null;

  const cooldownWarning = useMemo(() => {
    if (!lastRun) return null;
    const diffMs = Date.now() - new Date(lastRun.started_at).getTime();
    if (diffMs >= 0 && diffMs < COOLDOWN_WARNING_MS) {
      const secondsAgo = Math.floor(diffMs / 1000);
      const minsAgo = Math.floor(secondsAgo / 60);
      return minsAgo > 0 ? `${minsAgo}m ${secondsAgo % 60}s` : `${secondsAgo}s`;
    }
    return null;
  }, [lastRun]);

  return (
    <div className="space-y-6">
      {/* Trigger Section */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-lg">
            <RefreshCw className="w-5 h-5 text-primary" />
            Run NCR
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            The Nightly Consolidation Run executes all consolidation phases (Decay → Strengthen →
            Schema Compression). It can take several minutes.
          </p>

          {cooldownWarning && (
            <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-500/30 bg-amber-500/10">
              <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Last run was {cooldownWarning} ago. Running NCR too frequently may not produce
                meaningful changes. The server also rate-limits manual triggers to once per hour.
              </p>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 p-3 rounded-lg border border-red-500/30 bg-red-500/10">
              <AlertTriangle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
              <p className="text-xs text-red-700 dark:text-red-400 break-words">{error}</p>
            </div>
          )}

          <AlertDialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <AlertDialogTrigger asChild>
              <Button disabled={triggering || !currentBank} className="gap-2">
                {triggering ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Running NCR...
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4" />
                    Run NCR Now
                  </>
                )}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Run Nightly Consolidation?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will run the full consolidation pipeline for{" "}
                  <span className="font-mono">{currentBank}</span>. It can take several minutes and
                  temporarily locks the bank for other NCR runs. Continue?
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={handleTrigger}>Run NCR</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </CardContent>
      </Card>

      {/* Last Run Summary */}
      <div>
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          Last Run Summary
          {lastRun && (
            <span className="ml-2 text-xs font-normal normal-case text-muted-foreground">
              — {formatRelative(lastRun.started_at)}
            </span>
          )}
        </h2>
        {loading && !lastRun ? (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 animate-pulse">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-28 rounded-xl bg-muted" />
            ))}
          </div>
        ) : !lastRun ? (
          <Card>
            <CardContent className="pt-6">
              <p className="text-sm text-muted-foreground">
                No NCR runs yet. Trigger one to see results.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <SummaryCard
              title="Consolidation"
              icon={Activity}
              accent="from-blue-500/10 to-blue-600/5 border-blue-500/20"
              metrics={[
                { label: "total", value: numFromStats(lastRun.consolidation_stats, "total") },
                {
                  label: "consolidated",
                  value: numFromStats(lastRun.consolidation_stats, "consolidated"),
                },
              ]}
            />
            <SummaryCard
              title="Decay"
              icon={TrendingDown}
              accent="from-rose-500/10 to-rose-600/5 border-rose-500/20"
              metrics={[
                { label: "total", value: numFromStats(lastRun.decay_stats, "total") },
                { label: "decayed", value: numFromStats(lastRun.decay_stats, "decayed") },
                { label: "archived", value: numFromStats(lastRun.decay_stats, "archived") },
              ]}
            />
            <SummaryCard
              title="Strengthen"
              icon={TrendingUp}
              accent="from-emerald-500/10 to-emerald-600/5 border-emerald-500/20"
              metrics={[
                { label: "total", value: numFromStats(lastRun.strengthen_stats, "total") },
                { label: "promoted", value: numFromStats(lastRun.strengthen_stats, "promoted") },
              ]}
            />
            <SummaryCard
              title="Schema"
              icon={Layers}
              accent="from-purple-500/10 to-purple-600/5 border-purple-500/20"
              metrics={[
                { label: "created", value: numFromStats(lastRun.schema_stats, "created") },
                {
                  label: "strengthened",
                  value: numFromStats(lastRun.schema_stats, "strengthened"),
                },
                { label: "deleted", value: numFromStats(lastRun.schema_stats, "deleted") },
              ]}
            />
          </div>
        )}
      </div>

      {/* Run History Table */}
      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between">
          <CardTitle className="text-lg">Run History</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => loadHistory({ silent: true })}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {loading && history.length === 0 ? (
            <div className="space-y-2 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 rounded-lg bg-muted" />
              ))}
            </div>
          ) : history.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No NCR runs recorded yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Started</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Summary</TableHead>
                  <TableHead className="w-20 text-right">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((run) => {
                  const isOpen = expanded.has(run.run_id);
                  const hasErrors = (run.errors?.length ?? 0) > 0;
                  return (
                    <Fragment key={run.run_id}>
                      <TableRow
                        className="cursor-pointer"
                        onClick={() => toggleExpanded(run.run_id)}
                      >
                        <TableCell>
                          {isOpen ? (
                            <ChevronDown className="w-4 h-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {formatTimestamp(run.started_at)}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {formatDuration(run.duration_seconds)}
                        </TableCell>
                        <TableCell>
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wide border ${
                              run.trigger === "manual"
                                ? "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20"
                                : "bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20"
                            }`}
                          >
                            {run.trigger}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {runSummaryLine(run)}
                        </TableCell>
                        <TableCell className="text-right">
                          {hasErrors ? (
                            <span
                              className="inline-flex items-center gap-1 text-red-500"
                              title={`${run.errors?.length} error(s)`}
                            >
                              <AlertTriangle className="w-3.5 h-3.5" />
                              <span className="text-xs font-semibold">{run.errors?.length}</span>
                            </span>
                          ) : (
                            <CheckCircle className="w-3.5 h-3.5 text-emerald-500 inline" />
                          )}
                        </TableCell>
                      </TableRow>
                      {isOpen && (
                        <TableRow>
                          <TableCell />
                          <TableCell colSpan={5}>
                            <div className="space-y-3 py-2">
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                                <div>
                                  <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                    Consolidation
                                  </p>
                                  <pre className="font-mono text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                                    {JSON.stringify(run.consolidation_stats, null, 2) || "—"}
                                  </pre>
                                </div>
                                <div>
                                  <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                    Decay
                                  </p>
                                  <pre className="font-mono text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                                    {JSON.stringify(run.decay_stats, null, 2) || "—"}
                                  </pre>
                                </div>
                                <div>
                                  <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                    Strengthen
                                  </p>
                                  <pre className="font-mono text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                                    {JSON.stringify(run.strengthen_stats, null, 2) || "—"}
                                  </pre>
                                </div>
                                <div>
                                  <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                    Schema
                                  </p>
                                  <pre className="font-mono text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                                    {JSON.stringify(run.schema_stats, null, 2) || "—"}
                                  </pre>
                                </div>
                                {run.promotion_stats && (
                                  <div className="md:col-span-2">
                                    <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                      Promotion (Shared Bank)
                                    </p>
                                    <pre className="font-mono text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                                      {JSON.stringify(run.promotion_stats, null, 2)}
                                    </pre>
                                  </div>
                                )}
                              </div>
                              {hasErrors && (
                                <div className="p-3 rounded-lg border border-red-500/30 bg-red-500/10">
                                  <p className="text-xs font-semibold text-red-700 dark:text-red-400 mb-1">
                                    Errors ({run.errors?.length})
                                  </p>
                                  <ul className="space-y-1">
                                    {run.errors?.map((e, idx) => (
                                      <li
                                        key={idx}
                                        className="text-[11px] text-red-700 dark:text-red-400 font-mono"
                                      >
                                        • {e}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {run.trace_data && (
                                <div>
                                  <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">
                                    Pipeline Trace
                                  </p>
                                  <PipelineTraceView
                                    trace={run.trace_data as unknown as PipelineTrace}
                                    compact
                                  />
                                </div>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
