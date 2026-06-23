import { useMemo } from "react";
import { Play } from "lucide-react";

import { Slider } from "@/components/ui/slider";

type CompareScrubberProps = {
  leftFrameHours: number[];
  rightFrameHours: number[];
  forecastHour: number;
  onForecastHourChange: (hour: number) => void;
  leftResolvedRun: string;
  rightResolvedRun: string;
};

export function deriveValidTime(resolvedRun: string, forecastHour: number): string | null {
  // resolvedRun format: "YYYYMMDD_HHz" e.g. "20260619_12z"
  const match = resolvedRun.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})z$/i);
  if (!match) return null;
  const [, year, month, day, hour] = match;
  const validDate = new Date(Date.UTC(
    Number(year), Number(month) - 1, Number(day), Number(hour)
  ));
  validDate.setUTCHours(validDate.getUTCHours() + Math.round(forecastHour));
  try {
    const raw = new Intl.DateTimeFormat("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(validDate);
    // Some platforms/locales insert " at " between date and time — strip it.
    return raw.replace(/\s+at\s+/i, ", ");
  } catch {
    return null;
  }
}

function nearestValidHourIndex(validHours: number[], forecastHour: number): number {
  if (validHours.length === 0) {
    return 0;
  }
  if (!Number.isFinite(forecastHour)) {
    return 0;
  }
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  validHours.forEach((hour, index) => {
    const distance = Math.abs(hour - forecastHour);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

export function CompareScrubber({
  leftFrameHours,
  rightFrameHours,
  forecastHour,
  onForecastHourChange,
  leftResolvedRun,
  rightResolvedRun,
}: CompareScrubberProps) {
  const validHours = useMemo(() => {
    const rightSet = new Set(rightFrameHours);
    return leftFrameHours.filter(h => rightSet.has(h));
  }, [leftFrameHours, rightFrameHours]);

  if (validHours.length === 0) {
    return (
      <div className="pointer-events-none absolute inset-x-0 bottom-4 z-40 flex justify-center px-4">
        <div className="pointer-events-auto max-w-[min(92vw,720px)] rounded-2xl border border-amber-300/25 bg-[#07111f]/90 px-4 py-3 text-center text-sm text-amber-50 shadow-[0_14px_45px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-md">
          No overlapping forecast hours between the selected models/variables. Try selecting a shared variable or adjusting the run times.
        </div>
      </div>
    );
  }

  const currentIndex = nearestValidHourIndex(validHours, forecastHour);
  const currentHour = validHours[currentIndex];
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
            FH {Math.round(currentHour)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default CompareScrubber;
