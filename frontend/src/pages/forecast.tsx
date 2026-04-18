import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Cloud,
  CloudDrizzle,
  CloudLightning,
  CloudMoon,
  CloudRain,
  CloudSnow,
  CloudSun,
  MapPinned,
  Moon,
  Search,
  Sun,
  Wind,
  X,
} from "lucide-react";

import { API_ORIGIN, MAP_VIEW_DEFAULTS } from "@/lib/config";
import { buildPermalinkSearch } from "@/lib/permalink";

// ── Types ─────────────────────────────────────────────────────────────

type LocationResult = {
  display_name: string;
  latitude: number;
  longitude: number;
  timezone: string | null;
  country_code: string | null;
};

type CurrentData = {
  source: string;
  observed_at: string | null;
  station: { id: string; name: string; distance_km: number | null } | null;
  temperature_f: number | null;
  dewpoint_f: number | null;
  humidity_pct: number | null;
  wind_dir_deg: number | null;
  wind_speed_mph: number | null;
  wind_gust_mph: number | null;
  pressure_mb: number | null;
  visibility_mi: number | null;
  icon: string;
  short_text: string | null;
  quality: {
    is_fallback: boolean;
    is_stale: boolean;
    freshness: string;
    age_minutes: number | null;
  };
};

type HourlyEntry = {
  time: string | null;
  temperature_f: number | null;
  pop_pct: number | null;
  weather_code: string;
  short_text: string | null;
  wind_speed_mph: number | null;
  wind_dir_deg: number | null;
};

type DailyEntry = {
  date: string | null;
  high_f: number | null;
  low_f: number | null;
  pop_pct: number | null;
  qpf_in: number | null;
  snow_in: number | null;
  wind_speed_mph: number | null;
  icon: string;
  short_text: string | null;
};

type TextForecastPeriod = {
  name: string | null;
  is_daytime: boolean;
  temperature_f: number | null;
  wind_text: string | null;
  short_text: string | null;
  detailed_text: string | null;
};

type AlertEntry = {
  id: string | null;
  event: string | null;
  severity: string | null;
  urgency: string | null;
  effective: string | null;
  expires: string | null;
  headline: string | null;
  areas: string[];
  description: string | null;
};

type ForecastPayload = {
  location: {
    display_name: string;
    latitude: number;
    longitude: number;
    country_code: string | null;
    resolved_by: string;
  };
  source_status: {
    primary_region_mode: string;
    nws: string;
    open_meteo: string;
  };
  current: CurrentData;
  hourly: HourlyEntry[];
  daily: DailyEntry[];
  official_text_forecast: {
    source: string;
    generated_at: string | null;
    periods: TextForecastPeriod[];
  } | null;
  afd: {
    office: string;
    issued_at: string | null;
    headline: string;
    text: string | null;
  } | null;
  alerts: AlertEntry[];
  attribution: {
    current: string | null;
    hourly: string | null;
    daily: string | null;
  };
  freshness: {
    current: { state: string | null; observed_at: string | null; age_minutes: number | null };
    afd: { state: string; issued_at: string | null; age_hours: number | null };
  };
};

// ── Helpers ───────────────────────────────────────────────────────────

function degreesToCardinal(deg: number | null): string {
  if (deg === null) return "--";
  const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return dirs[Math.round(deg / 22.5) % 16];
}

function formatHour(time: string | null): string {
  if (!time) return "--";
  const match = time.match(/T(\d{2}):/);
  if (!match) return "--";
  const h = parseInt(match[1], 10);
  if (h === 0) return "12a";
  if (h === 12) return "12p";
  return h < 12 ? `${h}a` : `${h - 12}p`;
}

function formatObservedAt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

function formatIssuedAt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(d);
}

function formatDayLabel(date: string | null, index: number): string {
  if (!date) return "--";
  if (index === 0) {
    const today = new Date().toLocaleDateString("en-CA");
    if (date === today) return "Today";
  }
  const d = new Date(date + "T12:00:00");
  if (isNaN(d.getTime())) return "--";
  return d.toLocaleDateString("en-US", { weekday: "short" });
}

