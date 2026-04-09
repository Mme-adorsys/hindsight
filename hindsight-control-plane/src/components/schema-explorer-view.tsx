"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useBank } from "@/lib/bank-context";
import { client } from "@/lib/api";
import type { SchemaItem, SchemaDetailResponse, SchemaMember } from "@/lib/api";
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
} from "lucide-react";
import cytoscape from "cytoscape";

// ---------------------------------------------------------------------------
// Maturity badge config
// ---------------------------------------------------------------------------

const MATURITY_CONFIG: Record<string, { label: string; bg: string; text: string }> = {
  emerging: { label: "Emerging", bg: "bg-slate-100 dark:bg-slate-800", text: "text-slate-600 dark:text-slate-400" },
  stable: { label: "Stable", bg: "bg-blue-100 dark:bg-blue-900/40", text: "text-blue-700 dark:text-blue-400" },
  dominant: { label: "Dominant", bg: "bg-emerald-100 dark:bg-emerald-900/40", text: "text-emerald-700 dark:text-emerald-400" },
};

// Cytoscape node colors by maturity
const MATURITY_COLORS: Record<string, string> = {
  emerging: "#94a3b8",
  stable: "#3b82f6",
  dominant: "#10b981",
};

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

type SortKey = "label" | "member_count" | "maturity" | "avg_strength" | "last_activated";
type SortDir = "asc" | "desc";

