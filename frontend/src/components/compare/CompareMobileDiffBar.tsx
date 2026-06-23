import { Settings } from "lucide-react";

type CompareMobileDiffBarProps = {
  /** Pre-formatted summary, e.g. "06Z GFS − 00Z ECMWF · Surface Temp". */
  summary: string;
  onOpenDrawer: () => void;
};

/**
 * Collapsed single-line diff summary bar for mobile (`layoutMode === "mobile"`).
 * Render-only: receives the formatted summary string and a callback to open the
 * comparison-settings drawer. Replaces the multi-row control header on mobile.
 */
export function CompareMobileDiffBar({ summary, onOpenDrawer }: CompareMobileDiffBarProps) {
  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
      >
        Δ
      </span>
      <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-white/82">
        {summary}
      </span>
      <button
        type="button"
        onClick={onOpenDrawer}
        aria-label="Comparison settings"
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/60 transition-all hover:border-white/18 hover:bg-white/[0.09] hover:text-white"
      >
        <Settings className="h-4 w-4" />
      </button>
    </div>
  );
}

export default CompareMobileDiffBar;
