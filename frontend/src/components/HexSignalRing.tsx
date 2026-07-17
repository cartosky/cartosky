/**
 * Brand hexagon outline with a bright segment chasing around it.
 * Geometry measured from the alpha channel of new_logo.png —
 * see docs/LOADER_REDESIGN_IMPLEMENTATION_PLAN.md §3.
 *
 * Sizes: "md" (44px, glow) for overlay/skeleton cards; "sm" (24px) for
 * standalone panel loaders; "xs" (12px) for inline search spinners, where the
 * comet is lengthened to ~35% of the perimeter so it still reads as motion.
 */
const HEX_POINTS = "161,0 320,90 320,253 161,343 2,253 2,90";

const SIZE_DEFAULTS: Record<HexSignalRingSize, string> = {
  md: "h-11 w-11",
  sm: "h-6 w-6",
  xs: "h-3 w-3",
};

type HexSignalRingSize = "md" | "sm" | "xs";

type HexSignalRingProps = {
  className?: string;
  size?: HexSignalRingSize;
};

export function HexSignalRing({ className, size = "md" }: HexSignalRingProps) {
  return (
    <svg
      viewBox="0 0 322 343"
      className={`overflow-visible ${size === "md" ? "" : `hex-signal-ring--${size} `}${className ?? SIZE_DEFAULTS[size]}`}
      aria-hidden="true"
    >
      <polygon className="hex-signal-ghost" points={HEX_POINTS} />
      <polygon className="hex-signal-comet" points={HEX_POINTS} />
    </svg>
  );
}
