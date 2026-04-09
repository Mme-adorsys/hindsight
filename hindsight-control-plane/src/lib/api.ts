/**
 * Client for calling Control Plane API routes (which proxy to the dataplane via SDK)
 * This should be used in client components, not the SDK directly
 */

export interface SystemConfig {
  llm: {
    provider: string;
    model: string;
    tier_routing: Record<string, string>;
  };
  embeddings: {
    provider: string;
    model: string;
  };
  reranker: {
    provider: string;
    model: string;
  };
  database: {
    postgres: string;
    qdrant: string;
    neo4j: string;
  };
  ncr: {
    enabled: boolean;
    interval_hours: number;
  };
}

export interface ThalamusScores {
  overall: number | null;
  novelty: number | null;
  surprise: number | null;
  task_relevance: number | null;
  emotional_valence: number | null;
}

// Retain-time provenance bundle (Phase A4 — 2026-04-09).
// Populated from engram_dictionary.retain_context JSONB. Currently carries
// the Thalamus rationale so the CP can explain "why this score" per
// dimension in the memory detail panel.
export interface ThalamusRationale {
  novelty_max_similar_id: string | null;
  novelty_max_similarity: number;
  surprise_expectation_provided: boolean;
  surprise_outcome_provided: boolean;
  task_relevance_context_source: "none" | "item" | "session";
  valence_prediction_error: number;
}

export interface RetainContext {
  thalamus_rationale?: ThalamusRationale;
  // future retain-time metadata slots land here without interface churn
  [key: string]: unknown;
}

export interface MemoryListItem {
  id: string;
  text: string;
  context: string;
  date: string;
  fact_type: string;
  mentioned_at: string | null;
  occurred_start: string | null;
  occurred_end: string | null;
  entities: string;
  chunk_id: string | null;
  // Engram metadata (null for legacy entries without engram_dictionary row)
  strength: number | null;
  layer: string | null;
  access_count: number | null;
  thalamus_scores: ThalamusScores | null;
  // Retain provenance (Phase A4)
  tags: string[] | null;
  expectation: string | null;
  outcome: string | null;
  session_mode: string | null;
  task_context: string | null;
  retain_context: RetainContext | null;
}

export interface EngramLayerStats {
  count: number;
  avg_strength: number;
}

export interface EngramStatsResponse {
  bank_id: string;
  total: number;
  layers: {
    working_memory: EngramLayerStats;
    buffer: EngramLayerStats;
    neocortex: EngramLayerStats;
  };
  strength_distribution: {
    weak: number;
    moderate: number;
    strong: number;
  };
}

// NCR (Nightly Consolidation Run) — Epic 21, Story 03

export interface NCRConsolidationStats {
  total: number;
  consolidated: number;
}

export interface NCRDecayStats {
  total: number;
  decayed: number;
  archived: number;
}

export interface NCRStrengthenStats {
  total: number;
  promoted: number;
}

export interface NCRSchemaStats {
  created: number;
  strengthened: number;
  deleted: number;
}

/** Live response from POST /ncr/trigger — flat shape, used by the trigger button. */
export interface NCRReport {
  bank_id: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  consolidation: NCRConsolidationStats | null;
  phase1_decay: NCRDecayStats | null;
  phase2_strengthen: NCRStrengthenStats | null;
  phase3_schema: NCRSchemaStats | null;
  errors: string[];
}

/** Historical row from GET /ncr/history — stats as flexible JSONB. */
export interface NCRRunHistoryItem {
  run_id: string;
  bank_id: string;
  trigger: "manual" | "scheduled";
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  consolidation_stats: Record<string, unknown> | null;
  decay_stats: Record<string, unknown> | null;
  strengthen_stats: Record<string, unknown> | null;
  schema_stats: Record<string, unknown> | null;
  promotion_stats: Record<string, unknown> | null;
  errors: string[] | null;
  // Observability Phase B (B4/B6) — per-run PipelineTrace with one step per NCR phase.
  trace_data: Record<string, unknown> | null;
}

export interface NCRHistoryResponse {
  runs: NCRRunHistoryItem[];
}

// ---------------------------------------------------------------------------
// Observability Phase B (B5/B6/B7) — PipelineTracer output for retain / recall / NCR.
// One TraceStep per pipeline phase, flat schema.
// ---------------------------------------------------------------------------

export interface PipelineTraceStep {
  name: string;
  phase: "retain" | "recall" | "ncr";
  started_at: string;
  duration_ms: number;
  status: "ok" | "error" | "skipped";
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  rationale: string | null;
  error: string | null;
}

export interface PipelineTrace {
  pipeline: "retain" | "recall" | "ncr";
  bank_id: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number;
  steps: PipelineTraceStep[];
  metadata: Record<string, unknown>;
}

