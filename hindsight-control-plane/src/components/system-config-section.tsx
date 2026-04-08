"use client";

import { useState, useEffect } from "react";
import { client } from "@/lib/api";
import type { SystemConfig } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Brain,
  Layers,
  Cpu,
  Server,
  CheckCircle,
  AlertCircle,
  Clock,
} from "lucide-react";

function StatusBadge({ name, status }: { name: string; status: string }) {
  const connected = status === "connected";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${
        connected
          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
          : "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
      }`}
    >
      {connected ? (
        <CheckCircle className="w-3 h-3" />
      ) : (
        <AlertCircle className="w-3 h-3" />
      )}
      {name}
    </span>
  );
}

export function SystemConfigSection() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .getSystemConfig()
      .then(setConfig)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Server className="w-5 h-5 text-primary" />
            System Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3 animate-pulse">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-24 rounded-xl bg-muted" />
              ))}
            </div>
            <div className="h-14 rounded-xl bg-muted" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!config) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Server className="w-5 h-5 text-primary" />
          System Configuration
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* LLM Cards Row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Primary Model */}
          <div className="bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className="p-1.5 rounded-lg bg-blue-500/20">
                <Brain className="w-4 h-4 text-blue-500" />
              </div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Primary Model
              </p>
            </div>
            <p className="text-sm font-bold text-foreground capitalize">{config.llm.provider}</p>
            <p className="text-xs text-muted-foreground font-mono mt-0.5 truncate" title={config.llm.model}>
              {config.llm.model}
            </p>
          </div>

          {/* Tier Routing */}
          <div className="bg-gradient-to-br from-purple-500/10 to-purple-600/5 border border-purple-500/20 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className="p-1.5 rounded-lg bg-purple-500/20">
                <Layers className="w-4 h-4 text-purple-500" />
              </div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Tier Routing
              </p>
            </div>
            <div className="space-y-1">
              {Object.entries(config.llm.tier_routing).map(([tier, model]) => (
                <div key={tier} className="flex justify-between items-center text-xs gap-2">
                  <span className="text-muted-foreground capitalize shrink-0">{tier}</span>
                  <span className="font-mono text-foreground truncate text-right" title={model}>
                    {model}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Embeddings & Reranker */}
          <div className="bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className="p-1.5 rounded-lg bg-emerald-500/20">
                <Cpu className="w-4 h-4 text-emerald-500" />
              </div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Embeddings & Reranker
              </p>
            </div>
            <div className="space-y-1.5">
              <div>
                <p className="text-[10px] text-muted-foreground uppercase tracking-wide">Embeddings</p>
                <p className="text-xs font-medium text-foreground capitalize">{config.embeddings.provider}</p>
                <p className="text-xs text-muted-foreground font-mono truncate" title={config.embeddings.model}>
                  {config.embeddings.model}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-muted-foreground uppercase tracking-wide">Reranker</p>
                <p className="text-xs font-medium text-foreground capitalize">{config.reranker.provider}</p>
                <p className="text-xs text-muted-foreground font-mono truncate" title={config.reranker.model}>
                  {config.reranker.model}
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Infrastructure Status + NCR Row */}
        <div className="flex flex-wrap items-center gap-3 px-1">
          <span className="text-xs text-muted-foreground font-medium">Infrastructure:</span>
          <StatusBadge name="PostgreSQL" status={config.database.postgres} />
          <StatusBadge name="Qdrant" status={config.database.qdrant} />
          <StatusBadge name="Neo4j" status={config.database.neo4j} />

          <span className="text-xs text-muted-foreground font-medium ml-3">NCR:</span>
          {config.ncr.enabled ? (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
              <CheckCircle className="w-3 h-3" />
              enabled · {config.ncr.interval_hours}h
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20">
              <Clock className="w-3 h-3" />
              disabled
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
