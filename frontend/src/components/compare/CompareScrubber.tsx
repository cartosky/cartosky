import { Play } from "lucide-react";

import { nearestFrame } from "@/lib/app-utils";
import { deriveValidTime } from "@/lib/time-axis";
import { Slider } from "@/components/ui/slider";

type CompareScrubberProps = {
  /**
   * Left-anchored hours at which BOTH panels render the same valid time
   * (valid-time-aligned mutual grid hours, precomputed by the page).
   */
  validHours: number[];
  forecastHour: number;
  onForecastHourChange: (hour: number) => void;
  leftResolvedRun: string;
  rightResolvedRun: string;
  /**
   * Right panel's forecast-hour offset at equal valid time
   * (rightHour = leftHour + offset). Non-zero when the runs differ; drives
   * the dual "FH 30 / 42" readout.
   */
  rightHourOffset: number;
};

export function CompareScrubber({
  validHours,
  forecastHour,
  onForecastHourChange,
  leftResolvedRun,
  rightResolvedRun,
  rightHourOffset,
}: CompareScrubberProps) {
  if (validHours.length === 0) {
    return (
      <div className="pointer-events-none absolute inset-x-0 bottom-4 z-40 flex justify-center px-4">
        <div className="pointer-events-auto max-w-[min(92vw,720px)] rounded-2xl border border-amber-300/25 bg-[#07111f]/90 px-4 py-3 text-center text-sm text-amber-50 shadow-[0_14px_45px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-md">
          No overlapping forecast hours between the selected models/variables. Try selecting a shared variable or adjusting the run times.
        </div>
      </div>
    );
  }

  // nearestFrame is the same snap the panels use to pick their rendered frame
  // (ComparePanel/diff hour resolution) — one tie-break rule everywhere, so the
  // scrubber label can never disagree with the frame on screen.
  const currentHour = nearestFrame(validHours, forecastHour);
  const currentIndex = Math.max(0, validHours.indexOf(currentHour));
  const activeRun = leftResolvedRun || rightResolvedRun;
  const validTime = activeRun ? deriveValidTime(activeRun, currentHour) : null;

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-40 flex justify-center px-4">
      <div className="pointer-events-auto flex w-[min(92vw,680px)] items-center gap-3 rounded-2xl border border-[#1a3a5c]/60 bg-[#061120]/[0.88] px-3 py-2.5 text-white shadow-[0_14px_45px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-md">
        <div className="flex shrink-0 items-center gap-2">
          {/* Playback deferred — implement after viewer migration to useModelLoader */}
          <button
            type="button"
            disabled
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/12 bg-white/[0.06] text-white/38 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] disabled:cursor-not-allowed"
            aria-label="Play or pause comparison playback"
          >
            <Play className="h-[17px] w-[17px] translate-x-[1px]" />
          </button>
        </div>

        <div className="min-w-0 flex-1">
          <div className="relative h-8 min-w-0">
            <Slider
              value={[currentIndex]}
              onValueChange={([value]) => {
                const nextHour = validHours[Math.round(value ?? 0)];
                if (Number.isFinite(nextHour)) {
                  onForecastHourChange(nextHour);
                }
              }}
              min={0}
              max={Math.max(0, validHours.length - 1)}
              step={1}
              className="absolute inset-x-0 top-1/2 w-full -translate-y-1/2 [&>*:first-child]:h-2 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200 [&>*:nth-child(2)]:h-4 [&>*:nth-child(2)]:w-4"
              aria-label="Shared comparison forecast hour"
            />
          </div>
        </div>

        <div className="h-9 w-px shrink-0 bg-white/[0.08]" />
        <div className="flex shrink-0 flex-col items-end gap-0.5">
          {validTime ? (
            <span className="text-[12px] font-semibold tracking-tight text-white transition-all duration-200">
              {validTime}
            </span>
          ) : (
            <span className="font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.18em] text-cyan-200/70">
              Shared hour
            </span>
          )}
          <span className="text-[10px] font-medium text-cyan-200/80 transition-all duration-200">
            {rightHourOffset !== 0
              // Different init cycles: same valid time = different per-side
              // forecast hours; show both so "FH" is never wrong for a panel.
              ? `FH ${Math.round(currentHour)} / ${Math.round(currentHour + rightHourOffset)}`
              : `FH ${Math.round(currentHour)}`}
          </span>
        </div>
      </div>
    </div>
  );
}

export default CompareScrubber;