function alertSeverityStyles(severity: string | null) {
  switch ((severity || "").toLowerCase()) {
    case "extreme":
      return { border: "border-rose-300/25", bg: "bg-rose-300/12", text: "text-rose-100", badge: "bg-rose-300/20 text-rose-100" };
    case "severe":
      return { border: "border-orange-300/22", bg: "bg-orange-300/10", text: "text-orange-100", badge: "bg-orange-300/18 text-orange-100" };
    case "moderate":
      return { border: "border-amber-300/20", bg: "bg-amber-300/8", text: "text-amber-100", badge: "bg-amber-300/16 text-amber-100" };
    default:
      return { border: "border-yellow-300/18", bg: "bg-yellow-300/[0.06]", text: "text-yellow-100", badge: "bg-yellow-300/14 text-yellow-100" };
  }
}

function freshnessLabel(state: string | null, ageMinutes: number | null): string {
  if (state === "modeled") return "Modeled";
  if ((state === "fresh" || state === "aging") && ageMinutes !== null) return `${ageMinutes}m ago`;
  if (state === "stale") return ageMinutes !== null ? `${ageMinutes}m ago · stale` : "Stale";
  return "Recent";
}

function freshnessColor(state: string | null): string {
  if (state === "fresh") return "text-emerald-400";
  if (state === "aging") return "text-amber-400";
  if (state === "stale") return "text-rose-400";
  if (state === "modeled") return "text-cyan-400";
  return "text-white/45";
}

function viewerHref(lat: number, lon: number): string {
  return `/viewer${buildPermalinkSearch({ region: MAP_VIEW_DEFAULTS.region, lat, lon, z: 7 })}`;
}

// ── Weather Icon ──────────────────────────────────────────────────────

function WeatherIcon({ code, className }: { code: string; className?: string }) {
  const cls = className ?? "h-5 w-5";
  switch (code) {
    case "clear-day":
      return <Sun className={cls} />;
    case "clear-night":
      return <Moon className={cls} />;
    case "partly-cloudy-day":
      return <CloudSun className={cls} />;
    case "partly-cloudy-night":
      return <CloudMoon className={cls} />;
    case "cloudy":
    case "fog":
      return <Cloud className={cls} />;
    case "drizzle":
      return <CloudDrizzle className={cls} />;
    case "rain":
    case "sleet":
      return <CloudRain className={cls} />;
    case "snow":
      return <CloudSnow className={cls} />;
    case "thunderstorm":
      return <CloudLightning className={cls} />;
    case "wind":
      return <Wind className={cls} />;
    default:
      return <Cloud className={cls} />;
  }
}

// ── Section Eyebrow ───────────────────────────────────────────────────

function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

// ── Hourly Strip ──────────────────────────────────────────────────────

