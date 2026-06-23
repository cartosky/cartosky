import { cn } from "@/lib/utils";

export type CompareMode = "split" | "diff";

type CompareModeToggleProps = {
  mode: CompareMode;
  onChange: (mode: CompareMode) => void;
  className?: string;
  /**
   * Compact sizing: h-7 buttons (no 44px mobile min) so the pill reads as a peer
   * next to h-8 icon buttons. Used in the mobile diff utility row.
   */
  compact?: boolean;
};

const OPTIONS: Array<{ value: CompareMode; label: string }> = [
  { value: "split", label: "Side by side" },
  { value: "diff", label: "Difference" },
];

/**
 * Segmented pill control toggling between split (side-by-side) and difference
 * compare modes. Stateless — the active mode and change handler are owned by the
 * parent (compare.tsx).
 */
export function CompareModeToggle({ mode, onChange, className, compact = false }: CompareModeToggleProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Compare mode"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-xl border border-white/[0.09] bg-white/[0.05] p-0.5",
        // Compact: fix the pill to h-8 so it matches sibling h-8 icon buttons exactly.
        compact && "h-8",
        className,
      )}
    >
      {OPTIONS.map((option) => {
        const active = option.value === mode;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => {
              if (!active) {
                onChange(option.value);
              }
            }}
            className={cn(
              "rounded-lg text-[11px] font-medium transition-all duration-150",
              // Compact fills the fixed h-8 pill (peer to h-8 icon buttons); default
              // keeps a 44px mobile tap target, collapsing to h-7 on desktop (sm+).
              compact ? "h-full px-2.5" : "h-7 min-h-[44px] px-3 sm:min-h-0",
              active
                ? "border border-cyan-300/25 bg-cyan-300/[0.10] text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
                : "border border-transparent text-white/55 hover:bg-white/[0.06] hover:text-white",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export default CompareModeToggle;
