import { ChevronDown } from "lucide-react";

import { ANIMATION_SPEEDS } from "@/lib/app-utils";

interface SpeedButtonProps {
  animationDelayMs: number;
  onSpeedChange: (delayMs: number) => void;
  touch?: boolean;
  expanded?: boolean;
}

/**
 * Cycles through animation playback speeds on each tap
 * (1× → 2× → 4× → 0.5× → 1×), displaying the current speed label.
 */
export function SpeedButton({
  animationDelayMs,
  onSpeedChange,
  touch = false,
  expanded = false,
}: SpeedButtonProps) {
  const currentIndex = ANIMATION_SPEEDS.findIndex((speed) => speed.delayMs === animationDelayMs);
  const current = ANIMATION_SPEEDS[currentIndex] ?? ANIMATION_SPEEDS[0];

  const handleClick = () => {
    const nextIndex = (currentIndex + 1) % ANIMATION_SPEEDS.length;
    onSpeedChange(ANIMATION_SPEEDS[nextIndex].delayMs);
  };

  return (
    <button
      type="button"
      data-testid={expanded ? "timeline-speed-control" : undefined}
      onClick={handleClick}
      aria-label={`Animation speed ${current.label}`}
      className={`flex shrink-0 items-center justify-center rounded-lg border border-white/12 bg-white/[0.045] font-sans text-[12px] font-semibold text-cyan-200 transition-colors hover:border-white/20 hover:bg-white/[0.08] ${touch ? "h-11 w-11" : expanded ? "h-8 min-w-[68px] gap-2 px-3 pointer-coarse:h-11" : "h-9 w-9 pointer-coarse:h-11 pointer-coarse:w-11"}`}
    >
      {current.label}
      {expanded ? <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 text-white/45" /> : null}
    </button>
  );
}
