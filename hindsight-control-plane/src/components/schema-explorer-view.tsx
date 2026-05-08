"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useBank } from "@/lib/bank-context";
import { client } from "@/lib/api";
import type { EvidenceEngram, SchemaDetailResponse, SchemaItem } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Network,
  RefreshCw,
  AlertTriangle,
  ChevronUp,
  ChevronDown,
  Loader2,
  Brain,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import cytoscape from "cytoscape";

// Epic 25 Story 28 — Schema-Explorer rewritten on top of the new
// Story-27 Control Plane endpoints. Surfaces the new schema entity
// shape (description, evidence_count, cycles_survived,
// last_reinforced_at) plus the lifecycle counters from Stories 21/22
// (access_count, drift_count) and the cross-agent confidence tier
// from Stories 24/25 (agent_local | cross_agent_validated |
// cross_agent_disputed).

// ---------------------------------------------------------------------------
// Confidence-tier badge
// ---------------------------------------------------------------------------

const TIER_CONFIG: Record<
  string,
  { label: string; bg: string; text: string; Icon?: typeof ShieldCheck }
> = {
  agent_local: {
    label: "Agent-local",
    bg: "bg-slate-100 dark:bg-slate-800",
    text: "text-slate-600 dark:text-slate-400",
  },
  cross_agent_validated: {
    label: "Cross-agent",
    bg: "bg-emerald-100 dark:bg-emerald-900/40",
    text: "text-emerald-700 dark:text-emerald-400",
    Icon: ShieldCheck,
  },
  cross_agent_disputed: {
    label: "Disputed",
    bg: "bg-amber-100 dark:bg-amber-900/40",
    text: "text-amber-700 dark:text-amber-400",
    Icon: ShieldAlert,
  },
};

function ConfidenceBadge({ tier }: { tier: string | null }) {
  if (!tier) return null;
  const cfg = TIER_CONFIG[tier] ?? TIER_CONFIG.agent_local;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}
    >
      {cfg.Icon ? <cfg.Icon className="w-3 h-3" /> : null}
      {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

type SortKey = "description" | "evidence_count" | "cycles_survived" | "last_reinforced_at";
type SortDir = "asc" | "desc";

function compareSchemas(a: SchemaItem, b: SchemaItem, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case "description":
      cmp = (a.description || "").localeCompare(b.description || "");
      break;
    case "evidence_count":
      cmp = (a.evidence_count || 0) - (b.evidence_count || 0);
      break;
    case "cycles_survived":
      cmp = (a.cycles_survived || 0) - (b.cycles_survived || 0);
      break;
    case "last_reinforced_at":
      cmp = (a.last_reinforced_at || "").localeCompare(b.last_reinforced_at || "");
      break;
  }
  return dir === "asc" ? cmp : -cmp;
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function truncate(text: string, max = 80): string {
  if (!text) return "";
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

// ---------------------------------------------------------------------------
// SortableHeader
// ---------------------------------------------------------------------------

function SortableHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const active = currentKey === sortKey;
  return (
    <TableHead
      className="cursor-pointer select-none hover:text-foreground transition-colors"
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active &&
          (currentDir === "asc" ? (
            <ChevronUp className="w-3 h-3" />
          ) : (
            <ChevronDown className="w-3 h-3" />
          ))}
      </span>
    </TableHead>
  );
}

// ---------------------------------------------------------------------------
// SchemaGraph — Cytoscape mini-graph: schema centre + evidence engrams
// ---------------------------------------------------------------------------

