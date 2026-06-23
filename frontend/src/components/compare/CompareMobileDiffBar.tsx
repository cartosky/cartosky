type CompareMobileDiffBarProps = {
  /** Line 1: "{lRun} {lModel} − {rRun} {rModel}". */
  modelLine: string;
  /** Line 2: variable display name. */
  variableLine: string;
};

/**
 * Collapsed two-line diff summary bar for mobile (`layoutMode === "mobile"`).
 * Render-only: receives the formatted summary lines. The comparison-settings
 * drawer is opened from the settings button in the top utility row.
 */
export function CompareMobileDiffBar({ modelLine, variableLine }: CompareMobileDiffBarProps) {
  return (
    <div className="flex items-center justify-center gap-2">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
      >
        Δ
      </span>
      <div className="min-w-0 leading-tight text-center">
        <div className="text-[12px] font-medium text-white/82">{modelLine}</div>
        <div className="text-xs text-slate-400">{variableLine}</div>
      </div>
    </div>
  );
}

export default CompareMobileDiffBar;
