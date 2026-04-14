import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

import { cn } from "@/lib/utils";

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn("relative flex w-full touch-none select-none items-center focus-visible:outline-none", className)}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-secondary">
      <SliderPrimitive.Range className="absolute h-full bg-gradient-to-r from-cyan-800 via-cyan-700 to-cyan-600" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb
      className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-600 shadow-[0_0_0_1px_rgba(6,182,212,0.2),0_0_8px_rgba(6,182,212,0.35)] transition-[box-shadow] duration-150 focus:outline-none focus-visible:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 focus:shadow-[0_0_0_1px_rgba(6,182,212,0.3),0_0_12px_rgba(6,182,212,0.5)] active:scale-[1.08] disabled:pointer-events-none disabled:opacity-50"
      style={{ willChange: "transform", transform: "translateZ(0)" }}
    />
  </SliderPrimitive.Root>
));
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
