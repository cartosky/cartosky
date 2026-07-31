import { SegmentedToggle } from "@/components/ui/segmented-toggle";
import { cn } from "@/lib/utils";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

/**
 * Coverage (data-domain) row — global go-live design §1/§2/§5.
 *
 * Shared by the desktop rail SOURCE section and the mobile SOURCE sheet, both
 * of which pass their own `FIELD_LABEL_CLASSNAME` so the row is visually
 * indistinguishable from Model / Variable / Run.
 *
 * Presentational only: the segment set, the selected value and the degraded
 * note all come from App.tsx via the toolbar context (the same seam every
 * other rail control uses). Renders nothing when the selected model declares
 * no non-canonical build region — canonical-only models see zero change.
 */
export function CoverageControl({
  labelClassName,
  className,
}: {
  labelClassName: string;
  className?: string;
}) {
  const toolbar = useViewerToolbar();
  const options = toolbar?.coverageOptions ?? [];
  if (!toolbar || options.length < 2) {
    return null;
  }

  return (
    <div
      data-testid="coverage-control"
      data-tour-target="coverage-control"
      className={cn("flex flex-col gap-1", className)}
    >
      <span data-testid="coverage-label" className={labelClassName}>Coverage</span>
      <SegmentedToggle
        value={toolbar.coverageValue ?? ""}
        onChange={(value) => toolbar.onCoverageChange?.(value)}
        options={options}
        ariaLabel="Coverage"
        className="w-full [&>button]:flex-1"
      />
      {toolbar.coverageDegradedNote ? (
        <span
          data-testid="coverage-degraded-note"
          className="pl-1 text-[11px] font-medium leading-snug text-amber-200/72"
        >
          {toolbar.coverageDegradedNote}
        </span>
      ) : null}
    </div>
  );
}

export default CoverageControl;
