function SummaryDot() {
  return <span aria-hidden="true" className="h-1 w-1 shrink-0 rounded-full bg-cyan-400" />;
}

function SummaryBadge({ children }: { children: string }) {
  return (
    <span
      aria-hidden="true"
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/[0.10] text-[13px] font-bold text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
    >
      {children}
    </span>
  );
}

type CompareMobileSummaryBarDiffProps = {
  variant?: "diff";
  comparisonPart: string;
  variablePart: string;
};

type CompareMobileSummaryBarSplitProps = {
  variant: "split";
  leftRunModel: string;
  leftVariable: string;
  rightRunModel: string;
  rightVariable: string;
};

export type CompareMobileSummaryBarProps =
  | CompareMobileSummaryBarDiffProps
  | CompareMobileSummaryBarSplitProps;

/**
 * Collapsed single-line compare summary for mobile. The settings drawer is
 * opened from the settings button in the top utility row.
 */
export function CompareMobileSummaryBar(props: CompareMobileSummaryBarProps) {
  return (
    <div className="flex items-center justify-center gap-2 px-1">
      <SummaryBadge>{props.variant === "split" ? "⇔" : "Δ"}</SummaryBadge>
      {props.variant === "split" ? (
        <p className="flex min-w-0 flex-wrap items-center justify-center gap-x-1.5 gap-y-0.5 text-center text-[12px] font-medium leading-tight text-white/82">
          <span>{props.leftRunModel}</span>
          <SummaryDot />
          <span className="text-slate-400">{props.leftVariable}</span>
          <span> - </span>
          <span>{props.rightRunModel}</span>
          <SummaryDot />
          <span className="text-slate-400">{props.rightVariable}</span>
        </p>
      ) : (
        <p className="flex min-w-0 flex-wrap items-center justify-center gap-x-1.5 gap-y-0.5 text-center text-[12px] font-medium leading-tight text-white/82">
          <span>{props.comparisonPart}</span>
          <SummaryDot />
          <span className="text-slate-400">{props.variablePart}</span>
        </p>
      )}
    </div>
  );
}

/** @deprecated Use CompareMobileSummaryBar */
export const CompareMobileDiffBar = CompareMobileSummaryBar;

export default CompareMobileSummaryBar;
