import { cn } from "@/lib/utils";

/**
 * Shared viewer field-trigger contract (map viewer redesign §2.3 / §11.1).
 * Source of truth is the Product/Variable custom-button appearance; the
 * Statistic popover trigger and the Radix-backed Run SelectTrigger consume the
 * same string so the four controls read as one field family. Panels behind
 * the triggers are untouched.
 *
 * Sizing follows §2.1: 32px tall under a fine pointer, 44px under coarse
 * (`pointer-coarse:`); the mobile inline-panel variant is always 44px.
 * Keyboard focus comes from the global `:focus-visible` token in globals.css.
 */
export const VIEWER_FIELD_TRIGGER_CHEVRON_CLASSNAME =
  "h-4 w-4 shrink-0 opacity-50 transition-transform";

export function viewerFieldTriggerClassName(options: {
  size?: "desktop" | "inline";
  open?: boolean;
  className?: string;
} = {}): string {
  const { size = "desktop", open = false, className } = options;
  return cn(
    "inline-flex w-auto items-center justify-between gap-2 rounded-xl border border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white disabled:cursor-not-allowed disabled:opacity-50",
    size === "inline" ? "h-11" : "h-8 pointer-coarse:h-11",
    open ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100" : "",
    className,
  );
}
