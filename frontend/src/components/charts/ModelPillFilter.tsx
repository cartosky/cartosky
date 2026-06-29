import { modelColor, modelShortName } from "@/lib/chart-constants";
import { useState, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@radix-ui/react-popover";
import { ChevronDown, Check } from "lucide-react";
import { buildRunOptions, formatRunLabel, sortRunIdsDescending } from "@/lib/run-options";

type ModelPillFilterProps = {
  models: string[];
  activeModels: Set<string>;
  onChange: (next: Set<string>) => void;
  mode?: "multi" | "single";
  availableRuns?: Record<string, string[]>;
  pinnedRuns?: Record<string, string>;
  /** Run ids actually served by the meteogram API (may differ from pins). */
  servedRuns?: Record<string, string>;
  onRunChange?: (model: string, runId: string | null) => void;
};

/**
 * Row of toggleable model pills. Each pill shows a color dot + short name.
 * Models the user cannot see (out of coverage / not entitled) are excluded by
 * the parent and never rendered here — no greyed-out pills.
 */

function RunPopover({
  model,
  runs,
  pinnedRunId,
  onRunChange,
  children,
}: {
  model: string;
  runs: string[];
  pinnedRunId: string | null;
  onRunChange: (model: string, runId: string | null) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const options = buildRunOptions(runs, sortRunIdsDescending(runs)[0] ?? null);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        className="z-50 min-w-[120px] rounded-lg border border-white/10 bg-[hsl(222,22%,11%)] p-1 shadow-xl"
        align="start"
        sideOffset={4}
      >
        {options.map((opt) => {
          const isSelected =
            opt.value === "latest" ? pinnedRunId === null : pinnedRunId === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-[12px] text-white/75 transition-colors hover:bg-white/[0.07] hover:text-white"
              onClick={() => {
                onRunChange(model, opt.value === "latest" ? null : opt.value);
                setOpen(false);
              }}
            >
              <span className="w-3 shrink-0">
                {isSelected && <Check className="h-3 w-3 text-white/60" />}
              </span>
              {opt.label}
            </button>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}

export function ModelPillFilter({ models, activeModels, onChange, mode = "multi", availableRuns, pinnedRuns, servedRuns, onRunChange, }: ModelPillFilterProps) {
  const toggle = (model: string) => {
    if (mode === "single") {
      // Single-select: clicking always selects exactly this model.
      onChange(new Set([model]));
      return;
    }
    const next = new Set(activeModels);
    if (next.has(model)) {
      next.delete(model);
    } else {
      next.add(model);
    }
    onChange(next);
  };

  return (
    <div className="flex flex-wrap gap-2">
      {models.map((model) => {
        const active = activeModels.has(model);
        const runs = availableRuns?.[model];
        const hasRuns = runs && runs.length > 0 && onRunChange;
        const pinnedRunId = pinnedRuns?.[model] ?? null;
        const servedRunId = servedRuns?.[model] ?? null;
        const displayedRunId = servedRunId ?? pinnedRunId;
        const currentRunLabel = displayedRunId
          ? formatRunLabel(displayedRunId)
          : runs
          ? formatRunLabel(sortRunIdsDescending(runs)[0])
          : null;

        const pillBase = `flex items-center rounded-full border transition-colors ${
          active
            ? "border-white/20 bg-white/[0.08]"
            : "border-white/10 bg-transparent"
        }`;

        return (
          <div key={model} className={pillBase}>
            {/* Left zone — toggles active */}
            <button
              type="button"
              onClick={() => toggle(model)}
              aria-pressed={active}
              className={`flex items-center gap-1.5 py-1 text-[12px] transition-colors ${
                hasRuns ? "pl-2.5 pr-2" : "px-2.5"
              } ${active ? "text-white/85" : "text-white/40 hover:text-white/60"}`}
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{
                  backgroundColor: modelColor(model),
                  opacity: active ? 1 : 0.3,
                }}
              />
              {modelShortName(model)}
            </button>

            {/* Right zone — run selector, only when runs available */}
            {hasRuns && currentRunLabel && (
              <RunPopover
                model={model}
                runs={runs!}
                pinnedRunId={displayedRunId}
                onRunChange={onRunChange!}
              >
                <button
                  type="button"
                  className={`flex items-center gap-0.5 border-l border-white/10 py-1 pl-2 pr-2.5 text-[10px] transition-colors ${
                    active
                      ? "text-white/45 hover:text-white/65"
                      : "text-white/25 hover:text-white/40"
                  }`}
                >
                  {currentRunLabel}
                  <ChevronDown className="h-2.5 w-2.5 shrink-0" />
                </button>
              </RunPopover>
            )}
          </div>
        );
      })}
    </div>
  );
}
