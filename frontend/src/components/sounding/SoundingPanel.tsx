import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RotateCw, X } from "lucide-react";

import { Hodograph } from "@/components/sounding/Hodograph";
import { SkewTChart } from "@/components/sounding/SkewTChart";
import { ThetaEInset } from "@/components/sounding/ThetaEInset";
import { useSounding } from "@/hooks/useSounding";
import { cn } from "@/lib/utils";
import type { SoundingFrame, SoundingGridPoint } from "@/lib/sounding-types";

/**
 * Map-click sounding panel (Skew-T design §6, Phase 3).
 *
 * Desktop: a docked right-hand panel — the map stays interactive underneath and
 * the grid-point marker stays visible. Mobile (<768 px): a full-width bottom
 * sheet following the Phase 8 sheet conventions (`window.innerHeight`, never
 * `dvh`, because the mobile URL bar makes viewport units lie).
 *
 * ## Forecast-hour sync (implementation choice)
 * ONE-WAY viewer → panel with a panel-local override. While the user has not
 * touched the panel slider, the panel follows the viewer's global forecast hour
 * whenever that hour exists in `frames`. The first drag of the panel slider
 * takes local control until the point or run changes. Two-way sync was
 * rejected as invasive: driving the viewer from here would fire grid fetches
 * and animation state changes from a panel that is meant to be a read-only
 * inspection surface.
 */

const PANEL_WIDTH_PX = 440;
/** Bottom controls + breathing room, so the panel never sits under them. */
const DESKTOP_BOTTOM_GUTTER_PX = 136;