function SchemaGraph({
  schema,
  evidence,
}: {
  schema: SchemaDetailResponse;
  evidence: EvidenceEngram[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const elements: cytoscape.ElementDefinition[] = [
      {
        data: {
          id: schema.id,
          label: truncate(schema.description, 24) || "(no description)",
          type: "schema",
        },
      },
      ...evidence.map((e) => ({
        data: {
          id: e.id,
          label: truncate(e.text, 18),
          type: "evidence",
        },
      })),
      ...evidence.map((e) => ({
        data: { id: `${schema.id}->${e.id}`, source: schema.id, target: e.id },
      })),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node[type='schema']",
          style: {
            "background-color": "#3b82f6",
            label: "data(label)",
            color: "#0f172a",
            "text-valign": "bottom",
            "text-margin-y": 6,
            "font-size": "11px",
            width: 36,
            height: 36,
          },
        },
        {
          selector: "node[type='evidence']",
          style: {
            "background-color": "#94a3b8",
            label: "",
            width: 14,
            height: 14,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": "#cbd5e1",
            "curve-style": "bezier",
          },
        },
      ],
      layout: {
        name: "concentric",
        concentric: (n: cytoscape.NodeSingular) => (n.data("type") === "schema" ? 100 : 1),
        levelWidth: () => 1,
        animate: false,
      },
      userZoomingEnabled: false,
      userPanningEnabled: false,
      boxSelectionEnabled: false,
      autoungrabify: true,
    });

    cy.on("mouseover", "node[type='evidence']", (evt: cytoscape.EventObject) => {
      const node = evt.target;
      node.style("label", node.data("label"));
      node.style("font-size", "9px");
      node.style("color", "#64748b");
      node.style("text-valign", "bottom");
      node.style("text-margin-y", 6);
    });
    cy.on("mouseout", "node[type='evidence']", (evt: cytoscape.EventObject) => {
      evt.target.style("label", "");
    });

    cyRef.current = cy as unknown as cytoscape.Core;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [schema, evidence]);

  return (
    <div ref={containerRef} className="w-full h-[300px] rounded-lg border border-border bg-card" />
  );
}

// ---------------------------------------------------------------------------
// Property renderer
// ---------------------------------------------------------------------------

function PropertyValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return <span className="text-xs text-muted-foreground">{value.map(String).join(", ")}</span>;
  }
  if (value && typeof value === "object" && "mean" in (value as Record<string, unknown>)) {
    const v = value as { min?: number; max?: number; mean?: number };
    return (
      <span className="text-xs font-mono text-muted-foreground">
        {v.mean?.toFixed(2)}{" "}
        <span className="opacity-60">
          ({v.min}–{v.max})
        </span>
      </span>
    );
  }
  if (typeof value === "object" && value !== null) {
    return <span className="text-xs font-mono text-muted-foreground">{JSON.stringify(value)}</span>;
  }
  return <span className="text-xs">{String(value)}</span>;
}

// ---------------------------------------------------------------------------
// SchemaExplorerView (main export)
// ---------------------------------------------------------------------------

