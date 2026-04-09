"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Copy, Check, X } from "lucide-react";
import { DocumentChunkModal } from "./document-chunk-modal";

interface MemoryDetailPanelProps {
  memory: any;
  onClose: () => void;
  compact?: boolean;
  inPanel?: boolean;
}

export function MemoryDetailPanel({
  memory,
  onClose,
  compact = false,
  inPanel = false,
}: MemoryDetailPanelProps) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [modalType, setModalType] = useState<"document" | "chunk" | null>(null);
  const [modalId, setModalId] = useState<string | null>(null);

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(text);
      setTimeout(() => setCopiedId(null), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
    }
  };

  const openDocumentModal = (docId: string) => {
    setModalType("document");
    setModalId(docId);
  };

  const openChunkModal = (chunkId: string) => {
    setModalType("chunk");
    setModalId(chunkId);
  };

  const closeModal = () => {
    setModalType(null);
    setModalId(null);
  };

  if (!memory) return null;

  // Handle both 'id' and 'node_id' (trace results use node_id)
  const memoryId = memory.id || memory.node_id;

  const labelSize = compact ? "text-[10px]" : "text-xs";
  const textSize = compact ? "text-xs" : "text-sm";

  // Panel mode: no outer border/bg, larger padding, prominent close button
  if (inPanel) {
    return (
      <>
        <div className="p-5">
          {/* Header with close button */}
          <div className="flex justify-between items-center mb-6 pb-4 border-b border-border">
            <div>
              <h3 className="text-xl font-bold text-foreground">Memory Details</h3>
              <p className="text-sm text-muted-foreground mt-1">Full memory content and metadata</p>
            </div>
            <Button variant="secondary" size="sm" onClick={onClose} className="h-8 w-8 p-0">
              <X className="h-5 w-5" />
            </Button>
          </div>

          <div className="space-y-5">
            {/* Full Text */}
            <div>
              <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                Full Text
              </div>
              <div className="text-sm whitespace-pre-wrap leading-relaxed text-foreground">
                {memory.text}
              </div>
            </div>

            {/* Context */}
            {memory.context && (
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                  Context
                </div>
                <div className="text-sm text-foreground">{memory.context}</div>
              </div>
            )}

            {/* Dates */}
            <div className="grid grid-cols-2 gap-4">
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                  Occurred
                </div>
                <div className="text-sm font-medium text-foreground">
                  {memory.occurred_start ? new Date(memory.occurred_start).toLocaleString() : "N/A"}
                </div>
              </div>
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                  Mentioned
                </div>
                <div className="text-sm font-medium text-foreground">
                  {memory.mentioned_at ? new Date(memory.mentioned_at).toLocaleString() : "N/A"}
                </div>
              </div>
            </div>

            {/* Entities */}
            {memory.entities && (
              <div>
                <div className="text-xs font-bold text-muted-foreground uppercase mb-3">
                  Entities
                </div>
                <div className="flex flex-wrap gap-2">
                  {(Array.isArray(memory.entities)
                    ? memory.entities
                    : String(memory.entities).split(", ")
                  ).map((entity: any, i: number) => {
                    const entityText =
                      typeof entity === "string" ? entity : entity?.name || JSON.stringify(entity);
                    return (
                      <span
                        key={i}
                        className="text-sm px-3 py-1.5 rounded-full bg-primary/10 text-primary font-medium"
                      >
                        {entityText}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Engram Metadata */}
            {(memory.strength != null || memory.layer != null || memory.access_count != null) && (
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-3">Engram</div>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase mb-1">Layer</div>
                    {memory.layer === "neocortex" ? (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 font-medium">
                        Neocortex
                      </span>
                    ) : memory.layer === "buffer" ? (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400 font-medium">
                        Buffer
                      </span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-medium">
                        Working
                      </span>
                    )}
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase mb-1">Strength</div>
                    {memory.strength != null ? (
                      <div className="flex items-center gap-1.5">
                        <div className="w-16 h-2 rounded-full bg-muted overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              memory.strength > 0.7
                                ? "bg-green-500"
                                : memory.strength > 0.3
                                  ? "bg-yellow-500"
                                  : "bg-red-500"
                            }`}
                            style={{ width: `${Math.round(memory.strength * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-foreground">
                          {memory.strength.toFixed(2)}
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">N/A</span>
                    )}
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase mb-1">Accesses</div>
                    <span className="text-xs font-mono text-foreground">
                      {memory.access_count ?? "–"}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Thalamus Scores */}
            {memory.thalamus_scores ? (
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-3">
                  Thalamus Scores
                </div>
                <div className="space-y-2">
                  {[
                    { key: "overall", label: "Overall" },
                    { key: "novelty", label: "Novelty" },
                    { key: "surprise", label: "Surprise" },
                    { key: "task_relevance", label: "Task Relevance" },
                    { key: "emotional_valence", label: "Emotional Valence" },
                  ].map(({ key, label }) => {
                    const val = memory.thalamus_scores[key];
                    return (
                      <div key={key} className="flex items-center gap-2">
                        <div className="w-28 text-xs text-muted-foreground shrink-0">{label}</div>
                        <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary"
                            style={{ width: val != null ? `${Math.round(val * 100)}%` : "0%" }}
                          />
                        </div>
                        <span className="text-xs font-mono text-foreground w-8 text-right">
                          {val != null ? val.toFixed(2) : "N/A"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                  Thalamus Scores
                </div>
                <div className="text-xs text-muted-foreground">N/A</div>
              </div>
            )}

            {/* ID */}
            {memoryId && (
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="text-xs font-bold text-muted-foreground uppercase mb-2">
                  Memory ID
                </div>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-mono break-all flex-1 text-muted-foreground">
                    {memoryId}
                  </code>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0 flex-shrink-0"
                    onClick={() => copyToClipboard(memoryId)}
                  >
                    {copiedId === memoryId ? (
                      <Check className="h-4 w-4 text-green-600" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>
            )}

            {/* Document/Chunk buttons */}
            {(memory.document_id || memory.chunk_id) && (
              <div className="flex gap-3 pt-2">
                {memory.document_id && (
                  <Button
                    onClick={() => openDocumentModal(memory.document_id)}
                    variant="secondary"
                    className="flex-1"
                  >
                    View Document
                  </Button>
                )}
                {memory.chunk_id && (
                  <Button
                    onClick={() => openChunkModal(memory.chunk_id)}
                    variant="secondary"
                    className="flex-1"
                  >
                    View Chunk
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Document/Chunk Modal */}
        {modalType && modalId && (
          <DocumentChunkModal type={modalType} id={modalId} onClose={closeModal} />
        )}
      </>
    );
  }

  // Original compact/default mode
  const padding = compact ? "p-3" : "p-4";
  const titleSize = compact ? "text-sm" : "text-lg";
  const gap = compact ? "space-y-2" : "space-y-4";

  return (
    <>
      <div
        className={`bg-card border-2 border-primary rounded-lg ${padding} sticky top-4 max-h-[calc(100vh-120px)] overflow-y-auto`}
      >
        <div className="flex justify-between items-start mb-4">
          <div>
            <h3 className={`${titleSize} font-bold text-card-foreground`}>Memory Details</h3>
            {!compact && (
              <p className="text-sm text-muted-foreground">Full memory content and metadata</p>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            className={compact ? "h-6 w-6 p-0" : "h-8 w-8 p-0"}
          >
            <X className={compact ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        </div>

        <div className={gap}>
          {/* Full Text */}
          <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
            <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-1`}>
              Full Text
            </div>
            <div className={`${textSize} whitespace-pre-wrap`}>{memory.text}</div>
          </div>

          {/* Context */}
          {memory.context && (
            <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
              <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-1`}>
                Context
              </div>
              <div className={textSize}>{memory.context}</div>
            </div>
          )}

          {/* Dates */}
          <div className="grid grid-cols-2 gap-2">
            <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
              <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-1`}>
                Occurred
              </div>
              <div className={textSize}>
                {memory.occurred_start ? new Date(memory.occurred_start).toLocaleString() : "N/A"}
              </div>
            </div>
            <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
              <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-1`}>
                Mentioned
              </div>
              <div className={textSize}>
                {memory.mentioned_at ? new Date(memory.mentioned_at).toLocaleString() : "N/A"}
              </div>
            </div>
          </div>

          {/* Entities */}
          {memory.entities && (
            <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
              <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-2`}>
                Entities
              </div>
              <div className="flex flex-wrap gap-1">
                {(Array.isArray(memory.entities)
                  ? memory.entities
                  : String(memory.entities).split(", ")
                ).map((entity: any, i: number) => {
                  const entityText =
                    typeof entity === "string" ? entity : entity?.name || JSON.stringify(entity);
                  return (
                    <span
                      key={i}
                      className={`${compact ? "text-[10px] px-1.5 py-0.5" : "text-xs px-2 py-1"} rounded bg-secondary text-secondary-foreground`}
                    >
                      {entityText}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {/* ID */}
          {memoryId && (
            <div className={`${compact ? "p-2" : "p-3"} bg-muted rounded-lg`}>
              <div className={`${labelSize} font-bold text-muted-foreground uppercase mb-1`}>
                Memory ID
              </div>
              <div className="flex items-center gap-2">
                <span className={`${compact ? "text-[10px]" : "text-sm"} font-mono break-all`}>
                  {memoryId}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0 flex-shrink-0"
                  onClick={() => copyToClipboard(memoryId)}
                >
                  {copiedId === memoryId ? (
                    <Check className="h-3 w-3 text-green-600" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </Button>
              </div>
            </div>
          )}

          {/* Document/Chunk buttons */}
          {(memory.document_id || memory.chunk_id) && (
            <div className={`flex gap-2 ${compact ? "pt-1" : ""}`}>
              {memory.document_id && (
                <Button
                  onClick={() => openDocumentModal(memory.document_id)}
                  size="sm"
                  variant="secondary"
                  className={`flex-1 ${compact ? "h-7 text-xs" : ""}`}
                >
                  View Document
                </Button>
              )}
              {memory.chunk_id && (
                <Button
                  onClick={() => openChunkModal(memory.chunk_id)}
                  size="sm"
                  variant="secondary"
                  className={`flex-1 ${compact ? "h-7 text-xs" : ""}`}
                >
                  View Chunk
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Document/Chunk Modal */}
      {modalType && modalId && (
        <DocumentChunkModal type={modalType} id={modalId} onClose={closeModal} />
      )}
    </>
  );
}
