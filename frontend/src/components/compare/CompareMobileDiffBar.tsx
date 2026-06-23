type CompareMobileDiffBarProps = {
  /** e.g. "06Z 6/23 GFS - 00Z 6/23 GFS" */
  comparisonPart: string;
  /** e.g. "Surface Temp" */
  variablePart: string;
};

/**
 * Collapsed single-line diff summary for mobile (`layoutMode === "mobile"`).
 * The comparison-settings drawer is opened from the settings button in the top utility row.
 */
export function CompareMobileDiffBar({ comparisonPart, variablePart }: CompareMobileDiffBarProps) {
  return (
    <div className="flex items-center justify-center gap-2 px-1">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
      >
        Δ
      </span>
      <p className="flex min-w-0 flex-wrap items-center justify-center gap-x-1.5 gap-y-0.5 text-center text-[12px] font-medium leading-tight text-white/82">
        <span>{comparisonPart}</span>
        <span aria-hidden="true" className="h-1 w-1 shrink-0 rounded-full bg-cyan-400" />
        <span className="text-slate-400">{variablePart}</span>
      </p>
    </div>
  );
}

export default CompareMobileDiffBar;