function useViewportHeight(active: boolean): number {
  const [height, setHeight] = useState(() =>
    typeof window === "undefined" ? 844 : window.innerHeight,
  );
  useEffect(() => {
    if (!active || typeof window === "undefined") return undefined;
    const update = () => setHeight(window.innerHeight);
    update();
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, [active]);
  return height;
}

function formatValidTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatCoordinate(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)}°${ns}, ${Math.abs(lon).toFixed(2)}°${ew}`;
}

function formatDistance(gridPoint: SoundingGridPoint | undefined): string {
  if (!gridPoint || !Number.isFinite(gridPoint.distance_km)) return "";
  return `~${gridPoint.distance_km.toFixed(1)} km from click`;
}

/** Every null index renders as an em dash — never 0, never a blank cell. */
const EMPTY_VALUE = "—";

function formatIndex(
  value: number | null | undefined,
  { digits = 0, unit = "" }: { digits?: number; unit?: string } = {},
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EMPTY_VALUE;
  return `${value.toFixed(digits)}${unit}`;
}

function IndexRow({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-white/45">{label}</span>
      <span data-testid={testId} className="tabular-nums text-white/[0.88]">
        {value}
      </span>
    </div>
  );
}

/**
 * Server-computed thermodynamics readout (design §7 Phase 4).
 *
 * Values are formatted here and computed nowhere — the client contains zero
 * thermodynamics. The parcel definition underneath is the server's string
 * verbatim, so the numbers and the label describing them can never drift.
 */
function IndicesReadout({
  frame,
  parcelDefinition,
}: {
  frame: SoundingFrame;
  parcelDefinition: string | undefined;
}) {
  const indices = frame.indices ?? null;
  const modelSbcape = indices?.model_sbcape;
  const hasModelSbcape = typeof modelSbcape === "number" && Number.isFinite(modelSbcape);

  return (
    <div className="mt-3">
      <div
        data-testid="sounding-indices"
        className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2.5 font-['IBM_Plex_Mono',monospace] text-[11px] leading-relaxed"
      >
        <IndexRow
          label="SBCAPE"
          testId="sounding-index-sbcape"
          value={formatIndex(indices?.sbcape)}
        />
        <IndexRow label="SBCIN" testId="sounding-index-sbcin" value={formatIndex(indices?.sbcin)} />
        <IndexRow
          label="MLCAPE"
          testId="sounding-index-mlcape"
          value={formatIndex(indices?.mlcape)}
        />
        <IndexRow label="MLCIN" testId="sounding-index-mlcin" value={formatIndex(indices?.mlcin)} />
        <IndexRow
          label="PWAT"
          testId="sounding-index-pwat"
          value={formatIndex(indices?.pwat_mm, { digits: 1, unit: " mm" })}
        />
        <IndexRow
          label="LCL"
          testId="sounding-index-lcl"
          value={formatIndex(indices?.lcl_hPa, { digits: 0, unit: " hPa" })}
        />
        {/* Only on format_version ≥ 2 stacks; older runs simply lack the row
            rather than showing a fabricated dash (decision #5). */}
        {hasModelSbcape ? (
          <IndexRow
            label="HRRR SBCAPE"
            testId="sounding-index-model-sbcape"
            value={formatIndex(modelSbcape)}
          />
        ) : null}
      </div>
      {parcelDefinition ? (
        <p
          data-testid="sounding-parcel-definition"
          className="mt-1.5 px-0.5 text-[10px] leading-snug text-white/40"
        >
          {parcelDefinition}
        </p>
      ) : null}
    </div>
  );
}

export type SoundingPanelProps = {
  model: string;
  modelLabel: string;
  /** The clicked point; the backend snaps it to the nearest grid cell. */
  lat: number;
  lon: number;
  /** Viewer's global forecast hour, or null when it is not a finite hour. */
  viewerForecastHour: number | null;
  layout: "desktop" | "mobile";
  onClose: () => void;
  /** Reports the snapped grid point so the map can place its marker. */
  onGridPoint?: (point: { lat: number; lon: number } | null) => void;
};

export function SoundingPanel({
  model,
  modelLabel,
  lat,
  lon,
  viewerForecastHour,
  layout,
  onClose,
  onGridPoint,
}: SoundingPanelProps) {
  const { data, loading, error, reload } = useSounding({ model, lat, lon });
  const [overrideFh, setOverrideFh] = useState<number | null>(null);
  const viewportHeight = useViewportHeight(layout === "mobile");

  const frames = data?.frames ?? [];
  const frameHours = useMemo(() => frames.map((frame) => frame.fh), [frames]);

  // A new pick or a new run hands control back to the viewer's hour.
  const identity = `${data?.run ?? ""}|${data?.grid_point?.row ?? ""}|${data?.grid_point?.col ?? ""}`;
  const lastIdentityRef = useRef(identity);
  useEffect(() => {
    if (lastIdentityRef.current !== identity) {
      lastIdentityRef.current = identity;
      setOverrideFh(null);
    }
  }, [identity]);

  const activeIndex = useMemo(() => {
    if (frameHours.length === 0) return 0;
    if (overrideFh != null) {
      const index = frameHours.indexOf(overrideFh);
      if (index >= 0) return index;
    }
    if (viewerForecastHour != null && Number.isFinite(viewerForecastHour)) {
      const index = frameHours.indexOf(Math.round(viewerForecastHour));
      if (index >= 0) return index;
    }
    return 0;
  }, [frameHours, overrideFh, viewerForecastHour]);

  const frame = frames[activeIndex] ?? null;

  // Report the snapped grid point up so the map marker tracks it; clear it on
  // unmount so closing the panel removes the marker.
  const gridLat = data?.grid_point?.lat ?? null;
  const gridLon = data?.grid_point?.lon ?? null;
  useEffect(() => {
    if (gridLat == null || gridLon == null) return;
    onGridPoint?.({ lat: gridLat, lon: gridLon });
    // `onGridPoint` is a stable App callback; depending on it would re-fire on
    // every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridLat, gridLon]);
  useEffect(
    () => () => onGridPoint?.(null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const isMobile = layout === "mobile";
  const containerStyle = isMobile
    ? { maxHeight: `${Math.round(viewportHeight * 0.82)}px` }
    : {
        top: "calc(var(--viewer-topbar-height, 3.5rem) + var(--viewer-header-extra, 0px) + 0.75rem)",
        width: `${PANEL_WIDTH_PX}px`,
        maxHeight: `calc(100vh - var(--viewer-topbar-height, 3.5rem) - var(--viewer-header-extra, 0px) - ${DESKTOP_BOTTOM_GUTTER_PX}px)`,
        // Denser than .glass-navy's 0.74 alpha: map data bleeding through makes
        // the traces hard to read (Brian, Phase 3 prod review). Panel-local so
        // other glass-navy surfaces keep their look.
        backgroundColor: "rgba(4, 16, 30, 0.93)",
      };

  return (
    <aside
      data-testid="sounding-panel"
      aria-label={`${modelLabel} sounding`}
      className={cn(
        // Above the Phase 8 mobile sheet (z-66) so the panel is never buried
        // by the peek; below the bar overflow menus (z-90).
        "fixed z-[70] flex flex-col overflow-hidden",
        isMobile
          ? "viewer-mobile-surface inset-x-0 bottom-0 rounded-t-2xl"
          : "glass-navy right-3 rounded-2xl",
      )}
      style={containerStyle}
    >
      <header className="flex shrink-0 items-start justify-between gap-2 border-b border-white/[0.07] px-3.5 py-2.5">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-white/[0.92]">
            Sounding — {modelLabel}
          </div>
          <div className="mt-0.5 font-['IBM_Plex_Mono',monospace] text-[11px] leading-relaxed text-white/50">
            {data ? (
              <>
                <span data-testid="sounding-run">{data.run}</span>
                {" · "}
                <span data-testid="sounding-grid-point">
                  {formatCoordinate(data.grid_point.lat, data.grid_point.lon)}
                </span>
                {formatDistance(data.grid_point) ? (
                  <>
                    {" · "}
                    <span data-testid="sounding-distance">{formatDistance(data.grid_point)}</span>
                  </>
                ) : null}
              </>
            ) : (
              formatCoordinate(lat, lon)
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close sounding"
          data-testid="sounding-close"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-white/40 transition-colors hover:bg-white/[0.07] hover:text-white/80 pointer-coarse:h-11 pointer-coarse:w-11"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {loading ? (
          <div
            data-testid="sounding-loading"
            className="flex h-[340px] flex-col items-center justify-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02]"
          >
            <Loader2 className="h-5 w-5 animate-spin text-cyan-200/70" />
            <span className="text-[12px] text-white/45">Loading sounding…</span>
          </div>
        ) : null}

        {!loading && error ? (
          <div
            data-testid="sounding-error"
            className="flex h-[340px] flex-col items-center justify-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-6 text-center"
          >
            <span className="text-[12px] leading-relaxed text-white/60">{error}</span>
            <button
              type="button"
              onClick={reload}
              data-testid="sounding-retry"
              className="inline-flex min-h-8 items-center gap-1.5 rounded-[7px] border border-white/[0.12] bg-white/[0.05] px-3 text-[12px] font-semibold text-white/80 transition-colors hover:bg-white/[0.09] pointer-coarse:min-h-11"
            >
              <RotateCw className="h-3.5 w-3.5" />
              Retry
            </button>
          </div>
        ) : null}

        {!loading && !error && data && frame ? (
          <>
            <SkewTChart
              levelsHPa={data.levels_hPa}
              frame={frame}
              title={`${modelLabel} sounding, ${formatCoordinate(data.grid_point.lat, data.grid_point.lon)}`}
            />
            {/* Stacked sections, never floating insets on the chart (Brian,
                Phase 5): chart → [hodograph | θe] → indices. The row collapses
                to one column below a 384px VIEWPORT (a proxy for content width
                that is exact today: mobile content is viewport−24px and the
                desktop panel is fixed 440px — revisit if the panel ever
                becomes resizable). */}
            {frame.profiles ? (
              <div
                data-testid="sounding-insets"
                className="mt-3 flex flex-col gap-3 min-[384px]:flex-row min-[384px]:gap-2.5"
              >
                <Hodograph
                  className="min-w-0 flex-1"
                  u={frame.u}
                  v={frame.v}
                  heightM={frame.profiles.height_m_agl}
                  surfaceU={frame.surface?.u10m}
                  surfaceV={frame.surface?.v10m}
                />
                <ThetaEInset
                  className="min-w-0 flex-1"
                  levelsHPa={data.levels_hPa}
                  thetaE={frame.profiles.theta_e}
                  surfaceP={frame.surface?.pres_sfc}
                  surfaceValue={frame.profiles.surface_theta_e}
                />
              </div>
            ) : null}
            <IndicesReadout frame={frame} parcelDefinition={data.parcel_definition} />
          </>
        ) : null}
      </div>

      {!loading && !error && frames.length > 0 ? (
        <div className="shrink-0 border-t border-white/[0.07] px-3.5 py-2.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-['IBM_Plex_Mono',monospace] text-[11px] font-semibold text-cyan-200/80">
              {`F${String(frame?.fh ?? 0).padStart(3, "0")}`}
            </span>
            <span
              data-testid="sounding-valid-time"
              className="truncate text-[11px] text-white/55"
            >
              {formatValidTime(frame?.valid_time)}
            </span>
          </div>
          <input
            type="range"
            data-testid="sounding-scrubber"
            aria-label="Forecast hour"
            min={0}
            max={Math.max(0, frames.length - 1)}
            step={1}
            value={activeIndex}
            onChange={(event) => setOverrideFh(frameHours[Number(event.target.value)] ?? null)}
            className="mt-1.5 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/[0.12] accent-cyan-300 pointer-coarse:h-2.5"
          />
        </div>
      ) : null}
    </aside>
  );
}

export default SoundingPanel;
