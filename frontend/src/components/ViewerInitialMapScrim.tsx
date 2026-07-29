import { HexSignalRing } from "@/components/HexSignalRing";

type ViewerInitialMapScrimProps = {
  visible: boolean;
  stageLabel: string;
  buildProgress: { available: number; total: number } | null;
};

/**
 * Cold-boot loading state confined to the map layer (map viewer redesign
 * Phase 2). Blocks pointer events only inside the map stacking context:
 * z-[55] sits above MapLibre, the vignette (z-10), notices (z-40), and
 * tooltips (z-50), and below the forecast controls (z-[60]/z-[70]) and the
 * site header (z-[80]) so chrome stays interactive while the requested frame
 * loads. Visibility is owned by App's requested-frame readiness gate.
 */
export function ViewerInitialMapScrim({ visible, stageLabel, buildProgress }: ViewerInitialMapScrimProps) {
  if (!visible) {
    return null;
  }
  return (
    <div
      data-testid="viewer-initial-map-scrim"
      role="status"
      aria-live="polite"
      aria-label={stageLabel}
      className="absolute inset-0 z-[55] flex items-center justify-center bg-[#040d18]/58 text-white backdrop-blur-[2px]"
      style={{
        paddingTop: "calc(var(--viewer-topbar-height, 3.5rem) + var(--viewer-header-extra, 0px))",
        paddingLeft: "var(--viewer-rail-width, 0px)",
      }}
    >
      <div className="flex flex-col items-center gap-3">
        <HexSignalRing />
        <div className="max-w-[16rem] text-center text-xs font-medium text-white/76">
          {stageLabel}
        </div>
        {buildProgress ? (
          <div className="flex items-center gap-1.5 text-[11px] font-medium leading-none">
            <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            <span className="shrink-0 text-amber-300/90">Building</span>
            <span className="shrink-0 tabular-nums text-white/50">
              {buildProgress.available}/{buildProgress.total} hrs
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
