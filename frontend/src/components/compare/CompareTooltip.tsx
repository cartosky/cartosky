import type { SampleTooltipState } from "@/lib/use-sample-tooltip";

type CompareTooltipProps = {
  leftTooltip: SampleTooltipState;
  rightTooltip: SampleTooltipState;
  x: number;
  y: number;
  containerWidth: number; // width of the panel being hovered
  side: "left" | "right";
};

export function CompareTooltip({
  leftTooltip,
  rightTooltip,
  x,
  y,
  containerWidth,
  side,
}: CompareTooltipProps) {
  const hasLeft = leftTooltip?.kind === "sample";
  const hasRight = rightTooltip?.kind === "sample";
  if (!hasLeft && !hasRight) return null;

  function formatValue(t: SampleTooltipState): string {
    if (!t || t.kind !== "sample") return "—";
    return t.label?.trim() || `${t.value.toFixed(1)} ${t.units}`;
  }

  // Flip tooltip to left of cursor when near right edge of panel
  const nearRightEdge = x > containerWidth - 160;
  const offsetX = nearRightEdge ? -(140 + 14) : 14;

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