function HourlyStrip({ hourly }: { hourly: HourlyEntry[] }) {
  const entries = hourly.slice(0, 24);
  if (entries.length === 0) return null;
  return (
    <div>
      <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">Next 24 Hours</div>
      <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1.5">
        {entries.map((entry, i) => (
          <div
            key={i}
            className="flex min-w-[3.75rem] flex-none flex-col items-center rounded-2xl border border-white/8 bg-slate-950/28 px-2 py-3"
          >
            <div className="text-[10px] text-white/48">{formatHour(entry.time)}</div>
            <WeatherIcon code={entry.weather_code} className="mt-2 h-4 w-4 text-cyan-200/75" />
            <div className="mt-2 text-sm font-semibold text-white">{entry.temperature_f ?? "--"}°</div>
            <div className="mt-1 h-3 text-[10px] text-cyan-300/65">
              {entry.pop_pct && entry.pop_pct > 0 ? `${entry.pop_pct}%` : ""}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Daily Forecast ────────────────────────────────────────────────────

function DailyForecast({ daily }: { daily: DailyEntry[] }) {
  const entries = daily.slice(0, 7);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
      {entries.map((entry, i) => (
        <div
          key={i}
          className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-4"
        >
          <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
            {formatDayLabel(entry.date, i)}
          </div>
          <WeatherIcon code={entry.icon} className="mt-3 h-6 w-6 text-cyan-200/80" />
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-lg font-semibold text-white">{entry.high_f ?? "--"}°</span>
            <span className="text-sm text-white/40">{entry.low_f ?? "--"}°</span>
          </div>
          <div className="mt-1.5 text-xs leading-5 text-white/55 line-clamp-2">{entry.short_text ?? ""}</div>
          {entry.pop_pct && entry.pop_pct > 0 ? (
            <div className="mt-2 text-[10px] text-cyan-300/65">{entry.pop_pct}%</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// ── Alerts Banner ─────────────────────────────────────────────────────

function AlertsBanner({ alerts }: { alerts: AlertEntry[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(i: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) {
        next.delete(i);
      } else {
        next.add(i);
      }
      return next;
    });
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert, i) => {
        const styles = alertSeverityStyles(alert.severity);
        const isOpen = expanded.has(i);
        return (
          <div key={alert.id ?? i} className={`rounded-[1.4rem] border ${styles.border} ${styles.bg} overflow-hidden`}>
            <button
              type="button"
              onClick={() => toggle(i)}
              className="flex w-full items-start gap-3 p-4 text-left"
            >
              <AlertTriangle className={`mt-0.5 h-4 w-4 flex-none ${styles.text}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`text-sm font-semibold ${styles.text}`}>
                    {alert.event ?? "Weather Alert"}
                  </span>
                  {alert.severity ? (
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${styles.badge}`}>
                      {alert.severity}
                    </span>
                  ) : null}
                </div>
                {alert.headline ? (
                  <p className="mt-1 text-sm text-white/72">{alert.headline}</p>
                ) : null}
                {alert.areas.length > 0 ? (
                  <p className="mt-1 text-xs text-white/40">{alert.areas.slice(0, 3).join("; ")}</p>
                ) : null}
              </div>
              <div className="flex-none text-white/40">
                {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </div>
            </button>
            {isOpen && alert.description ? (
              <div className="border-t border-white/8 px-4 pb-4 pt-3">
                <p className="text-sm leading-7 text-white/65 whitespace-pre-wrap">{alert.description}</p>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ── Text Forecast Section ─────────────────────────────────────────────

function TextForecastSection({ data }: { data: ForecastPayload["official_text_forecast"] }) {
  const [showAll, setShowAll] = useState(false);
  if (!data || data.periods.length === 0) return null;

  const visiblePeriods = showAll ? data.periods : data.periods.slice(0, 6);

  return (
    <section className="border-b border-white/8 bg-[#091423] px-5 py-12 md:px-8 md:py-16">
      <div className="mx-auto max-w-6xl">
        <SectionEyebrow>Official Forecast</SectionEyebrow>
        <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <h2 className="text-2xl font-semibold tracking-tight text-white">NWS Forecast Periods</h2>
          {data.generated_at && (
            <div className="text-xs text-white/35">Generated {formatObservedAt(data.generated_at)}</div>
          )}
        </div>
        <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visiblePeriods.map((period, i) => (
            <div key={i} className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-white/45">
                    {period.name ?? (period.is_daytime ? "Day" : "Night")}
                  </div>
                  <div className="mt-3 text-sm leading-6 text-white/80">
                    {period.short_text ?? "Forecast unavailable"}
                  </div>
                  {period.wind_text ? (
                    <div className="mt-2 text-xs text-white/45">Wind: {period.wind_text}</div>
                  ) : null}
                </div>
                <div className="flex-none text-right">
                  <div className="text-2xl font-semibold tracking-tight text-cyan-100">
                    {period.temperature_f ?? "--"}°
                  </div>
                  <div className="mt-1 text-[10px] uppercase tracking-[0.15em] text-white/35">
                    {period.is_daytime ? "High" : "Low"}
                  </div>
                </div>
              </div>
              {period.detailed_text ? (
                <p className="mt-4 border-t border-white/8 pt-4 text-xs leading-6 text-white/50">
                  {period.detailed_text}
                </p>
              ) : null}
            </div>
          ))}
        </div>
        {data.periods.length > 6 ? (
          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="inline-flex items-center gap-2 rounded-xl border border-white/12 bg-white/[0.04] px-4 py-2 text-sm text-white/65 transition hover:border-white/20 hover:bg-white/[0.06]"
            >
              {showAll ? (
                <>Show less <ChevronUp className="h-3.5 w-3.5" /></>
              ) : (
                <>Show all {data.periods.length} periods <ChevronDown className="h-3.5 w-3.5" /></>
              )}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

// ── AFD Section ───────────────────────────────────────────────────────

function AfdSection({ afd }: { afd: NonNullable<ForecastPayload["afd"]> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <section className="border-b border-white/8 bg-[#08111f] px-5 py-12 md:px-8 md:py-16">
      <div className="mx-auto max-w-6xl">
        <SectionEyebrow>NWS Discussion</SectionEyebrow>
        <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <h2 className="text-2xl font-semibold tracking-tight text-white">Area Forecast Discussion</h2>
          <div className="flex items-center gap-4">
            <div className="text-xs text-white/35">
              {afd.office}
              {afd.issued_at ? ` · ${formatIssuedAt(afd.issued_at)}` : ""}
            </div>
          </div>
        </div>
        <div className="mt-8 rounded-[1.6rem] border border-white/8 bg-white/[0.02]">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
          >
            <div className="text-sm font-medium text-white/75">
              {expanded ? "Collapse discussion" : "Expand Area Forecast Discussion"}
            </div>
            {expanded ? (
              <ChevronUp className="h-4 w-4 flex-none text-white/40" />
            ) : (
              <ChevronDown className="h-4 w-4 flex-none text-white/40" />
            )}
          </button>
          {expanded && afd.text ? (
            <div className="border-t border-white/8 px-5 pb-5 pt-4">
              <pre className="max-h-[32rem] overflow-y-auto font-mono text-xs leading-6 text-white/60 whitespace-pre-wrap break-words">
                {afd.text}
              </pre>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function Forecast() {
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<LocationResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState<LocationResult | null>(null);
  const [forecast, setForecast] = useState<ForecastPayload | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const searchContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q) {
      setQuery(q);
      void loadForecastByQuery(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const trimmed = query.trim();
    if (trimmed.length < 2 || (selectedLocation && query === selectedLocation.display_name)) {
      if (trimmed.length < 2) {
        setSearchResults([]);
        setShowDropdown(false);
      }
      return;
    }

    setIsSearching(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API_ORIGIN}/api/locations/search?q=${encodeURIComponent(trimmed)}`);
        if (!res.ok) throw new Error("Search unavailable");
        const data = (await res.json()) as { results?: LocationResult[] };
        const results = data.results ?? [];
        setSearchResults(results);
        setShowDropdown(results.length > 0);
      } catch {
        setSearchResults([]);
        setShowDropdown(false);
      } finally {
        setIsSearching(false);
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, selectedLocation]);

  async function loadForecastByQuery(q: string) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;

    setIsLoading(true);
    setError(null);
    setForecast(null);
    setShowDropdown(false);

    try {
      const res = await fetch(
        `${API_ORIGIN}/api/forecast-page/by-query?q=${encodeURIComponent(q)}`,
        { signal: controller.signal }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? "Forecast unavailable for this location.");
      }
      const data = (await res.json()) as ForecastPayload;
      setForecast(data);
      setQuery(data.location.display_name);
      setSelectedLocation({
        display_name: data.location.display_name,
        latitude: data.location.latitude,
        longitude: data.location.longitude,
        timezone: null,
        country_code: data.location.country_code,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable.");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadForecastByCoords(lat: number, lon: number) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;

    setIsLoading(true);
    setError(null);
    setForecast(null);
    setShowDropdown(false);

    try {
      const res = await fetch(
        `${API_ORIGIN}/api/forecast-page?lat=${lat}&lon=${lon}`,
        { signal: controller.signal }
      );
      if (!res.ok) throw new Error("Forecast unavailable for this location.");
      const data = (await res.json()) as ForecastPayload;
      setForecast(data);
      setQuery(data.location.display_name);
      setSelectedLocation({
        display_name: data.location.display_name,
        latitude: data.location.latitude,
        longitude: data.location.longitude,
        timezone: null,
        country_code: data.location.country_code,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable.");
    } finally {
      setIsLoading(false);
    }
  }

  function selectLocation(location: LocationResult) {
    setSelectedLocation(location);
    setQuery(location.display_name);
    setShowDropdown(false);
    setSearchResults([]);
    void loadForecastByCoords(location.latitude, location.longitude);
  }

  function clearSearch() {
    setQuery("");
    setSelectedLocation(null);
    setForecast(null);
    setError(null);
    setSearchResults([]);
    setShowDropdown(false);
    if (loadAbortRef.current) loadAbortRef.current.abort();
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  const observedAtLabel = forecast
    ? formatObservedAt(forecast.freshness.current.observed_at)
    : null;

  return (
    <div className="-mx-5 -mt-12 space-y-0 md:-mx-8 md:-mt-16">
      {/* ── Hero ── */}
      <section className="relative overflow-hidden border-b border-white/8 bg-[#07111f] px-5 pb-16 pt-28 md:px-8 md:pt-32">
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-95"
          style={{
            backgroundImage: `
              linear-gradient(90deg, rgba(6,12,24,0.95) 0%, rgba(6,12,24,0.84) 42%, rgba(6,12,24,0.66) 100%),
              linear-gradient(180deg, rgba(7,17,31,0.72), rgba(7,17,31,0.9)),
              url(/assets/hero-image.png)
            `,
            backgroundSize: "auto, auto, cover",
            backgroundPosition: "center, center, center right",
          }}
        />

        <div className="relative mx-auto grid min-h-[calc(100svh-10rem)] max-w-6xl items-start gap-12 py-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
          {/* Left: Branding + Search */}
          <div className="max-w-2xl">
            <SectionEyebrow>Forecast Preview</SectionEyebrow>
            <h1 className="mt-8 text-balance text-5xl font-semibold tracking-[-0.04em] text-white md:text-7xl md:leading-[0.98]">
              Local weather,
              <br />
              <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic text-cyan-200">
                clearly briefed.
              </span>
            </h1>
            <p className="mt-7 max-w-xl text-base leading-8 text-white/74 md:text-lg">
              Search any city or zip code to pull up current conditions, a short-range outlook, and a direct handoff to the map viewer when you need deeper analysis.
            </p>

            {/* Search box */}
            <div ref={searchContainerRef} className="relative mt-10">
              <div className="rounded-[1.7rem] border border-white/10 bg-slate-950/35 p-4 shadow-[0_24px_70px_rgba(0,0,0,0.28)] backdrop-blur-md">
                <label className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                  <Search className="h-4 w-4 flex-none text-cyan-200/85" />
                  <input
                    ref={inputRef}
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      if (selectedLocation && e.target.value !== selectedLocation.display_name) {
                        setSelectedLocation(null);
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && query.trim().length >= 2) {
                        setShowDropdown(false);
                        if (searchResults.length > 0) {
                          selectLocation(searchResults[0]);
                        } else {
                          void loadForecastByQuery(query.trim());
                        }
                      }
                      if (e.key === "Escape") {
                        setShowDropdown(false);
                      }
                    }}
                    onFocus={() => {
                      if (searchResults.length > 0) setShowDropdown(true);
                    }}
                    placeholder="Search city, state, or zip code"
                    className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/35"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  {isSearching ? (
                    <div className="h-3.5 w-3.5 flex-none animate-spin rounded-full border border-cyan-300/30 border-t-cyan-300" />
                  ) : selectedLocation ? (
                    <button
                      type="button"
                      onClick={clearSearch}
                      className="flex-none rounded-full p-0.5 text-white/35 transition hover:text-white/65"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </label>

                {showDropdown && searchResults.length > 0 ? (
                  <div className="mt-3 space-y-1">
                    {searchResults.slice(0, 6).map((result, i) => (
                      <button
                        key={i}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => selectLocation(result)}
                        className="w-full rounded-2xl border border-white/8 bg-white/[0.03] px-4 py-3 text-left transition duration-100 hover:border-white/15 hover:bg-white/[0.05]"
                      >
                        <div className="text-sm font-medium text-white">{result.display_name}</div>
                        {result.country_code && result.country_code !== "US" ? (
                          <div className="mt-0.5 text-xs text-white/38">{result.country_code}</div>
                        ) : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          {/* Right: Data Panel */}
          <div className="rounded-[2rem] border border-white/10 bg-slate-950/35 p-5 shadow-[0_28px_90px_rgba(0,0,0,0.26)] backdrop-blur-md">
            {/* Header */}
            <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/8 pb-4">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-[0.26em] text-cyan-200/70">
                  Location Briefing
                </div>
                <div className="mt-3 truncate text-3xl font-semibold tracking-tight text-white">
                  {forecast?.location.display_name ?? "Search a location"}
                </div>
                <div className="mt-2 text-sm text-white/52">
                  {isLoading
                    ? "Loading forecast…"
                    : observedAtLabel
                    ? `Observed ${observedAtLabel}`
                    : "Enter a city, state, or zip code above"}
                </div>
              </div>
              {forecast ? (
                <Link
                  to={viewerHref(forecast.location.latitude, forecast.location.longitude)}
                  className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-4 py-2.5 text-sm font-semibold text-slate-950 shadow-[0_12px_30px_rgba(35,196,255,0.15)] transition duration-200 hover:-translate-y-px hover:brightness-105"
                >
                  Open In Viewer
                  <ArrowRight className="h-4 w-4" />
                </Link>
              ) : null}
            </div>

            {/* Error state */}
            {error ? (
              <div className="mt-6 rounded-2xl border border-rose-300/18 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">
                {error}
              </div>
            ) : null}

            {/* Loading skeleton */}
            {isLoading && !error ? (
              <div className="mt-6 space-y-4 animate-pulse">
                <div className="grid gap-4 lg:grid-cols-[0.78fr_1.22fr]">
                  <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.02] p-5">
                    <div className="h-2 w-24 rounded bg-white/8" />
                    <div className="mt-5 h-12 w-20 rounded-xl bg-white/8" />
                    <div className="mt-3 h-4 w-28 rounded bg-white/6" />
                    <div className="mt-6 space-y-3">
                      {[1, 2, 3].map((k) => (
                        <div key={k} className="flex justify-between">
                          <div className="h-3 w-20 rounded bg-white/6" />
                          <div className="h-3 w-16 rounded bg-white/6" />
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.02] p-5">
                    <div className="h-2 w-24 rounded bg-white/8" />
                    <div className="-mx-1 mt-4 flex gap-1.5 overflow-hidden px-1">
                      {[1, 2, 3, 4, 5, 6].map((k) => (
                        <div key={k} className="h-20 w-14 flex-none rounded-2xl bg-white/6" />
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            {/* Loaded content */}
            {forecast && !isLoading ? (
              <div className="mt-6 space-y-4">
                <div className="grid gap-4 lg:grid-cols-[0.78fr_1.22fr]">
                  {/* Current conditions */}
                  <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-5">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">
                      Current Conditions
                    </div>
                    <div className="mt-4 flex items-start gap-3">
                      <WeatherIcon
                        code={forecast.current.icon}
                        className="mt-1 h-9 w-9 flex-none text-cyan-200/80"
                      />
                      <div>
                        <div className="text-5xl font-semibold tracking-tight text-white">
                          {forecast.current.temperature_f ?? "--"}°
                        </div>
                        <div className="mt-1.5 text-sm text-cyan-100/85">
                          {forecast.current.short_text ?? ""}
                        </div>
                      </div>
                    </div>
                    <div className="mt-5 space-y-2.5 text-sm text-white/60">
                      <div className="flex items-center justify-between gap-3">
                        <span>Dew Point</span>
                        <span>{forecast.current.dewpoint_f != null ? `${forecast.current.dewpoint_f}°` : "--"}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>Humidity</span>
                        <span>{forecast.current.humidity_pct != null ? `${forecast.current.humidity_pct}%` : "--"}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>Wind</span>
                        <span className="text-right">
                          {degreesToCardinal(forecast.current.wind_dir_deg)}{" "}
                          {forecast.current.wind_speed_mph ?? "--"} mph
                          {forecast.current.wind_gust_mph
                            ? ` · G${forecast.current.wind_gust_mph}`
                            : ""}
                        </span>
                      </div>
                      {forecast.current.pressure_mb != null ? (
                        <div className="flex items-center justify-between gap-3">
                          <span>Pressure</span>
                          <span>{forecast.current.pressure_mb} mb</span>
                        </div>
                      ) : null}
                      {forecast.current.visibility_mi != null ? (
                        <div className="flex items-center justify-between gap-3">
                          <span>Visibility</span>
                          <span>{forecast.current.visibility_mi} mi</span>
                        </div>
                      ) : null}
                    </div>
                    <div className="mt-4 border-t border-white/8 pt-4">
                      <div className="text-xs text-white/32">
                        {forecast.current.station?.name ?? forecast.attribution.current ?? ""}
                      </div>
                      <div className={`mt-0.5 text-xs font-medium ${freshnessColor(forecast.freshness.current.state)}`}>
                        {freshnessLabel(forecast.freshness.current.state, forecast.freshness.current.age_minutes)}
                      </div>
                    </div>
                  </div>

                  {/* Hourly strip */}
                  <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-5">
                    <HourlyStrip hourly={forecast.hourly} />
                    {forecast.alerts.length > 0 ? (
                      <div className="mt-4 flex items-center gap-2 rounded-2xl border border-rose-300/20 bg-rose-300/10 px-3 py-2">
                        <AlertTriangle className="h-3.5 w-3.5 flex-none text-rose-300" />
                        <span className="text-xs font-medium text-rose-100">
                          {forecast.alerts.length === 1
                            ? "1 active alert"
                            : `${forecast.alerts.length} active alerts`}{" "}
                          — see below
                        </span>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}

            {/* Empty state */}
            {!forecast && !isLoading && !error ? (
              <div className="mt-8 pb-4 text-center">
                <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-white/25">
                  <Search className="h-6 w-6" />
                </div>
                <p className="mt-4 text-sm text-white/38">
                  Search a location above to see current conditions and forecast.
                </p>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      {/* ── Alerts ── */}
      {forecast?.alerts && forecast.alerts.length > 0 ? (
        <section className="border-b border-white/8 bg-[#0b1527] px-5 py-10 md:px-8 md:py-12">
          <div className="mx-auto max-w-6xl">
            <SectionEyebrow>Active Alerts</SectionEyebrow>
            <h2 className="mt-4 text-2xl font-semibold tracking-tight text-white">
              {forecast.alerts.length === 1 ? "1 Active Alert" : `${forecast.alerts.length} Active Alerts`}
            </h2>
            <div className="mt-6">
              <AlertsBanner alerts={forecast.alerts} />
            </div>
          </div>
        </section>
      ) : null}

      {/* ── Daily Forecast ── */}
      {forecast?.daily && forecast.daily.length > 0 ? (
        <section className="border-b border-white/8 bg-[#0b1527] px-5 py-12 md:px-8 md:py-16">
          <div className="mx-auto max-w-6xl">
            <SectionEyebrow>Extended Outlook</SectionEyebrow>
            <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
              <h2 className="text-2xl font-semibold tracking-tight text-white">7-Day Forecast</h2>
              {forecast.attribution.daily ? (
                <div className="text-xs text-white/30">Source: {forecast.attribution.daily}</div>
              ) : null}
            </div>
            <div className="mt-8">
              <DailyForecast daily={forecast.daily} />
            </div>
          </div>
        </section>
      ) : null}

      {/* ── Official Text Forecast ── */}
      {forecast?.official_text_forecast ? (
        <TextForecastSection data={forecast.official_text_forecast} />
      ) : null}

      {/* ── AFD ── */}
      {forecast?.afd ? (
        <AfdSection afd={forecast.afd} />
      ) : null}

      {/* ── Feature Callouts ── */}
      <section className="border-y border-white/8 bg-[#0b1527] px-5 py-16 md:px-8 md:py-20">
        <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-3">
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <MapPinned className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">
              Any location, instantly
            </h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              Search a city, state, zip code, or international location. Open-Meteo geocoding resolves queries worldwide; U.S. locations route through the NWS hybrid pipeline for official data.
            </p>
          </div>
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <CloudSun className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">
              Official data, clearly surfaced
            </h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              Current observations come from the best available NWS station. Text forecasts and Area Forecast Discussions are shown for U.S. locations when available.
            </p>
          </div>
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <ArrowRight className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">
              One click to the viewer
            </h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              Your selected location stays locked as you move into the interactive map. Models, radar, SPC outlooks, and more are available directly from the forecast handoff.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