function compareSchemas(a: SchemaItem, b: SchemaItem, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case "label":
      cmp = (a.label || "").localeCompare(b.label || "");
      break;
    case "member_count":
      cmp = (a.member_count || 0) - (b.member_count || 0);
      break;
    case "maturity": {
      const order: Record<string, number> = { emerging: 0, stable: 1, dominant: 2 };
      cmp = (order[a.maturity] ?? 0) - (order[b.maturity] ?? 0);
      break;
    }
    case "avg_strength":
      cmp = (a.avg_strength || 0) - (b.avg_strength || 0);
      break;
    case "last_activated":
      cmp = (a.last_activated || "").localeCompare(b.last_activated || "");
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

// ---------------------------------------------------------------------------
// MaturityBadge
// ---------------------------------------------------------------------------

function MaturityBadge({ maturity }: { maturity: string }) {
  const cfg = MATURITY_CONFIG[maturity] || MATURITY_CONFIG.emerging;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
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
        {active && (currentDir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
      </span>
    </TableHead>
  );
}

// ---------------------------------------------------------------------------
// SchemaGraph (Cytoscape mini-graph)
// ---------------------------------------------------------------------------

function SchemaGraph({
  schema,
  members,
}: {
  schema: SchemaDetailResponse;
  members: SchemaMember[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const maturityColor = MATURITY_COLORS[schema.maturity] || MATURITY_COLORS.emerging;

    const nodes: cytoscape.ElementDefinition[] = [
      {
        data: {
          id: schema.schema_id,
          label: schema.label || "Schema",
          type: "schema",
        },
      },
      ...members.map((m) => ({
        data: {
          id: m.engram_id,
          label: (m.text_preview || "").slice(0, 40) + ((m.text_preview || "").length > 40 ? "..." : ""),
          type: "member",
          strength: m.strength || 0,
        },
      })),
    ];

    const edges: cytoscape.ElementDefinition[] = members.map((m) => ({
      data: {
        source: m.engram_id,
        target: schema.schema_id,
        id: `${m.engram_id}->${schema.schema_id}`,
      },
    }));

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...nodes, ...edges],
      style: [
        {
          selector: "node[type='schema']",
          style: {
            "background-color": maturityColor,
            label: "data(label)",
            "text-valign": "bottom",
            "text-margin-y": 8,
            "font-size": "11px",
            color: "#64748b",
            width: 48,
            height: 48,
            "border-width": 3,
            "border-color": maturityColor,
            "border-opacity": 0.4,
          },
        },
        {
          selector: "node[type='member']",
          style: {
            "background-color": "#94a3b8",
            "background-opacity": 0.7,
            label: "",
            width: `mapData(strength, 0, 1, 16, 36)`,
            height: `mapData(strength, 0, 1, 16, 36)`,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#cbd5e1",
            "curve-style": "bezier",
            opacity: 0.6,
          },
        },
      ],
      layout: {
        name: "concentric",
        concentric: (node: cytoscape.NodeSingular) => (node.data("type") === "schema" ? 2 : 1),
        levelWidth: () => 1,
        minNodeSpacing: 30,
        animate: false,
      },
      userZoomingEnabled: false,
      userPanningEnabled: false,
      boxSelectionEnabled: false,
      autoungrabify: true,
    });

    // Tooltip on hover
    cy.on("mouseover", "node[type='member']", (evt) => {
      const node = evt.target;
      node.style("label", node.data("label"));
      node.style("font-size", "9px");
      node.style("color", "#64748b");
      node.style("text-valign", "bottom");
      node.style("text-margin-y", 6);
    });
    cy.on("mouseout", "node[type='member']", (evt) => {
      evt.target.style("label", "");
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [schema, members]);

  return <div ref={containerRef} className="w-full h-[300px] rounded-lg border border-border bg-card" />;
}

// ---------------------------------------------------------------------------
// SchemaExplorerView (main export)
// ---------------------------------------------------------------------------

export function SchemaExplorerView() {
  const { currentBank } = useBank();
  const [schemas, setSchemas] = useState<SchemaItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SchemaDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Sort state
  // ---------------------------------------------------------------------------
  const [sortKey, setSortKey] = useState<SortKey>("last_activated");
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

  // ---------------------------------------------------------------------------
  // Fetch schema list
  // ---------------------------------------------------------------------------
  const fetchSchemas = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!currentBank) return;
      if (!opts?.silent) setLoading(true);
      try {
        const res = await client.listSchemas(currentBank);
        setSchemas(res.schemas);
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

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(() => fetchSchemas({ silent: true }), 30_000);
    return () => clearInterval(id);
  }, [fetchSchemas]);

  // ---------------------------------------------------------------------------
  // Fetch schema detail on selection
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!selectedId || !currentBank) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const res = await client.getSchemaDetail(selectedId, currentBank);
        if (!cancelled) setDetail(res);
      } catch {
        if (!cancelled) setDetail(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, currentBank]);

  // ---------------------------------------------------------------------------
  // Render: loading
  // ---------------------------------------------------------------------------
  if (loading) {
    return (
      <div className="space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 rounded-lg bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: error
  // ---------------------------------------------------------------------------
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

  // ---------------------------------------------------------------------------
  // Render: empty state
  // ---------------------------------------------------------------------------
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
              Schemas form after multiple NCR (Nightly Consolidation Run) cycles when frequently
              co-activated Engrams are clustered and abstracted into higher-level patterns.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: schema list + detail
  // ---------------------------------------------------------------------------
  return (
    <div className="space-y-6">
      {/* Refresh button */}
      <div className="flex justify-end">
        <button
          onClick={() => fetchSchemas()}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors rounded-md hover:bg-accent"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {/* Schema Table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Network className="w-4 h-4" />
            Schemas ({schemas.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHeader label="Label" sortKey="label" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                <SortableHeader label="Members" sortKey="member_count" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                <SortableHeader label="Maturity" sortKey="maturity" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                <SortableHeader label="Strength" sortKey="avg_strength" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                <SortableHeader label="Last Active" sortKey="last_activated" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedSchemas.map((s) => (
                <TableRow
                  key={s.schema_id}
                  className={`cursor-pointer transition-colors ${
                    selectedId === s.schema_id
                      ? "bg-primary/5 border-l-2 border-l-primary"
                      : "hover:bg-muted/50"
                  }`}
                  onClick={() => setSelectedId(selectedId === s.schema_id ? null : s.schema_id)}
                >
                  <TableCell className="font-medium max-w-[300px] truncate">
                    {s.label || <span className="text-muted-foreground italic">untitled</span>}
                  </TableCell>
                  <TableCell>{s.member_count}</TableCell>
                  <TableCell><MaturityBadge maturity={s.maturity} /></TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-2 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full rounded-full bg-primary/60"
                          style={{ width: `${(s.avg_strength || 0) * 100}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {((s.avg_strength || 0) * 100).toFixed(0)}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {formatRelativeTime(s.last_activated)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Detail Panel */}
      {selectedId && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Brain className="w-4 h-4" />
              {detailLoading ? "Loading..." : detail?.label || "Schema Detail"}
              {detail && <MaturityBadge maturity={detail.maturity} />}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {detailLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : detail ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Left: Member list */}
                <div>
                  <h4 className="text-sm font-semibold text-muted-foreground mb-3">
                    Members ({detail.members.length})
                  </h4>
                  {detail.members.length === 0 ? (
                    <p className="text-sm text-muted-foreground italic">
                      This schema has no remaining members — they may have decayed.
                    </p>
                  ) : (
                    <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
                      {detail.members.map((m) => (
                        <div
                          key={m.engram_id}
                          className="p-3 rounded-lg border border-border bg-muted/30 hover:bg-muted/60 transition-colors"
                        >
                          <p className="text-sm text-foreground line-clamp-2">
                            {m.text_preview || <span className="italic text-muted-foreground">no preview</span>}
                          </p>
                          <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
                            <span>Strength: {((m.strength || 0) * 100).toFixed(0)}%</span>
                            <span className="font-mono">{m.engram_id.slice(0, 8)}...</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Right: Mini-graph */}
                <div>
                  <h4 className="text-sm font-semibold text-muted-foreground mb-3">
                    Schema Graph
                  </h4>
                  {detail.members.length > 0 ? (
                    <SchemaGraph schema={detail} members={detail.members} />
                  ) : (
                    <div className="h-[300px] rounded-lg border border-border bg-card flex items-center justify-center">
                      <span className="text-sm text-muted-foreground">No members to visualize</span>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">Failed to load schema detail.</p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
