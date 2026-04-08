"use client";

import { Button } from "@/components/ui/button";

export type SessionMode = "precision" | "exploration" | "analogy" | "validation";

const MODES: { value: SessionMode; label: string; description: string }[] = [
  {
    value: "precision",
    label: "Precision",
    description: "Few strong facts, conservative inferences",
  },
  {
    value: "exploration",
    label: "Exploration",
    description: "Many facts including weak ones, bold inferences",
  },
  {
    value: "analogy",
    label: "Analogy",
    description: "Structural similarity, patterns from other domains",
  },
  {
    value: "validation",
    label: "Validation",
    description: "Juxtapose contradictions, verify consistency",
  },
];

interface SessionModeSelectorProps {
  value: SessionMode;
  onChange: (mode: SessionMode) => void;
}

export function SessionModeSelector({ value, onChange }: SessionModeSelectorProps) {
  return (
    <div>
      <label className="font-bold block mb-2 text-card-foreground">Mode:</label>
      <div className="flex gap-1">
        {MODES.map((mode) => (
          <Button
            key={mode.value}
            variant={value === mode.value ? "default" : "outline"}
            size="sm"
            onClick={() => onChange(mode.value)}
            title={mode.description}
            className="text-xs px-3"
          >
            {mode.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
