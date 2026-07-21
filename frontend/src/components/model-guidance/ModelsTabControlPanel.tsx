import type { ReactNode } from "react";

import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { ControlSelect } from "@/components/model-guidance/ControlSelect";
import { SegmentedToggle } from "@/components/ui/segmented-toggle";
import { modelShortName } from "@/lib/chart-constants";
import { buildRunOptions, sortRunIdsDescending } from "@/lib/run-options";

type ViewMode = "compare" | "detail";

const VIEW_MODES = [
  { value: "compare", label: "Compare" },
  { value: "detail", label: "Model Detail" },
] as const satisfies ReadonlyArray<{ value: ViewMode; label: string }>;

type ModelsTabControlPanelProps = {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  models: string[];
  activeModels: Set<string>;
  onActiveModelsChange: (next: Set<string>) => void;
  filterMode?: "multi" | "single";
  availableRuns?: Record<string, string[]>;
  pinnedRuns?: Record<string, string>;
  servedRuns?: Record<string, string>;
  onRunChange?: (model: string, runId: string | null) => void;
};

function ControlRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-white/40">
        {label}:
      </span>
      {children}
    </div>
  );
}

export function ModelsTabControlPanel({
  viewMode,
  onViewModeChange,
  models,
  activeModels,
  onActiveModelsChange,
  filterMode = "multi",
  availableRuns,
  pinnedRuns,
  servedRuns,
  onRunChange,
}: ModelsTabControlPanelProps) {
  const selectedModel =
    filterMode === "single"
      ? models.find((m) => activeModels.has(m)) ?? models[0] ?? null
      : null;
  const runs = selectedModel
    ? sortRunIdsDescending(availableRuns?.[selectedModel] ?? [])
    : [];
  const runOptions = buildRunOptions(runs, runs[0] ?? null);
  const latestRun = runs[0] ?? null;
  const pinned = selectedModel ? pinnedRuns?.[selectedModel] : undefined;
  // A pin equal to the latest run reads as "Latest" (that option represents it).
  const runValue = pinned && pinned !== latestRun ? pinned : "latest";

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <ControlRow label="Mode">
          <SegmentedToggle
            value={viewMode}
            onChange={onViewModeChange}
            options={[...VIEW_MODES]}
            ariaLabel="Model guidance view"
          />
        </ControlRow>
        <div className="hidden h-5 w-px bg-white/10 sm:block" />
        {filterMode === "single" ? (
          <>
            <ControlSelect
              label="Model"
              ariaLabel="Detail model"
              value={selectedModel ?? ""}
              options={models.map((m) => ({ value: m, label: modelShortName(m) }))}
              onChange={(next) => onActiveModelsChange(new Set([next]))}
            />
            {selectedModel && runOptions.length > 0 && onRunChange ? (
              <ControlSelect
                label="Run time"
                ariaLabel={`${modelShortName(selectedModel)} run time`}
                value={runValue}
                options={runOptions}
                onChange={(next) =>
                  onRunChange(selectedModel, next === "latest" ? null : next)
                }
              />
            ) : null}
          </>
        ) : (
          <ControlRow label="Filter">
            <ModelPillFilter
              models={models}
              activeModels={activeModels}
              onChange={onActiveModelsChange}
              mode={filterMode}
              availableRuns={availableRuns}
              pinnedRuns={pinnedRuns}
              servedRuns={servedRuns}
              onRunChange={onRunChange}
            />
          </ControlRow>
        )}
      </div>
    </div>
  );
}
