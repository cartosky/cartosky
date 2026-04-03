import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ChevronDown, ChevronUp, Loader2, X } from "lucide-react";
import {
  fetchAnchorWeather,
  fetchAnchorAfd,
  type AnchorWeatherResponse,
  type AnchorAfdResponse,
  type NwsForecastPeriod,
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

type TabId = "current" | "forecast" | "afd";

// ---------------------------------------------------------------------------
// Shared class constants (matching TWF Share Modal patterns)
// ---------------------------------------------------------------------------

const modalCardClass =
  "glass-overlay my-2 flex max-h-[calc(100dvh-1rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl text-white sm:my-4 sm:max-h-[calc(100dvh-2rem)]";

const sectionCardClass = "glass-overlay-section rounded-2xl";

const tabButtonBase =
  "flex-1 px-3 py-2.5 text-xs font-medium transition-colors sm:text-sm";

const tabButtonActive =
  "border-b-2 border-emerald-400/80 text-white";

const tabButtonInactive =
  "border-b-2 border-transparent text-white/50 hover:text-white/70";

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

function forecastStaleness(generatedAt: string | null | undefined): StalenessLevel {
  if (!generatedAt) return "stale";
  try {
    const ageMs = Date.now() - new Date(generatedAt).getTime();
    const ageHrs = ageMs / 3_600_000;
    if (ageHrs < 6) return "fresh";
    if (ageHrs < 12) return "warning";
    return "stale";
  } catch {
    return "stale";
  }
}

function afdStaleness(issuedAt: string | null | undefined): StalenessLevel {
  if (!issuedAt) return "stale";
  try {
    const ageMs = Date.now() - new Date(issuedAt).getTime();
    const ageHrs = ageMs / 3_600_000;
    if (ageHrs < 12) return "fresh";
    if (ageHrs < 24) return "warning";
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

function ForecastPeriodRow({ period }: { period: NwsForecastPeriod }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-white/[0.04] last:border-b-0">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-white/[0.03]"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-medium text-white">{period.name}</span>
            <span className="tabular-nums text-xs font-semibold text-white/90">
              {period.tempF != null ? `${period.tempF}°F` : "—"}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-white/50">{period.shortForecast}</div>
          {period.windSpeed ? (
            <div className="mt-0.5 text-[11px] text-white/40">
              Wind: {period.windDirection ?? ""} {period.windSpeed}
              {period.precipProbability != null && period.precipProbability > 0
                ? ` · ${period.precipProbability}% precip`
                : ""}
            </div>
          ) : null}
        </div>
        <div className="shrink-0 text-white/30">
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </div>
      </button>
      {expanded && period.detailedForecast ? (
        <div className="px-3.5 pb-3 text-xs leading-relaxed text-white/60">
          {period.detailedForecast}
        </div>
      ) : null}
    </div>
  );
}

function ForecastTab({
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
  if (loading) return <SkeletonBlock lines={10} />;
  if (error) return <ErrorBlock message={error} onRetry={onRetry} />;
  if (!weather?.forecast?.periods?.length) return <ErrorBlock message="No forecast data available." />;

  const forecast = weather.forecast;
  const staleness = forecastStaleness(forecast.generatedAt);

  return (
    <div className="space-y-1">
      <div className={`${sectionCardClass} divide-y divide-white/[0.04] overflow-hidden`}>
        {forecast.periods.map((period) => (
          <ForecastPeriodRow key={period.number} period={period} />
        ))}
      </div>
      <div className={`pt-2 text-center text-[11px] ${stalenessColor(staleness)}`}>
        {staleness === "stale" ? "Forecast may be outdated" : `Generated at ${formatTime(forecast.generatedAt)}`}
      </div>
    </div>
  );
}

function AfdTab({
  afd,
  loading,
  error,
  onRetry,
}: {
  afd: AnchorAfdResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading) return <SkeletonBlock lines={12} />;
  if (error) return <ErrorBlock message={error} onRetry={onRetry} />;
  if (!afd || !afd.productText) {
    return <ErrorBlock message="No Area Forecast Discussion available for this office." />;
  }

  const staleness = afdStaleness(afd.issuedAt);

  return (
    <div className="space-y-1">
      <div className={`${sectionCardClass} overflow-hidden`}>
        <pre className="legend-scroll max-h-[50vh] overflow-y-auto whitespace-pre-wrap break-words px-3.5 py-3 font-mono text-[11px] leading-relaxed text-white/80 sm:text-xs">
          {afd.productText}
        </pre>
      </div>
      <div className={`pt-2 text-center text-[11px] ${stalenessColor(staleness)}`}>
        {staleness === "stale"
          ? "This discussion may be outdated"
          : `Issued at ${formatTime(afd.issuedAt)}${afd.officeName ? ` by ${afd.officeName}` : ""}`}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function NwsCityModal({ open, onClose, anchor }: NwsCityModalProps) {
  // Tab state
  const [activeTab, setActiveTab] = useState<TabId>("current");

  // Weather data (obs + forecast)
  const [weather, setWeather] = useState<AnchorWeatherResponse | null>(null);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherError, setWeatherError] = useState<string | null>(null);

  // AFD data (lazy loaded)
  const [afd, setAfd] = useState<AnchorAfdResponse | null>(null);
  const [afdLoading, setAfdLoading] = useState(false);
  const [afdError, setAfdError] = useState<string | null>(null);
  const afdFetchedRef = useRef(false);

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
    setActiveTab("current");
    setWeather(null);
    setWeatherError(null);
    setAfd(null);
    setAfdError(null);
    afdFetchedRef.current = false;

    const controller = new AbortController();
    fetchWeather(controller.signal);

    return () => {
      controller.abort();
    };
  }, [open, fetchWeather]);

  // --------------------------------------------------
  // Fetch AFD on first tab switch
  // --------------------------------------------------
  const fetchAfdData = useCallback(
    (signal?: AbortSignal) => {
      setAfdLoading(true);
      setAfdError(null);
      fetchAnchorAfd(anchor.id, signal)
        .then((result) => {
          if (!signal?.aborted) {
            setAfd(result);
            setAfdLoading(false);
          }
        })
        .catch((err) => {
          if (!signal?.aborted) {
            setAfdError(err instanceof Error ? err.message : "Failed to load AFD.");
            setAfdLoading(false);
          }
        });
    },
    [anchor.id],
  );

  useEffect(() => {
    if (!open || activeTab !== "afd" || afdFetchedRef.current) return;
    afdFetchedRef.current = true;

    const controller = new AbortController();
    fetchAfdData(controller.signal);

    return () => {
      controller.abort();
    };
  }, [open, activeTab, fetchAfdData]);

  // --------------------------------------------------
  // Retry handlers
  // --------------------------------------------------
  const retryWeather = useCallback(() => {
    fetchWeather();
  }, [fetchWeather]);

  const retryAfd = useCallback(() => {
    afdFetchedRef.current = false;
    fetchAfdData();
  }, [fetchAfdData]);

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  if (!open) return null;

  const tabs: { id: TabId; label: string }[] = [
    { id: "current", label: "Current" },
    { id: "forecast", label: "Forecast" },
    { id: "afd", label: "AFD" },
  ];

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

        {/* Tab bar */}
        <div className="flex shrink-0 border-b border-white/[0.06]">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`${tabButtonBase} ${activeTab === tab.id ? tabButtonActive : tabButtonInactive}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="legend-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {activeTab === "current" ? (
            <CurrentTab
              weather={weather}
              loading={weatherLoading}
              error={weatherError}
              onRetry={retryWeather}
            />
          ) : null}
          {activeTab === "forecast" ? (
            <ForecastTab
              weather={weather}
              loading={weatherLoading}
              error={weatherError}
              onRetry={retryWeather}
            />
          ) : null}
          {activeTab === "afd" ? (
            <AfdTab
              afd={afd}
              loading={afdLoading}
              error={afdError}
              onRetry={retryAfd}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
