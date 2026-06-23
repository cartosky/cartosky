import { SlidersHorizontal } from "lucide-react";

type CompareMobileDiffBarProps = {
  /** Line 1: "{lRun} {lModel} − {rRun} {rModel}". */
  modelLine: string;
  /** Line 2: variable display name. */
  variableLine: string;
  onOpenDrawer: () => void;
};

/**
 * Collapsed two-line diff summary bar for mobile (`layoutMode === "mobile"`).
 * Render-only: receives the formatted summary lines and a callback to open the
 * comparison-settings drawer. Replaces the multi-row control header on mobile.
 */
export function CompareMobileDiffBar({ modelLine, variableLine, onOpenDrawer }: CompareMobileDiffBarProps) {
  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
      >
        Δ
      </span>
      <div className="min-w-0 flex-1 leading-tight">
        <div className="text-[12px] font-medium text-white/82">{modelLine}</div>
        <div className="text-xs text-slate-400">{variableLine}</div>
      </div>
      <button
        type="button"
        onClick={onOpenDrawer}
        aria-label="Comparison settings"
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/60 transition-all hover:border-white/18 hover:bg-white/[0.09] hover:text-white"
      >
        <SlidersHorizontal className="h-4 w-4" />
      </button>
    </div>
  );
}

export default CompareMobileDiffBar;
