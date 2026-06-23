import { cn } from "@/lib/utils";

export type CompareMode = "split" | "diff";

type CompareModeToggleProps = {
  mode: CompareMode;
  onChange: (mode: CompareMode) => void;
  className?: string;
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
export function CompareModeToggle({ mode, onChange, className }: CompareModeToggleProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Compare mode"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-xl border border-white/[0.09] bg-white/[0.05] p-0.5",
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
              // 44px min tap target on touch/mobile; compact h-7 on desktop (sm+).
              "h-7 min-h-[44px] rounded-lg px-3 text-[11px] font-medium transition-all duration-150 sm:min-h-0",
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
