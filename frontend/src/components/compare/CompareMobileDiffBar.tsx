type CompareMobileDiffBarProps = {
  /** e.g. "6/23 06Z GFS - 6/23 00Z GFS * Surface Temp" */
  summaryLine: string;
};

/**
 * Collapsed single-line diff summary for mobile (`layoutMode === "mobile"`).
 * The comparison-settings drawer is opened from the settings button in the top utility row.
 */
export function CompareMobileDiffBar({ summaryLine }: CompareMobileDiffBarProps) {
  return (
    <div className="flex items-center justify-center gap-2 px-1">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
      >
        Δ
      </span>
      <p className="min-w-0 text-center text-[12px] font-medium leading-tight text-white/82">
        {summaryLine}
      </p>
    </div>
  );
}

export default CompareMobileDiffBar;