export interface RetainTraceListItem {
  id: string;
  operation_id: string | null;
  started_at: string;
  duration_ms: number;
  step_count: number;
  status: "ok" | "error";
}

export interface RetainTraceListResponse {
  traces: RetainTraceListItem[];
}

export interface RetainTraceDetail {
  id: string;
  bank_id: string;
  operation_id: string | null;
  started_at: string;
  duration_ms: number;
  trace_data: PipelineTrace;
}

export interface SchemaMember {
  engram_id: string;
  text_preview: string;
  strength: number | null;
}

export interface SchemaItem {
  schema_id: string;
  label: string | null;
  member_count: number;
  maturity: "emerging" | "stable" | "dominant";
  avg_strength: number | null;
  created_at: string | null;
  last_activated: string | null;
  tags: string[] | null;
}

export interface SchemaListResponse {
  schemas: SchemaItem[];
}

export interface SchemaDetailResponse extends SchemaItem {
  members: SchemaMember[];
}

export class ControlPlaneClient {
  private async fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`API Error: ${response.status} - ${error}`);
    }

    return response.json();
  }

  /**
   * List all banks
   */
  async listBanks() {
    return this.fetchApi<{ banks: any[] }>("/api/banks", { cache: "no-store" as RequestCache });
  }

  /**
   * Create a new bank
   */
  async createBank(bankId: string) {
    return this.fetchApi<{ bank_id: string }>("/api/banks", {
      method: "POST",
      body: JSON.stringify({ bank_id: bankId }),
    });
  }

  /**
   * Recall memories
   */
  async recall(params: {
    query: string;
    types?: string[];
    bank_id: string;
    budget?: string;
    max_tokens?: number;
    trace?: boolean;
    include?: {
      entities?: { max_tokens: number } | null;
      chunks?: { max_tokens: number } | null;
    };
    query_timestamp?: string;
    mode?: string;
  }) {
    return this.fetchApi("/api/recall", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  /**
   * Reflect and generate answer
   */
  async reflect(params: {
    query: string;
    bank_id: string;
    budget?: string;
    context?: string;
    include_facts?: boolean;
    mode?: string;
  }) {
    return this.fetchApi("/api/reflect", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  /**
   * Retain memories (batch)
   */
  async retain(params: {
    bank_id: string;
    items: Array<{
      content: string;
      timestamp?: string;
      context?: string;
      metadata?: Record<string, string>;
      document_id?: string;
      entities?: Array<{ text: string; type?: string }>;
    }>;
    document_id?: string;
    async?: boolean;
  }) {
    const endpoint = params.async ? "/api/memories/retain_async" : "/api/memories/retain";
    return this.fetchApi(endpoint, {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  /**
   * Get bank statistics
   */
  async getBankStats(bankId: string) {
    return this.fetchApi(`/api/stats/${bankId}`);
  }

  /**
   * Get Engram lifecycle statistics (layer distribution + strength buckets)
   */
  async getEngramStats(bankId: string) {
    return this.fetchApi<EngramStatsResponse>(
      `/api/engrams/stats?bank_id=${encodeURIComponent(bankId)}`,
      { cache: "no-store" as RequestCache }
    );
  }

  /**
   * Manually trigger the NCR pipeline for a bank. Can take several minutes.
   */
  async triggerNCR(bankId: string) {
    return this.fetchApi<NCRReport>("/api/ncr/trigger", {
      method: "POST",
      body: JSON.stringify({ bank_id: bankId }),
    });
  }

  /**
   * Get the NCR run history for a bank (most recent first).
   */
  async getNCRHistory(bankId: string, limit?: number) {
    const qs = new URLSearchParams({ bank_id: bankId });
    if (limit !== undefined) qs.set("limit", String(limit));
    return this.fetchApi<NCRHistoryResponse>(`/api/ncr/history?${qs.toString()}`, {
      cache: "no-store" as RequestCache,
    });
  }

  /**
   * List the most recent retain pipeline traces for a bank (Phase B).
   */
  async listRetainTraces(bankId: string, limit?: number) {
    const qs = new URLSearchParams({ bank_id: bankId });
    if (limit !== undefined) qs.set("limit", String(limit));
    return this.fetchApi<RetainTraceListResponse>(`/api/retain_traces?${qs.toString()}`, {
      cache: "no-store" as RequestCache,
    });
  }

  /**
   * Get a single retain trace with the full trace_data payload.
   */
  async getRetainTrace(bankId: string, traceId: string) {
    const qs = new URLSearchParams({ bank_id: bankId });
    return this.fetchApi<RetainTraceDetail>(
      `/api/retain_traces/${encodeURIComponent(traceId)}?${qs.toString()}`,
      { cache: "no-store" as RequestCache }
    );
  }

  /**
   * List all schemas (Meta-Engrams) for a bank.
   */
  async listSchemas(bankId: string, limit?: number) {
    const qs = new URLSearchParams({ bank_id: bankId });
    if (limit !== undefined) qs.set("limit", String(limit));
    return this.fetchApi<SchemaListResponse>(`/api/schemas?${qs.toString()}`, {
      cache: "no-store" as RequestCache,
    });
  }

  /**
   * Get a single schema with its member Engrams.
   */
  async getSchemaDetail(schemaId: string, bankId: string) {
    return this.fetchApi<SchemaDetailResponse>(
      `/api/schemas/${encodeURIComponent(schemaId)}?bank_id=${encodeURIComponent(bankId)}`,
      { cache: "no-store" as RequestCache }
    );
  }

  /**
   * Get graph data
   */
  async getGraph(params: { bank_id: string; type?: string; limit?: number }) {
    const queryParams = new URLSearchParams();
    queryParams.append("bank_id", params.bank_id);
    if (params.type) queryParams.append("type", params.type);
    if (params.limit) queryParams.append("limit", params.limit.toString());
    return this.fetchApi(`/api/graph?${queryParams}`);
  }

  /**
   * List operations
   */
  async listOperations(bankId: string) {
    return this.fetchApi(`/api/operations/${bankId}`);
  }

  /**
   * List entities
   */
  async listEntities(params: { bank_id: string; limit?: number }) {
    const queryParams = new URLSearchParams();
    queryParams.append("bank_id", params.bank_id);
    if (params.limit) queryParams.append("limit", params.limit.toString());
    return this.fetchApi(`/api/entities?${queryParams}`);
  }

  /**
   * Get entity details
   */
  async getEntity(entityId: string, bankId: string) {
    return this.fetchApi(`/api/entities/${entityId}?bank_id=${bankId}`);
  }

  /**
   * Regenerate entity observations
   */
  async regenerateEntityObservations(entityId: string, bankId: string) {
    return this.fetchApi(`/api/entities/${entityId}/regenerate?bank_id=${bankId}`, {
      method: "POST",
    });
  }

  /**
   * List documents
   */
  async listDocuments(params: { bank_id: string; q?: string; limit?: number; offset?: number }) {
    const queryParams = new URLSearchParams();
    queryParams.append("bank_id", params.bank_id);
    if (params.q) queryParams.append("q", params.q);
    if (params.limit) queryParams.append("limit", params.limit.toString());
    if (params.offset) queryParams.append("offset", params.offset.toString());
    return this.fetchApi(`/api/documents?${queryParams}`);
  }

  /**
   * Get document
   */
  async getDocument(documentId: string, bankId: string) {
    return this.fetchApi(`/api/documents/${documentId}?bank_id=${bankId}`);
  }

  /**
   * Delete document and all its associated memory units
   */
  async deleteDocument(documentId: string, bankId: string) {
    return this.fetchApi<{
      success: boolean;
      message: string;
      document_id: string;
      memory_units_deleted: number;
    }>(`/api/documents/${documentId}?bank_id=${bankId}`, {
      method: "DELETE",
    });
  }

  /**
   * Delete an entire memory bank and all its data
   */
  async deleteBank(bankId: string) {
    return this.fetchApi<{
      success: boolean;
      message: string;
      deleted_count: number;
    }>(`/api/banks/${bankId}`, {
      method: "DELETE",
    });
  }

  /**
   * Get system configuration (read-only, no secrets)
   */
  async getSystemConfig(): Promise<SystemConfig> {
    return this.fetchApi<SystemConfig>("/api/config", { cache: "no-store" as RequestCache });
  }

  /**
   * Get chunk
   */
  async getChunk(chunkId: string) {
    return this.fetchApi(`/api/chunks/${chunkId}`);
  }

  /**
   * Get bank profile
   */
  async getBankProfile(bankId: string) {
    return this.fetchApi<{
      bank_id: string;
      name: string;
      disposition: {
        skepticism: number;
        literalism: number;
        empathy: number;
      };
      background: string;
    }>(`/api/profile/${bankId}`);
  }

  /**
   * Update bank profile
   */
  async updateBankProfile(
    bankId: string,
    profile: {
      name?: string;
      disposition?: {
        skepticism: number;
        literalism: number;
        empathy: number;
      };
      background?: string;
    }
  ) {
    return this.fetchApi(`/api/profile/${bankId}`, {
      method: "PUT",
      body: JSON.stringify(profile),
    });
  }
}

// Export singleton instance
export const client = new ControlPlaneClient();
