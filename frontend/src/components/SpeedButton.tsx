import { ANIMATION_SPEEDS } from "@/lib/app-utils";

interface SpeedButtonProps {
  animationDelayMs: number;
  onSpeedChange: (delayMs: number) => void;
  touch?: boolean;
}

/**
 * Cycles through animation playback speeds on each tap
 * (1× → 2× → 4× → 0.5× → 1×), displaying the current speed label.
 */
export function SpeedButton({ animationDelayMs, onSpeedChange, touch = false }: SpeedButtonProps) {
  const currentIndex = ANIMATION_SPEEDS.findIndex((speed) => speed.delayMs === animationDelayMs);
  const current = ANIMATION_SPEEDS[currentIndex] ?? ANIMATION_SPEEDS[0];

  const handleClick = () => {
    const nextIndex = (currentIndex + 1) % ANIMATION_SPEEDS.length;
    onSpeedChange(ANIMATION_SPEEDS[nextIndex].delayMs);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={`Animation speed ${current.label}`}
      className={`flex shrink-0 items-center justify-center rounded-xl border border-white/12 bg-white/[0.05] font-['IBM_Plex_Mono',monospace] text-[11px] font-semibold text-cyan-300 transition-colors hover:bg-white/[0.09] ${touch ? "h-11 w-11" : "h-9 w-9"}`}
    >
      {current.label}
    </button>
  );
}
