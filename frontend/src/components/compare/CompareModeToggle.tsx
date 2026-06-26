import { SegmentedToggle } from "@/components/ui/segmented-toggle";

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
    <SegmentedToggle
      value={mode}
      onChange={onChange}
      options={OPTIONS}
      ariaLabel="Compare mode"
      className={className}
      compact={compact}
    />
  );
}

export default CompareModeToggle;
