import type { SampleTooltipState } from "@/lib/use-sample-tooltip";
import { getDiffScale } from "@/lib/compare-diff-scales";

type CompareTooltipProps = {
  leftTooltip: SampleTooltipState;
  rightTooltip: SampleTooltipState;
  x: number;
  y: number;
  containerWidth: number; // width of the panel being hovered
  side: "left" | "right";
  /** "split" (default) shows L/R rows; "diff" shows Δ plus a muted breakdown. */
  mode?: "split" | "diff";
  /** Active var_key — drives diff units (diff mode only). */
  varKey?: string | null;
  leftModel?: string;
  rightModel?: string;
};

function sampleValue(t: SampleTooltipState): number | null {
  if (!t || t.kind !== "sample") {
    return null;
  }
  return Number.isFinite(t.value) ? t.value : null;
}

function formatValue(t: SampleTooltipState): string {
  if (!t || t.kind !== "sample") return "—";
  return t.label?.trim() || `${t.value.toFixed(1)} ${t.units}`;
}

export function CompareTooltip({
  leftTooltip,
  rightTooltip,
  x,
  y,
  containerWidth,
  side,
  mode = "split",
  varKey,
  leftModel,
  rightModel,
}: CompareTooltipProps) {
  const hasLeft = leftTooltip?.kind === "sample";
  const hasRight = rightTooltip?.kind === "sample";
  if (!hasLeft && !hasRight) return null;

  // Flip tooltip to left of cursor when near right edge of panel
  const nearRightEdge = x > containerWidth - 160;
  const offsetX = nearRightEdge ? -(140 + 14) : 14;

  if (mode === "diff") {
    const leftValue = sampleValue(leftTooltip);
    const rightValue = sampleValue(rightTooltip);
    const units = getDiffScale(varKey).units;
    const hasBoth = leftValue !== null && rightValue !== null;
    const delta = hasBoth ? leftValue! - rightValue! : null;
    const deltaText = delta === null ? "—" : `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}${units ? ` ${units}` : ""}`;

    return (
      <div
        className="pointer-events-none absolute z-50 flex flex-col gap-1 rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.92] px-3 py-2 text-xs shadow-xl backdrop-blur-md"
        style={{ left: x + offsetX, top: y - 32 }}
      >
        <div className="flex items-center gap-2">
          <span className="w-5 shrink-0 text-[11px] font-bold text-cyan-200/80">Δ</span>
          <span className="font-semibold text-white">{deltaText}</span>
        </div>
        {hasBoth ? (
          <>
            <div className="border-t border-white/[0.07]" />
            <div className="flex items-center gap-2">
              <span className="w-5 shrink-0 text-[9px] font-semibold uppercase tracking-[0.14em] text-white/40">L</span>
              <span className="text-white/65">{formatValue(leftTooltip)}</span>
              {leftModel ? <span className="text-white/35">({leftModel.toUpperCase()})</span> : null}
            </div>
            <div className="flex items-center gap-2">
              <span className="w-5 shrink-0 text-[9px] font-semibold uppercase tracking-[0.14em] text-white/40">R</span>
              <span className="text-white/65">{formatValue(rightTooltip)}</span>
              {rightModel ? <span className="text-white/35">({rightModel.toUpperCase()})</span> : null}
            </div>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className="pointer-events-none absolute z-50 flex flex-col gap-1 rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.92] px-3 py-2 text-xs shadow-xl backdrop-blur-md"
      style={{ left: x + offsetX, top: y - 32 }}
    >
      <div className="flex items-center gap-2">
        <span className="font-semibold uppercase tracking-[0.14em] text-[9px] text-cyan-200/60 w-6 shrink-0">L</span>
        <span className="text-white/90 font-medium">{formatValue(leftTooltip)}</span>
      </div>
      <div className="border-t border-white/[0.07]" />
      <div className="flex items-center gap-2">
        <span className="font-semibold uppercase tracking-[0.14em] text-[9px] text-cyan-200/60 w-6 shrink-0">R</span>
        <span className="text-white/90 font-medium">{formatValue(rightTooltip)}</span>
      </div>
    </div>
  );
}
