import { useEffect, useMemo, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@radix-ui/react-popover";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatisticOption = {
  value: string;
  label: string;
  longLabel?: string;
};

type StatKind = "mean" | "percentile" | "probability";

function classifyStatKey(key: string): StatKind {
  if (!key || key === "mean") return "mean";
  if (key.startsWith("prob_gt_")) return "probability";
  return "percentile";
}

const TAB_ORDER: Array<{ kind: StatKind; label: string }> = [
  { kind: "mean", label: "Mean" },
  { kind: "percentile", label: "Percentile" },
  { kind: "probability", label: "Probability" },
];

type StatisticPickerProps = {
  value: string;
  onValueChange: (value: string) => void;
  options: StatisticOption[];
  disabled?: boolean;
  minWidth?: string;
};

export function StatisticPicker({
  value,
  onValueChange,
  options,
  disabled,
  minWidth = "min-w-[120px] max-w-[200px]",
}: StatisticPickerProps) {
  const [open, setOpen] = useState(false);

  const grouped = useMemo(() => {
    const byKind: Record<StatKind, StatisticOption[]> = { mean: [], percentile: [], probability: [] };
    for (const option of options) {
      byKind[classifyStatKey(option.value)].push(option);
    }
    return byKind;
  }, [options]);

  const availableTabs = TAB_ORDER.filter((tab) => grouped[tab.kind].length > 0);
  const currentKind = classifyStatKey(value);
  const [activeTab, setActiveTab] = useState<StatKind>(currentKind);

  useEffect(() => {
    if (open) {
      setActiveTab(currentKind);
    }
  }, [open, currentKind]);

  if (availableTabs.length === 0) {
    return null;
  }

  const selectedLabel = options.find((option) => option.value === (value || "mean"))?.label ?? "Mean";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "inline-flex h-8 items-center justify-between gap-2 rounded-xl border border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50",
            minWidth,
            open ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100" : "",
          )}
        >
          <span className="whitespace-nowrap">{selectedLabel}</span>
          <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 opacity-50 transition-transform", open ? "rotate-180" : "")} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={8}
        className="z-[90] w-[240px] overflow-hidden rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.92] p-2 text-white shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
      >
        <div className="flex gap-1 rounded-lg bg-white/[0.04] p-1">
          {availableTabs.map((tab) => (
            <button
              key={tab.kind}
              type="button"
              onClick={() => {
                if (tab.kind === "mean") {
                  onValueChange("mean");
                  setOpen(false);
                  return;
                }
                setActiveTab(tab.kind);
              }}
              className={cn(
                "flex-1 rounded-md px-2 py-1.5 text-[11px] font-semibold transition-colors",
                activeTab === tab.kind
                  ? "bg-cyan-300/[0.14] text-cyan-100"
                  : "text-white/56 hover:bg-white/[0.06] hover:text-white/82",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {activeTab !== "mean" ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {grouped[activeTab].map((option) => {
              const selected = option.value === value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => {
                    onValueChange(option.value);
                    setOpen(false);
                  }}
                  className={cn(
                    "rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition-colors",
                    selected
                      ? "border-cyan-300/30 bg-cyan-300/[0.14] text-cyan-100"
                      : "border-white/[0.09] bg-white/[0.03] text-white/72 hover:border-white/18 hover:bg-white/[0.07] hover:text-white",
                  )}
                  title={option.longLabel ?? option.label}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
