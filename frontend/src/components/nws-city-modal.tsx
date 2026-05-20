import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, Loader2, X } from "lucide-react";
import { Link } from "react-router-dom";
import {
  fetchAnchorWeather,
  type AnchorWeatherResponse,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type NwsCityModalProps = {
  open: boolean;
  onClose: () => void;
  anchor: {
    id: string;
    city: string;
    state: string;
    st: string;
  };
};

// ---------------------------------------------------------------------------
// Shared class constants (matching TWF Share Modal patterns)
// ---------------------------------------------------------------------------

const modalCardClass =
  "glass-overlay my-2 flex max-h-[calc(100dvh-1rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl text-white sm:my-4 sm:max-h-[calc(100dvh-2rem)]";

const sectionCardClass = "glass-overlay-section rounded-2xl";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(isoString: string | null | undefined): string {
  if (!isoString) return "";
  try {
    const date = new Date(isoString);
    const now = Date.now();
    const diffMs = now - date.getTime();
    if (diffMs < 0) return "just now";
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin} min ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ago`;
  } catch {
    return "";
  }
}

function formatTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    return new Date(isoString).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return "—";
  }
}

type StalenessLevel = "fresh" | "warning" | "stale";

function observationStaleness(observedAt: string | null | undefined): StalenessLevel {
  if (!observedAt) return "stale";
  try {
    const ageMs = Date.now() - new Date(observedAt).getTime();
    const ageMin = ageMs / 60_000;
    if (ageMin < 30) return "fresh";
    if (ageMin < 90) return "warning";
    return "stale";
  } catch {
    return "stale";
  }
}

function stalenessColor(level: StalenessLevel): string {
  if (level === "warning") return "text-amber-300";
  if (level === "stale") return "text-amber-300";
  return "text-white/50";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonBlock({ lines = 4 }: { lines?: number }) {
  return (
    <div className="animate-pulse space-y-3 py-2">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded bg-white/[0.06]"
          style={{ width: `${70 + Math.random() * 30}%` }}
        />
      ))}
    </div>
  );
}

function ErrorBlock({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-6">
      <AlertCircle className="h-6 w-6 text-red-400/80" />
      <p className="text-center text-xs text-white/60">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex h-7 items-center rounded-md bg-white/[0.08] px-3 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.12]"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

function ObsRow({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex items-baseline justify-between gap-2 py-1.5">
      <span className="text-xs text-white/50">{label}</span>
      <span className="text-sm font-medium tabular-nums text-white">{value}</span>
    </div>
  );
}

function CurrentTab({
  weather,
  loading,
  error,
  onRetry,
}: {
  weather: AnchorWeatherResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading) return <SkeletonBlock lines={8} />;
  if (error) return <ErrorBlock message={error} onRetry={onRetry} />;
  if (!weather?.observation) return <ErrorBlock message="No observation data available." />;

  const obs = weather.observation;
  const staleness = observationStaleness(obs.observedAt);
  const rel = relativeTime(obs.observedAt);

  return (
    <div className="space-y-1">
      <div className="pb-2 text-center">
        <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-200/72">
          Current Conditions
        </div>
      </div>
      {obs.textDescription ? (
        <div className="pb-2 text-center text-base font-medium text-white">{obs.textDescription}</div>
      ) : null}

      <div className={`${sectionCardClass} divide-y divide-white/[0.04] px-3.5`}>
        <ObsRow label="Temperature" value={obs.tempF != null ? `${obs.tempF}°F` : null} />
        <ObsRow label="Dewpoint" value={obs.dewpointF != null ? `${obs.dewpointF}°F` : null} />
        <ObsRow label="Humidity" value={obs.relativeHumidity != null ? `${obs.relativeHumidity}%` : null} />
        <ObsRow
          label="Wind"
          value={
            obs.windSpeedMph != null
              ? `${obs.windDirection ?? ""} ${obs.windSpeedMph} mph`.trim()
              : null
          }
        />
        {obs.windGustMph != null ? (
          <ObsRow label="Gusts" value={`${obs.windGustMph} mph`} />
        ) : null}
        <ObsRow label="Wind Chill" value={obs.windChillF != null ? `${obs.windChillF}°F` : null} />
        <ObsRow label="Heat Index" value={obs.heatIndexF != null ? `${obs.heatIndexF}°F` : null} />
        <ObsRow label="Pressure" value={obs.pressureInHg != null ? `${obs.pressureInHg} inHg` : null} />
        <ObsRow label="Visibility" value={obs.visibilityMi != null ? `${obs.visibilityMi} mi` : null} />
        {obs.precipLastHourIn != null ? (
          <ObsRow label="Precip (1hr)" value={`${obs.precipLastHourIn} in`} />
        ) : null}
      </div>

      <div className={`pt-2 text-center text-[11px] ${stalenessColor(staleness)}`}>
        {staleness === "stale" ? "Observation may be outdated" : null}
        {staleness !== "stale" ? (
          <>
            Observed at {formatTime(obs.observedAt)}
            {rel ? ` (${rel})` : ""}
            {obs.stationId ? ` from ${obs.stationId}` : ""}
          </>
        ) : null}
      </div>

      {weather.meta.observationDegraded ? (
        <div className="pt-1 text-center text-[11px] text-amber-300/80">
          Observation quality may be reduced
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function NwsCityModal({ open, onClose, anchor }: NwsCityModalProps) {
  // Weather data (obs + forecast)
  const [weather, setWeather] = useState<AnchorWeatherResponse | null>(null);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherError, setWeatherError] = useState<string | null>(null);

  // Track open cycle
  const wasOpenRef = useRef(false);

  // --------------------------------------------------
  // Escape key + scroll lock
  // --------------------------------------------------
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  // --------------------------------------------------
  // Fetch weather on open
  // --------------------------------------------------
  const fetchWeather = useCallback(
    (signal?: AbortSignal) => {
      setWeatherLoading(true);
      setWeatherError(null);
      fetchAnchorWeather(anchor.id, signal)
        .then((result) => {
          if (!signal?.aborted) {
            setWeather(result);
            setWeatherLoading(false);
          }
        })
        .catch((err) => {
          if (!signal?.aborted) {
            setWeatherError(err instanceof Error ? err.message : "Failed to load weather data.");
            setWeatherLoading(false);
          }
        });
    },
    [anchor.id],
  );

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) return;
    wasOpenRef.current = true;

    // Reset state for new open cycle
    setWeather(null);
    setWeatherError(null);

    const controller = new AbortController();
    fetchWeather(controller.signal);

    return () => {
      controller.abort();
    };
  }, [open, fetchWeather]);

  // --------------------------------------------------
  // Retry handlers
  // --------------------------------------------------
  const retryWeather = useCallback(() => {
    fetchWeather();
  }, [fetchWeather]);

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  if (!open) return null;

  const forecastQuery = `${anchor.city}, ${anchor.st}`;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-slate-950/46 p-2 backdrop-blur-sm backdrop-brightness-[0.62] backdrop-saturate-75 sm:items-center sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Weather for ${anchor.city}`}
    >
      <div
        className={modalCardClass}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between px-4 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-white sm:text-base">
              {anchor.city}, {anchor.st}
            </h2>
            {weather ? (
              <div className="mt-0.5 text-[11px] text-white/40">
                NWS {weather.state}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-white/[0.08] text-white/80 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-colors hover:bg-white/[0.12]"
            aria-label="Close weather modal"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="legend-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4">
          <CurrentTab
            weather={weather}
            loading={weatherLoading}
            error={weatherError}
            onRetry={retryWeather}
          />

          <div className="mt-4 pt-2">
            <Link
              to={{
                pathname: "/forecast",
                search: `?q=${encodeURIComponent(forecastQuery)}`,
              }}
              onClick={onClose}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-cyan-300/30 bg-[linear-gradient(135deg,rgba(16,36,56,0.94)_0%,rgba(26,79,104,0.94)_52%,rgba(106,183,212,0.94)_100%)] px-4 py-3 text-sm font-semibold text-white shadow-[0_14px_34px_rgba(17,68,92,0.34)] transition-all hover:brightness-110"
            >
              Open Full Forecast
              <ArrowRight className="h-4 w-4" />
            </Link>
            <p className="mt-2 text-center text-[11px] text-white/45">
              Opens the Forecast page for {forecastQuery} with hourly, extended, model, and discussion details.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
