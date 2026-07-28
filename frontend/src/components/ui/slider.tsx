import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

import { cn } from "@/lib/utils";

type SliderProps = React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root> & {
  /**
   * Track fractions (0-1) that are already buffered, rendered as subtle
   * segments over the track (video-player style). Rendered after the Range:
   * callers restyle the Range via `[&>*:first-child>*:first-child]` structural
   * selectors, so the Range must stay the Track's first child.
   */
  bufferedRanges?: Array<[number, number]>;
};

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({ className, bufferedRanges, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn("relative flex w-full touch-none select-none items-center focus-visible:outline-none", className)}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-secondary">
      <SliderPrimitive.Range className="absolute h-full bg-gradient-to-r from-cyan-800 via-cyan-700 to-cyan-600" />
      {bufferedRanges?.map(([start, end], index) => (
        <span
          key={index}
          aria-hidden
          className="pointer-events-none absolute h-full bg-cyan-300/20"
          style={{
            left: `${Math.max(0, Math.min(1, start)) * 100}%`,
            width: `${Math.max(0, Math.min(1, end) - Math.max(0, start)) * 100}%`,
          }}
        />
      ))}
    </SliderPrimitive.Track>
    {/* Touch-target contract (§2.1): the thumb ELEMENT is the hit area
        (32px fine / 44px coarse); the visual dot is a smaller inner child. */}
    {/* The historical inline translateZ(0) suppressed Radix's translateX(-50%),
        anchoring the thumb's LEFT edge at the value position; the dot center
        therefore sits at +half-thumb-width. Compensate per regime so the
        visual dot lands exactly where the pre-Phase-4 16px thumb put it:
        32px thumb -> -16px, 44px thumb -> -28px (A/B-measured against the exact pre-Phase-4 rendering on the live scrubber). transform-gpu keeps the
        compositing hint that translateZ(0) provided. */}
    <SliderPrimitive.Thumb
      className="group flex h-8 w-8 transform-gpu items-center justify-center rounded-full -translate-x-4 disabled:pointer-events-none disabled:opacity-50 pointer-coarse:h-11 pointer-coarse:w-11 pointer-coarse:-translate-x-7"
      style={{ willChange: "transform" }}
    >
      <span
        aria-hidden="true"
        className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-600 shadow-[0_0_0_1px_rgba(6,182,212,0.2),0_0_8px_rgba(6,182,212,0.35)] transition-[box-shadow,transform] duration-150 group-focus:shadow-[0_0_0_1px_rgba(6,182,212,0.3),0_0_12px_rgba(6,182,212,0.5)] group-active:scale-[1.08]"
      />
    </SliderPrimitive.Thumb>
  </SliderPrimitive.Root>
));
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
