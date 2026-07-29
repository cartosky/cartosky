import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { ANIMATION_SPEEDS } from "@/lib/app-utils";
import { cn } from "@/lib/utils";

const DESKTOP_SPEED_OPTIONS = [...ANIMATION_SPEEDS].sort(
  (left, right) => right.delayMs - left.delayMs,
);

interface SpeedButtonProps {
  animationDelayMs: number;
  onSpeedChange: (delayMs: number) => void;
  touch?: boolean;
  expanded?: boolean;
}

/**
 * Compact/touch mode cycles through animation playback speeds. The expanded
 * desktop control opens a complete option menu so users can jump directly to
 * a speed instead of clicking through the sequence.
 */
export function SpeedButton({
  animationDelayMs,
  onSpeedChange,
  touch = false,
  expanded = false,
}: SpeedButtonProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const currentIndex = ANIMATION_SPEEDS.findIndex((speed) => speed.delayMs === animationDelayMs);
  const current = ANIMATION_SPEEDS[currentIndex] ?? ANIMATION_SPEEDS[0];

  useEffect(() => {
    if (!menuOpen) return undefined;
    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  const handleCycle = () => {
    const nextIndex = (currentIndex + 1) % ANIMATION_SPEEDS.length;
    onSpeedChange(ANIMATION_SPEEDS[nextIndex].delayMs);
  };

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        data-testid={expanded ? "timeline-speed-control" : undefined}
        onClick={expanded ? () => setMenuOpen((open) => !open) : handleCycle}
        aria-label={`Animation speed ${current.label}`}
        aria-haspopup={expanded ? "menu" : undefined}
        aria-expanded={expanded ? menuOpen : undefined}
        className={`flex shrink-0 items-center justify-center rounded-lg border border-white/12 bg-white/[0.045] font-sans text-[12px] font-semibold text-cyan-200 transition-colors hover:border-white/20 hover:bg-white/[0.08] ${touch ? "h-11 w-11" : expanded ? "h-8 min-w-[68px] gap-2 px-3 pointer-coarse:h-11" : "h-9 w-9 pointer-coarse:h-11 pointer-coarse:w-11"}`}
      >
        {current.label}
        {expanded ? (
          <ChevronDown
            aria-hidden="true"
            className={cn("h-3.5 w-3.5 text-white/45 transition-transform", menuOpen && "rotate-180")}
          />
        ) : null}
      </button>

      {expanded && menuOpen ? (
        <div
          data-testid="timeline-speed-menu"
          role="menu"
          aria-label="Animation speed options"
          className="absolute bottom-full left-0 z-50 mb-2 w-28 overflow-hidden rounded-xl border border-white/[0.12] bg-[#071522]/95 p-1.5 shadow-[0_12px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-xl"
        >
          {DESKTOP_SPEED_OPTIONS.map((speed) => {
            const selected = speed.delayMs === current.delayMs;
            return (
              <button
                key={speed.delayMs}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  onSpeedChange(speed.delayMs);
                  setMenuOpen(false);
                }}
                className={cn(
                  "flex min-h-8 w-full items-center justify-between rounded-lg px-2.5 text-[12px] font-semibold transition-colors pointer-coarse:min-h-11",
                  selected
                    ? "bg-cyan-300/[0.12] text-cyan-100"
                    : "text-white/68 hover:bg-white/[0.06] hover:text-white",
                )}
              >
                {speed.label}
                <Check
                  aria-hidden="true"
                  className={cn("h-3.5 w-3.5", selected ? "opacity-100" : "opacity-0")}
                />
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
