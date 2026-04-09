"use client";

import { useState, useEffect, useCallback } from "react";
import { useBank } from "@/lib/bank-context";
import { client } from "@/lib/api";
import type { EngramStatsResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Layers, RefreshCw, ArrowRight } from "lucide-react";

const REFRESH_INTERVAL_MS = 30_000;
const PROMOTION_THRESHOLD = 0.4; // Epic 12 — strength required for neocortex promotion

type LayerKey = "working_memory" | "buffer" | "neocortex";

interface LayerMeta {
  key: LayerKey;
  label: string;
  description: string;
  accent: string; // tailwind gradient
  border: string;
  text: string;
  badge: string;
}

const LAYER_META: LayerMeta[] = [
  {
    key: "working_memory",
    label: "Working Memory",
    description: "Fresh inputs — not yet consolidated",
    accent: "from-slate-500/10 to-slate-600/5",
    border: "border-slate-500/20",
    text: "text-slate-500",
    badge: "bg-slate-500/20",
  },
  {
    key: "buffer",
    label: "Buffer",
    description: "Consolidated — waiting for NCR promotion",
    accent: "from-amber-500/10 to-amber-600/5",
    border: "border-amber-500/20",
    text: "text-amber-500",
    badge: "bg-amber-500/20",
  },
  {
    key: "neocortex",
    label: "Neocortex",
    description: "Long-term knowledge — NCR-promoted",
    accent: "from-emerald-500/10 to-emerald-600/5",
    border: "border-emerald-500/20",
    text: "text-emerald-500",
    badge: "bg-emerald-500/20",
  },
];

function formatPercent(count: number, total: number): string {
  if (total === 0) return "0%";
  return `${Math.round((count / total) * 100)}%`;
}

function formatStrength(value: number): string {
  return value.toFixed(2);
}

export function EngramLifecycleView() {
  const { currentBank } = useBank();
  const [stats, setStats] = useState<EngramStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStats = useCallback(
    async (options: { silent?: boolean } = {}) => {
      if (!currentBank) return;
      if (options.silent) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      try {
        const data = await client.getEngramStats(currentBank);
        setStats(data);
        setError(null);
      } catch (err) {
        console.error("Failed to load engram stats:", err);
        setError(err instanceof Error ? err.message : "Failed to load engram stats");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [currentBank]
  );

  useEffect(() => {
    loadStats();
    const interval = setInterval(() => loadStats({ silent: true }), REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [loadStats]);

  if (loading && !stats) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Layers className="w-5 h-5 text-primary" />
            Engram Lifecycle
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4 animate-pulse">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-32 rounded-xl bg-muted" />
              ))}
            </div>
            <div className="h-10 rounded-xl bg-muted" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error && !stats) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-red-500">Error: {error}</p>
        </CardContent>
      </Card>
    );
  }

  if (!stats) return null;

  const { total, layers, strength_distribution } = stats;
  const distributionTotal =
    strength_distribution.weak + strength_distribution.moderate + strength_distribution.strong;

  return (
    <div className="space-y-4">
      {/* Header with refresh */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-semibold">Engram Lifecycle</h2>
          <span className="text-sm text-muted-foreground">
            · {total.toLocaleString()} total engrams
          </span>
        </div>
        <button
          type="button"
          onClick={() => loadStats({ silent: true })}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-50"
          title="Refresh now (auto-refresh every 30s)"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {/* Layer Cards Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {LAYER_META.map((meta, idx) => {
          const layer = layers[meta.key];
          const percent = formatPercent(layer.count, total);
          return (
            <div key={meta.key} className="relative">
              <div
                className={`bg-gradient-to-br ${meta.accent} border ${meta.border} rounded-xl p-4 h-full`}
              >
                <div className="flex items-center gap-2 mb-3">
                  <div className={`p-1.5 rounded-lg ${meta.badge}`}>
                    <Layers className={`w-4 h-4 ${meta.text}`} />
                  </div>
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    {meta.label}
                  </p>
                </div>
                <p className="text-2xl font-bold text-foreground">{layer.count.toLocaleString()}</p>
                <p className="text-xs text-muted-foreground mb-3">{percent} of total</p>

                {/* Avg strength bar */}
                <div className="space-y-1">
                  <div className="flex justify-between text-[10px] text-muted-foreground uppercase tracking-wide">
                    <span>Avg Strength</span>
                    <span className="font-mono">{formatStrength(layer.avg_strength)}</span>
                  </div>
                  <div className="relative h-2 rounded-full bg-muted overflow-hidden">
                    <div
                      className={`absolute left-0 top-0 h-full ${meta.text.replace("text-", "bg-")}`}
                      style={{ width: `${Math.min(100, layer.avg_strength * 100)}%` }}
                    />
                    {/* Promotion threshold marker at 0.4 */}
                    <div
                      className="absolute top-0 h-full w-px bg-foreground/40"
                      style={{ left: `${PROMOTION_THRESHOLD * 100}%` }}
                      title={`Promotion threshold (${PROMOTION_THRESHOLD})`}
                    />
                  </div>
                </div>

                <p className="text-[11px] text-muted-foreground mt-3 leading-snug">
                  {meta.description}
                </p>
              </div>

              {/* Flow arrow between cards (only on md+ screens, not after last) */}
              {idx < LAYER_META.length - 1 && (
                <div className="hidden md:flex absolute top-1/2 -right-3 -translate-y-1/2 z-10 pointer-events-none">
                  <div className="bg-background border border-border rounded-full p-1">
                    <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Strength Distribution Bar */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Strength Distribution</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {distributionTotal === 0 ? (
            <p className="text-xs text-muted-foreground">No active engrams.</p>
          ) : (
            <>
              <div className="relative h-6 rounded-lg overflow-hidden bg-muted flex">
                <div
                  className="bg-red-500 h-full flex items-center justify-center text-[10px] font-semibold text-white"
                  style={{ width: `${(strength_distribution.weak / distributionTotal) * 100}%` }}
                  title={`Weak (< 0.3): ${strength_distribution.weak}`}
                >
                  {strength_distribution.weak > 0 &&
                  strength_distribution.weak / distributionTotal > 0.08
                    ? strength_distribution.weak
                    : ""}
                </div>
                <div
                  className="bg-amber-500 h-full flex items-center justify-center text-[10px] font-semibold text-white"
                  style={{
                    width: `${(strength_distribution.moderate / distributionTotal) * 100}%`,
                  }}
                  title={`Moderate (0.3–0.7): ${strength_distribution.moderate}`}
                >
                  {strength_distribution.moderate > 0 &&
                  strength_distribution.moderate / distributionTotal > 0.08
                    ? strength_distribution.moderate
                    : ""}
                </div>
                <div
                  className="bg-emerald-500 h-full flex items-center justify-center text-[10px] font-semibold text-white"
                  style={{ width: `${(strength_distribution.strong / distributionTotal) * 100}%` }}
                  title={`Strong (> 0.7): ${strength_distribution.strong}`}
                >
                  {strength_distribution.strong > 0 &&
                  strength_distribution.strong / distributionTotal > 0.08
                    ? strength_distribution.strong
                    : ""}
                </div>
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  Weak &lt; 0.3 ({strength_distribution.weak.toLocaleString()})
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  Moderate 0.3–0.7 ({strength_distribution.moderate.toLocaleString()})
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  Strong &gt; 0.7 ({strength_distribution.strong.toLocaleString()})
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