export function SchemaExplorerView() {
  const { currentBank } = useBank();
  const [schemas, setSchemas] = useState<SchemaItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SchemaDetailResponse | null>(null);
  const [evidence, setEvidence] = useState<EvidenceEngram[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [sortKey, setSortKey] = useState<SortKey>("last_reinforced_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = useCallback(
    (key: SortKey) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key);
        setSortDir("desc");
      }
    },
    [sortKey]
  );

  const sortedSchemas = [...schemas].sort((a, b) => compareSchemas(a, b, sortKey, sortDir));

  const fetchSchemas = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!currentBank) return;
      if (!opts?.silent) setLoading(true);
      try {
        const res = await client.listSchemas(currentBank);
        setSchemas(res);
        setError(null);
      } catch (e) {
        if (!opts?.silent) setError(e instanceof Error ? e.message : "Failed to fetch schemas");
      } finally {
        if (!opts?.silent) setLoading(false);
      }
    },
    [currentBank]
  );

  useEffect(() => {
    fetchSchemas();
  }, [fetchSchemas]);

  useEffect(() => {
    const id = setInterval(() => fetchSchemas({ silent: true }), 30_000);
    return () => clearInterval(id);
  }, [fetchSchemas]);

  useEffect(() => {
    if (!selectedId || !currentBank) {
      setDetail(null);
      setEvidence([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const [d, ev] = await Promise.all([
          client.getSchemaDetail(selectedId),
          client.getSchemaEvidence(selectedId, currentBank).catch(() => [] as EvidenceEngram[]),
        ]);
        if (!cancelled) {
          setDetail(d);
          setEvidence(ev);
        }
      } catch {
        if (!cancelled) {
          setDetail(null);
          setEvidence([]);
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, currentBank]);

  if (loading) {
    return (
      <div className="space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 rounded-lg bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Card className="border-red-200 dark:border-red-900/40">
        <CardContent className="pt-6">
          <div className="flex items-center gap-2 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => fetchSchemas()}
            className="mt-3 text-sm text-primary hover:underline"
          >
            Retry
          </button>
        </CardContent>
      </Card>
    );
  }

  if (schemas.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="text-center py-12">
            <Network className="w-12 h-12 mx-auto text-muted-foreground/40 mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">
              No schemas have emerged yet
            </h3>
            <p className="text-muted-foreground text-sm max-w-md mx-auto">
              Schemas form after C2 cycles cluster repeatedly co-activated buffer engrams and R4
              mints a stable :Schema node. Run C2 a few times to see them surface.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <button
          onClick={() => fetchSchemas()}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors rounded-md hover:bg-accent"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Network className="w-4 h-4" />
            Schemas in {currentBank}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHeader
                  label="Description"
                  sortKey="description"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Evidence"
                  sortKey="evidence_count"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Cycles"
                  sortKey="cycles_survived"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Last reinforced"
                  sortKey="last_reinforced_at"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onSort={handleSort}
                />
                <TableHead>Tier</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedSchemas.map((s) => (
                <TableRow
                  key={s.id}
                  className={`cursor-pointer ${selectedId === s.id ? "bg-accent" : ""}`}
                  onClick={() => setSelectedId(s.id)}
                >
                  <TableCell className="font-medium">
                    {truncate(s.description, 60) || s.id}
                  </TableCell>
                  <TableCell>{s.evidence_count}</TableCell>
                  <TableCell>{s.cycles_survived}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatRelativeTime(s.last_reinforced_at)}
                  </TableCell>
                  <TableCell>
                    <ConfidenceBadge tier={s.confidence_tier} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selectedId && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Brain className="w-4 h-4" />
              Schema Detail
              {detailLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {detail ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="space-y-4">
                  <div>
                    <h4 className="text-sm font-medium text-muted-foreground mb-1">Description</h4>
                    <p className="text-sm">{detail.description || "(no description)"}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <span className="text-muted-foreground">Evidence count: </span>
                      <span className="font-medium">{detail.evidence_count}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Cycles survived: </span>
                      <span className="font-medium">{detail.cycles_survived}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Access count: </span>
                      <span className="font-medium">{detail.access_count}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Drift count: </span>
                      <span
                        className={`font-medium ${detail.drift_count > 0 ? "text-amber-600 dark:text-amber-400" : ""}`}
                      >
                        {detail.drift_count}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Last reinforced: </span>
                      <span>{formatRelativeTime(detail.last_reinforced_at)}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Last accessed: </span>
                      <span>{formatRelativeTime(detail.last_accessed)}</span>
                    </div>
                  </div>
                  {detail.confidence_tier && (
                    <div>
                      <h4 className="text-sm font-medium text-muted-foreground mb-1">Confidence</h4>
                      <ConfidenceBadge tier={detail.confidence_tier} />
                    </div>
                  )}
                  {Object.keys(detail.properties).length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-muted-foreground mb-1">Properties</h4>
                      <ul className="space-y-1">
                        {Object.entries(detail.properties).map(([k, v]) => (
                          <li key={k} className="flex items-baseline gap-2 text-sm">
                            <span className="font-mono text-xs text-muted-foreground">{k}:</span>
                            <PropertyValue value={v} />
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                <div className="space-y-3">
                  <h4 className="text-sm font-medium text-muted-foreground">Evidence Engrams</h4>
                  {evidence.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No active evidence engrams.</p>
                  ) : (
                    <ul className="space-y-2">
                      {evidence.map((e) => (
                        <li
                          key={e.id}
                          className="text-sm border rounded-md px-3 py-2 bg-card hover:bg-accent/40 transition-colors"
                        >
                          <p>{truncate(e.text, 140)}</p>
                          {e.tags?.length > 0 && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {e.tags.slice(0, 4).join(" · ")}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div>
                    <h4 className="text-sm font-medium text-muted-foreground mt-4 mb-2">
                      Mini-graph
                    </h4>
                    <SchemaGraph schema={detail} evidence={evidence} />
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Select a schema to inspect.</p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
