import { cn } from "@/lib/utils";

export type SegmentedToggleOption<T extends string> = {
  value: T;
  label: string;
};

type SegmentedToggleProps<T extends string> = {
  value: T;
  onChange: (value: T) => void;
  options: Array<SegmentedToggleOption<T>>;
  ariaLabel: string;
  className?: string;
  /**
   * Compact sizing: h-7 buttons (no 44px mobile min) so the pill reads as a peer
   * next to h-8 icon buttons.
   */
  compact?: boolean;
};

export function SegmentedToggle<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  className,
  compact = false,
}: SegmentedToggleProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-0.5 rounded-xl border border-white/[0.09] bg-white/[0.05] p-0.5",
        compact && "h-8",
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
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
