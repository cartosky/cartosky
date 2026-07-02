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
    <SliderPrimitive.Thumb
      className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-600 shadow-[0_0_0_1px_rgba(6,182,212,0.2),0_0_8px_rgba(6,182,212,0.35)] transition-[box-shadow] duration-150 focus:outline-none focus-visible:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:shadow-[0_0_0_1px_rgba(6,182,212,0.3),0_0_12px_rgba(6,182,212,0.5)] active:scale-[1.08] disabled:pointer-events-none disabled:opacity-50"
      style={{ willChange: "transform", transform: "translateZ(0)" }}
    />
  </SliderPrimitive.Root>
));
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
