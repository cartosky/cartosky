import { useEffect, useId, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useAuth, useUser } from "@clerk/react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  CloudSun,
  Loader2,
  MapPinned,
  RefreshCw,
  Search,
  Star,
  X,
} from "lucide-react";

import { makeForecastLocationId, useForecastLocations, type ForecastLocation } from "@/hooks/useForecastLocations";
import { RadarPreviewCard } from "@/components/forecast/RadarPreviewCard";
import { ModelsTabContent } from "@/components/model-guidance/ModelsTabContent";
import { EnsemblesTabContent } from "@/components/model-guidance/EnsemblesTabContent";
import { API_V4_BASE, MAP_VIEW_DEFAULTS, getReleaseSha } from "@/lib/config";
import { buildPermalinkSearch } from "@/lib/permalink";
import { captureProductAnalyticsEvent } from "@/lib/analytics";
import { MODELS_TAB_VARIABLES } from "@/lib/chart-constants";
import { eligibleTemperatureModels } from "@/lib/eligible-temperature-models";
import { useEntitlements } from "@/lib/entitlements";
import { meteogramAuthHeaders } from "@/lib/meteogram-auth";
import { prefetchMeteogram } from "@/lib/meteogram-cache";
import { useSiteLoading } from "@/lib/site-loading";

// ── Types ─────────────────────────────────────────────────────────────

type LocationResult = {
  display_name: string;
  latitude: number;
  longitude: number;
  timezone: string | null;
  country_code: string | null;
  admin1?: string | null;
  country?: string | null;
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
  quality: { is_fallback: boolean; is_stale: boolean; freshness: string; age_minutes: number | null };
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
  wind_gust_mph: number | null;
  sunrise?: string | null;
  sunset?: string | null;
  icon: string;
  short_text: string | null;
};

type AirQualityData = {
  source: string;
  observed_at: string | null;
  us_aqi: number | null;
  category: string | null;
  color: string | null;
  driver: {
    code: string;
    label: string;
    value: number | null;
    unit: string | null;
    aqi: number;
  } | null;
  pollutants: {
    pm2_5: number | null;
    pm10: number | null;
    ozone: number | null;
    nitrogen_dioxide: number | null;
  };
} | null;

type PollenTypeData = {
  code: string;
  label: string;
  category: string | null;
  index: number | null;
  color?: string | null;
  in_season: boolean;
};

type PollenData = {
  source: string;
  date: string | null;
  index: number | null;
  category: string | null;
  color: string | null;
  dominant_type: string | null;
  dominant_plant: string | null;
  summary: string | null;
  types: PollenTypeData[];
} | null;

type TemperatureHistoryData = {
  today_high_f: number | null;
  normal_high_f: number | null;
  today_low_f: number | null;
  normal_low_f: number | null;
  departure_f: number | null;
  high_is_final: boolean;
  records_high: unknown | null;
  records_low: unknown | null;
  station_name: string | null;
} | null;

type ObservedPrecipYtdData = {
  actual_in: number | null;
  normal_in: number | null;
  percent_of_normal: number | null;
  departure_in: number | null;
  station_name: string | null;
};

type ObservedPrecipData = {
  last_6h_in: number | null;
  last_24h_in: number | null;
  last_72h_in: number | null;
  ytd: ObservedPrecipYtdData | null;
} | null;

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
    timezone: string | null;
    country_code: string | null;
    admin1: string | null;
    resolved_by: string;
  };
  source_status: { primary_region_mode: string; nws: string; open_meteo: string };
  current: CurrentData;
  hourly: HourlyEntry[];
  daily: DailyEntry[];
  air_quality: AirQualityData;
  pollen: PollenData;
  temperature_history: TemperatureHistoryData;
  observed_precip: ObservedPrecipData;
  official_text_forecast: { source: string; generated_at: string | null; periods: TextForecastPeriod[] } | null;
  afd: { office: string; issued_at: string | null; headline: string; text: string | null } | null;
  alerts: AlertEntry[];
  attribution: {
    current: string | null;
    hourly: string | null;
    daily: string | null;
    air_quality?: string | null;
    pollen?: string | null;
    temperature_history?: string | null;
    observed_precip?: string | null;
  };
  freshness: {
    current: { state: string | null; observed_at: string | null; age_minutes: number | null };
    afd: { state: string; issued_at: string | null; age_hours: number | null };
  };
};

// ── Tab config ────────────────────────────────────────────────────────

type TabId = "current" | "hourly" | "7day" | "extended" | "models" | "ensembles" | "discussion";

const TABS: { id: TabId; label: string }[] = [
  { id: "current", label: "Today" },
  { id: "hourly", label: "Hourly" },
  { id: "7day", label: "7-day" },
  { id: "extended", label: "Extended" },
  { id: "models", label: "Models" },
  { id: "ensembles", label: "Ensembles" },
  { id: "discussion", label: "Discussion" },
];

function isTabId(value: string | null | undefined): value is TabId {
  return TABS.some((tab) => tab.id === value);
}

// ── Helpers ───────────────────────────────────────────────────────────

function degreesToCardinal(deg: number | null): string {
  if (deg === null) return "--";
  const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
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
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(d);
}

function formatIssuedAt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
}

function formatDayLabel(date: string | null, index: number): string {
  if (!date) return "--";
  if (index === 0 && date === new Date().toLocaleDateString("en-CA")) return "Today";
  const d = new Date(date + "T12:00:00");
  if (isNaN(d.getTime())) return "--";
  return d.toLocaleDateString("en-US", { weekday: "short" });
}

function isCoordString(s: string): boolean {
  return /^-?\d+\.\d{3,},?\s*-?\d+\.\d{3,}$/.test(s.trim());
}

function alertStyles(severity: string | null) {
  switch ((severity || "").toLowerCase()) {
    case "extreme":  return { border: "border-rose-300/25",   bg: "bg-rose-300/10",       text: "text-rose-100" };
    case "severe":   return { border: "border-orange-300/20", bg: "bg-orange-300/8",       text: "text-orange-100" };
    case "moderate": return { border: "border-amber-300/20",  bg: "bg-amber-300/8",        text: "text-amber-100" };
    default:         return { border: "border-yellow-300/16", bg: "bg-yellow-300/[0.05]",  text: "text-yellow-100" };
  }
}

function freshnessChip(state: string | null, ageMinutes: number | null): { label: string; color: string } {
  if (state === "fresh")   return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Fresh",             color: "text-emerald-400" };
  if (state === "aging")   return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Aging",             color: "text-amber-400" };
  if (state === "stale")   return { label: ageMinutes != null ? `${ageMinutes}m ago · stale` : "Stale",     color: "text-rose-400" };
  if (state === "modeled") return { label: "Modeled",                                                        color: "text-slate-400 dark:text-white/45" };
  return { label: "Recent", color: "text-slate-400 dark:text-white/45" };
}

function currentAgeLabel(current: CurrentData): string {
  const ageMinutes = current.quality?.age_minutes;
  if (ageMinutes !== null && ageMinutes !== undefined) {
    return `As of ${ageMinutes}m ago`;
  }
  const observed = formatObservedAt(current.observed_at);
  return observed ? `Observed ${observed}` : "Current observation";
}

function parseClockMinutes(isoLike: string | null | undefined): number | null {
  if (!isoLike) return null;
  const match = isoLike.match(/T(\d{2}):(\d{2})/);
  if (!match) return null;
  return parseInt(match[1], 10) * 60 + parseInt(match[2], 10);
}

function parseClockMinutesInZone(isoLike: string | null | undefined, timeZone?: string | null): number | null {
  if (!isoLike) return null;
  const hasExplicitOffset = /(?:Z|[+-]\d{2}:\d{2})$/.test(isoLike);
  if (hasExplicitOffset) {
    const date = new Date(isoLike);
    if (!Number.isNaN(date.getTime())) {
      const formatter = new Intl.DateTimeFormat("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: timeZone || undefined,
      });
      const parts = formatter.formatToParts(date);
      const hourPart = parts.find(part => part.type === "hour")?.value;
      const minutePart = parts.find(part => part.type === "minute")?.value;
      if (hourPart && minutePart) {
        return parseInt(hourPart, 10) * 60 + parseInt(minutePart, 10);
      }
    }
  }
  return parseClockMinutes(isoLike);
}

function formatClockTime(isoLike: string | null | undefined): string {
  const minutes = parseClockMinutes(isoLike);
  if (minutes === null) return "--";
  const hour24 = Math.floor(minutes / 60);
  const minute = minutes % 60;
  const period = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 || 12;
  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

function daylightDurationLabel(sunrise: string | null | undefined, sunset: string | null | undefined): string {
  const sunriseMinutes = parseClockMinutes(sunrise);
  const sunsetMinutes = parseClockMinutes(sunset);
  if (sunriseMinutes === null || sunsetMinutes === null || sunsetMinutes <= sunriseMinutes) return "--";
  const durationMinutes = sunsetMinutes - sunriseMinutes;
  const hours = Math.floor(durationMinutes / 60);
  const minutes = durationMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function sunProgress(
  currentObservedAt: string | null,
  sunrise: string | null | undefined,
  sunset: string | null | undefined,
  timeZone?: string | null,
): number {
  const nowMinutes = parseClockMinutesInZone(currentObservedAt, timeZone);
  const sunriseMinutes = parseClockMinutes(sunrise);
  const sunsetMinutes = parseClockMinutes(sunset);
  if (nowMinutes === null || sunriseMinutes === null || sunsetMinutes === null || sunsetMinutes <= sunriseMinutes) {
    return 0.5;
  }
  return clamp((nowMinutes - sunriseMinutes) / (sunsetMinutes - sunriseMinutes), 0, 1);
}

function formatPollutantValue(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  return value >= 10 ? value.toFixed(1).replace(/\.0$/, "") : value.toFixed(1);
}

function formatPrecipInches(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  return `${value.toFixed(2)} in`;
}

function formatSignedPrecipInches(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)} in`;
}

function departureColorClass(value: number | null): string {
  if (value === null) return "text-white/45";
  if (value > 0) return "text-emerald-300";
  if (value < 0) return "text-rose-300";
  return "text-white/55";
}

function airQualityDescription(data: NonNullable<AirQualityData>): string {
  const category = (data.category || "").toLowerCase();
  if (category === "good") return "Air quality is considered satisfactory, and air pollution poses little or no risk.";
  if (category === "moderate") return "Air quality is acceptable, though unusually sensitive people may notice mild symptoms.";
  if (category.includes("unhealthy")) return "Air quality may aggravate respiratory conditions, especially for sensitive groups.";
  return "Air quality conditions are available from the latest Open-Meteo air-quality analysis.";
}

function cardHeadingClassName(): string {
  return "text-[11px] font-medium uppercase tracking-[0.26em] text-white/45";
}

function precipColor(pct: number | null): string {
  if (pct == null || pct <= 10) return "text-white/30";
  if (pct <= 25) return "text-sky-400";
  return "text-amber-400";
}

function feelsLikeF(tempF: number | null, windMph: number | null, humidityPct: number | null): number | null {
  if (tempF === null) return null;
  if (tempF <= 50 && windMph !== null && windMph >= 3) {
    return Math.round(35.74 + 0.6215 * tempF - 35.75 * Math.pow(windMph, 0.16) + 0.4275 * tempF * Math.pow(windMph, 0.16));
  }
  if (tempF >= 80 && humidityPct !== null) {
    const T = tempF, R = humidityPct;
    const hi = -42.379 + 2.04901523*T + 10.14333127*R - 0.22475541*T*R
      - 0.00683783*T*T - 0.05481717*R*R + 0.00122874*T*T*R
      + 0.00085282*T*R*R - 0.00000199*T*T*R*R;
    return Math.round(hi);
  }
  return null;
}

function viewerHref(lat: number, lon: number): string {
  return `/viewer${buildPermalinkSearch({ region: MAP_VIEW_DEFAULTS.region, lat, lon, z: 7 })}`;
}

/** "Denver, CO · 39.7392°N, 104.9903°W"-style location line for shared images. */
function formatLocationText(name: string, lat: number, lon: number): string {
  const latStr = `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"}`;
  const lonStr = `${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"}`;
  return `${name} · ${latStr}, ${lonStr}`;
}

function readFiniteSearchParam(searchParams: URLSearchParams, key: string): number | null {
  const rawValue = searchParams.get(key);
  if (rawValue === null) return null;
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : null;
}

function toForecastLocation(label: string, lat: number, lon: number, locationHint?: Partial<LocationResult>): ForecastLocation {
  return {
    id: makeForecastLocationId(label, lat, lon),
    label,
    lat,
    lon,
    timezone: locationHint?.timezone ?? null,
    country_code: locationHint?.country_code ?? null,
    admin1: locationHint?.admin1 ?? null,
    country: locationHint?.country ?? null,
  };
}

function forecastLocationHint(location: ForecastLocation): Partial<LocationResult> | undefined {
  if (!location.timezone && !location.country_code && !location.admin1 && !location.country) return undefined;
  return {
    display_name: location.label,
    latitude: location.lat,
    longitude: location.lon,
    timezone: location.timezone ?? null,
    country_code: location.country_code ?? null,
    admin1: location.admin1 ?? null,
    country: location.country ?? null,
  };
}

// ── Weather Icon ──────────────────────────────────────────────────────

const WEATHER_ICON_VERSION = getReleaseSha() ?? (import.meta.env.DEV ? String(Date.now()) : null);

function weatherIconUrl(path: string): string {
  if (!WEATHER_ICON_VERSION) return path;
  return `${path}?v=${WEATHER_ICON_VERSION}`;
}

const weatherIconMarkupCache = new Map<string, string>();
const weatherIconRequestCache = new Map<string, Promise<string>>();

function parseWeatherIconSvg(svgMarkup: string): SVGSVGElement | null {
  const parser = new DOMParser();
  const document = parser.parseFromString(
    svgMarkup.trim().replace(/^<\?xml[\s\S]*?\?>\s*/i, ""),
    "image/svg+xml"
  );
  const svg = document.documentElement;
  if (svg.nodeName.toLowerCase() !== "svg") {
    return null;
  }
  return svg as unknown as SVGSVGElement;
}

function serializeWeatherIconSvg(svg: SVGSVGElement): string {
  return new XMLSerializer().serializeToString(svg);
}

function normalizeWeatherIconSvg(svgMarkup: string): string {
  const svg = parseWeatherIconSvg(svgMarkup);
  if (!svg) return svgMarkup.trim();

  svg.removeAttribute("width");
  svg.removeAttribute("height");
  svg.removeAttribute("style");
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("shape-rendering", "geometricPrecision");
  svg.setAttribute(
    "style",
    "display:block;width:100%;height:100%;min-width:100%;min-height:100%;transform:none;shape-rendering:geometricPrecision;"
  );

  svg.querySelectorAll("filter").forEach(element => element.remove());
  svg.querySelectorAll("[filter]").forEach(element => element.removeAttribute("filter"));

  return serializeWeatherIconSvg(svg);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function scopeWeatherIconSvg(svgMarkup: string, scopeId: string): string {
  const svg = parseWeatherIconSvg(svgMarkup);
  if (!svg) return svgMarkup;

  const idMap = new Map<string, string>();
  svg.querySelectorAll("[id]").forEach(element => {
    const existingId = element.getAttribute("id");
    if (!existingId) return;
    const scopedId = `${scopeId}-${existingId}`;
    idMap.set(existingId, scopedId);
    element.setAttribute("id", scopedId);
  });

  if (idMap.size === 0) {
    return serializeWeatherIconSvg(svg);
  }

  svg.querySelectorAll("*").forEach(element => {
    Array.from(element.attributes).forEach(attribute => {
      let nextValue = attribute.value;
      for (const [existingId, scopedId] of idMap.entries()) {
        nextValue = nextValue.replace(new RegExp(`url\\(#${escapeRegExp(existingId)}\\)`, "g"), `url(#${scopedId})`);
        nextValue = nextValue.replace(new RegExp(`(["'\\s(])#${escapeRegExp(existingId)}(?=["'\\s)])`, "g"), `$1#${scopedId}`);
        if (nextValue === `#${existingId}`) {
          nextValue = `#${scopedId}`;
        }
      }
      if (nextValue !== attribute.value) {
        element.setAttribute(attribute.name, nextValue);
      }
    });
  });

  return serializeWeatherIconSvg(svg);
}

async function loadWeatherIconMarkup(src: string): Promise<string> {
  const cachedMarkup = weatherIconMarkupCache.get(src);
  if (cachedMarkup) return cachedMarkup;

  const pendingRequest = weatherIconRequestCache.get(src);
  if (pendingRequest) return pendingRequest;

  const request = fetch(src)
    .then(async response => {
      if (!response.ok) {
        throw new Error(`Unable to load weather icon: ${src}`);
      }
      return normalizeWeatherIconSvg(await response.text());
    })
    .then(markup => {
      weatherIconMarkupCache.set(src, markup);
      weatherIconRequestCache.delete(src);
      return markup;
    })
    .catch(error => {
      weatherIconRequestCache.delete(src);
      throw error;
    });

  weatherIconRequestCache.set(src, request);
  return request;
}

const WEATHER_ICON_SRC: Record<string, string> = {
  "clear-day": weatherIconUrl("/assets/weather-icons/sunny_day.svg"),
  "clear-night": weatherIconUrl("/assets/weather-icons/clear_night.svg"),
  "partly-cloudy-day": weatherIconUrl("/assets/weather-icons/pcloudy_day.svg"),
  "partly-cloudy-night": weatherIconUrl("/assets/weather-icons/pcloudy_night.svg"),
  cloudy: weatherIconUrl("/assets/weather-icons/mcloudy_day.svg"),
  "fog-day": weatherIconUrl("/assets/weather-icons/foggy_day.svg"),
  "fog-night": weatherIconUrl("/assets/weather-icons/foggy_night.svg"),
  "drizzle-day": weatherIconUrl("/assets/weather-icons/light_rain_day.svg"),
  "drizzle-night": weatherIconUrl("/assets/weather-icons/light_rain_night.svg"),
  "rain-day": weatherIconUrl("/assets/weather-icons/rain_day.svg"),
  "rain-night": weatherIconUrl("/assets/weather-icons/rain_night.svg"),
  sleet: weatherIconUrl("/assets/weather-icons/sleet.svg"),
  "sleet-day": weatherIconUrl("/assets/weather-icons/sleet_day.svg"),
  "sleet-night": weatherIconUrl("/assets/weather-icons/sleet_night.svg"),
  snow: weatherIconUrl("/assets/weather-icons/snow.svg"),
  "snow-day": weatherIconUrl("/assets/weather-icons/snow_day.svg"),
  "snow-night": weatherIconUrl("/assets/weather-icons/snow_night.svg"),
  "thunderstorm-day": weatherIconUrl("/assets/weather-icons/tstorm_day.svg"),
  "thunderstorm-night": weatherIconUrl("/assets/weather-icons/tstorm_night.svg"),
  wind: weatherIconUrl("/assets/weather-icons/wind.svg"),
};

function WeatherIcon({ code, size = 20, className }: { code: string; size?: number; className?: string }) {
  const src = WEATHER_ICON_SRC[code] ?? WEATHER_ICON_SRC.cloudy;
  const iconScopeId = useId().replace(/:/g, "_");
  const [rawMarkup, setRawMarkup] = useState<string | null>(() => weatherIconMarkupCache.get(src) ?? null);

  useEffect(() => {
    let isActive = true;
    setRawMarkup(weatherIconMarkupCache.get(src) ?? null);

    void loadWeatherIconMarkup(src)
      .then(svgMarkup => {
        if (isActive) {
          setRawMarkup(svgMarkup);
        }
      })
      .catch(() => {
        if (isActive) {
          setRawMarkup(null);
        }
      });

    return () => {
      isActive = false;
    };
  }, [src]);

  const markup = rawMarkup ? scopeWeatherIconSvg(rawMarkup, iconScopeId) : null;

  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        minWidth: `${size}px`,
        minHeight: `${size}px`,
        flexShrink: 0,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        lineHeight: 0,
        transform: "none",
      }}
      dangerouslySetInnerHTML={markup ? { __html: markup } : undefined}
    />
  );
}

// ── Section label ─────────────────────────────────────────────────────

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] font-medium uppercase tracking-[0.26em] text-white/40">
      {children}
    </div>
  );
}

// ── Eyebrow (marketing sections) ─────────────────────────────────────

function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

// ── Hourly Chart ──────────────────────────────────────────────────────

function HourlyChart({ hourly }: { hourly: HourlyEntry[] }) {
  const entries = hourly.slice(0, 24);
  if (entries.length === 0) return null;

  const temps = entries.map(e => e.temperature_f ?? null).filter((t): t is number => t !== null);
  if (temps.length === 0) return null;

  const rawMin = Math.min(...temps);
  const rawMax = Math.max(...temps);
  const pad = Math.max((rawMax - rawMin) * 0.15, 3);
  const minT = rawMin - pad;
  const maxT = rawMax + pad;
  const range = maxT - minT;

  const VW = 460;
  const VH = 145;
  const TEMP_T = 18;
  const TEMP_B = 90;
  const PRECIP_T = 108;
  const PRECIP_B = 135;

  const xAt = (i: number) => (i / (entries.length - 1)) * VW;
  const yAt = (t: number) => TEMP_T + (1 - (t - minT) / range) * (TEMP_B - TEMP_T);

  function bezierPath(pts: { x: number; y: number }[]) {
    return pts.reduce((d, p, i) => {
      if (i === 0) return `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
      const prev = pts[i - 1];
      const cpx = ((prev.x + p.x) / 2).toFixed(1);
      return `${d} C ${cpx} ${prev.y.toFixed(1)} ${cpx} ${p.y.toFixed(1)} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
    }, "");
  }

  const tempPts = entries.map((e, i) => ({ x: xAt(i), y: yAt(e.temperature_f ?? (minT + range / 2)) }));
  const linePath = bezierPath(tempPts);
  const areaPath = `${linePath} L ${VW} ${TEMP_B} L 0 ${TEMP_B} Z`;

  const precipPts = entries.map((e, i) => ({
    x: xAt(i),
    y: PRECIP_B - ((e.pop_pct ?? 0) / 100) * (PRECIP_B - PRECIP_T),
  }));
  const precipLinePath = bezierPath(precipPts);
  const precipAreaPath = `${precipLinePath} L ${VW} ${PRECIP_B} L 0 ${PRECIP_B} Z`;

  const peakIdx = entries.reduce(
    (maxIdx, e, i, arr) => ((e.temperature_f ?? -999) > (arr[maxIdx].temperature_f ?? -999) ? i : maxIdx),
    0,
  );
  const endIdx = entries.length - 1;
  const labelIdx = [...new Set([0, peakIdx, endIdx])].sort((a, b) => a - b);

  const hasPrecip = entries.some(e => (e.pop_pct ?? 0) > 0);
  const chartHeight = hasPrecip ? VH : TEMP_B + 10;

  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  function handleMouseMove(e: MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * VW;
    let nearest = 0, minDist = Infinity;
    for (let i = 0; i < entries.length; i++) {
      const d = Math.abs(xAt(i) - svgX);
      if (d < minDist) { minDist = d; nearest = i; }
    }
    setHoverIdx(nearest);
  }

  const hEntry = hoverIdx !== null ? entries[hoverIdx] : null;
  const hX = hoverIdx !== null ? xAt(hoverIdx) : 0;
  const hY = hEntry ? yAt(hEntry.temperature_f ?? (minT + range / 2)) : 0;
  const showHoverPrecip = (hEntry?.pop_pct ?? 0) > 0;
  const showHoverWind = hEntry?.wind_speed_mph != null;
  const tooltipText = hEntry
    ? `${hEntry.temperature_f ?? "--"}°`
      + (showHoverPrecip ? ` · 💧${hEntry.pop_pct}%` : "")
      + (showHoverWind ? ` · ${degreesToCardinal(hEntry.wind_dir_deg)} ${hEntry.wind_speed_mph}mph` : "")
    : "";
  const tipW = 52 + (showHoverPrecip ? 44 : 0) + (showHoverWind ? 56 : 0);
  const tipX = hoverIdx === endIdx - 1 ? hX - tipW : hX - tipW / 2;

  return (
    <svg ref={svgRef} viewBox={`0 0 ${VW} ${chartHeight}`} className="h-auto w-full cursor-crosshair"
      aria-hidden="true" onMouseMove={handleMouseMove} onMouseLeave={() => setHoverIdx(null)}>
      <defs>
        <linearGradient id="hTempGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(103,232,249,0.20)" />
          <stop offset="100%" stopColor="rgba(103,232,249,0.01)" />
        </linearGradient>
        <linearGradient id="hPrecipGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(52,211,153,0.45)" />
          <stop offset="100%" stopColor="rgba(52,211,153,0.08)" />
        </linearGradient>
      </defs>

      <path d={areaPath} fill="url(#hTempGrad)" />
      <path d={linePath} fill="none" stroke="rgba(103,232,249,0.85)"
        strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />

      {hoverIdx === null && labelIdx.map(i => {
        const e = entries[i];
        const x = xAt(i);
        const y = yAt(e.temperature_f ?? (minT + range / 2));
        const anchor = i === 0 ? "start" : i === endIdx ? "end" : "middle";
        return (
          <g key={i}>
            <circle cx={x} cy={y} r={2.5} fill="rgba(103,232,249,1)" />
            <text x={x} y={y - 6} textAnchor={anchor} fontSize={9.5}
              fontWeight="500" fill="rgba(255,255,255,0.82)">
              {e.temperature_f != null ? `${e.temperature_f}°` : "--"}
            </text>
          </g>
        );
      })}

      {hasPrecip && (
        <>
          <line x1={0} y1={TEMP_B} x2={VW} y2={TEMP_B}
            stroke="rgba(255,255,255,0.07)" strokeWidth={1} />
          <text
            x={0}
            y={TEMP_B + 8}
            fontSize={4.8}
            fontWeight="500"
            fill="rgba(255,255,255,0.40)"
            letterSpacing="0.20em"
            textAnchor="start"
          >
            PRECIP CHANCE
          </text>
          <path d={precipAreaPath} fill="url(#hPrecipGrad)" />
          <path d={precipLinePath} fill="none"
            stroke="rgba(52,211,153,0.70)" strokeWidth={1.2}
            strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}

      {/* Invisible overlay captures mouse events across full area */}
      <rect x={0} y={0} width={VW} height={chartHeight} fill="transparent" />

      {hoverIdx !== null && hEntry && (
        <g>
          <line x1={hX} y1={TEMP_T - 4} x2={hX} y2={chartHeight}
            stroke="rgba(255,255,255,0.18)" strokeWidth={1} strokeDasharray="3 3" />
          <circle cx={hX} cy={hY} r={3.5} fill="rgba(103,232,249,1)" />
          <rect
            x={tipX}
            y={hY - 22} width={tipW} height={16} rx={3}
            fill="rgba(7,17,31,0.88)"
          />
          <text x={tipX + tipW / 2}
            y={hY - 10} textAnchor="middle" fontSize={9.5} fontWeight="500" fill="rgba(255,255,255,0.90)">
            {tooltipText}
          </text>
        </g>
      )}
    </svg>
  );
}

// ── Daily Temp Chart (Extended tab) ──────────────────────────────────

function DailyTempChart({ daily }: { daily: DailyEntry[] }) {
  if (!daily.length) return null;

  const allTemps = daily.flatMap(e => [e.high_f, e.low_f]).filter((v): v is number => v !== null);
  if (!allTemps.length) return null;

  const rawMin = Math.min(...allTemps);
  const rawMax = Math.max(...allTemps);
  const pad = Math.max((rawMax - rawMin) * 0.2, 4);
  const minT = rawMin - pad;
  const maxT = rawMax + pad;
  const range = maxT - minT;

  const VW = 460;
  const VH = 110;
  const CHART_T = 18;
  const CHART_B = 88;

  const n = daily.length;
  const xAt = (i: number) => n <= 1 ? VW / 2 : (i / (n - 1)) * VW;
  const yAt = (t: number) => CHART_T + (1 - (t - minT) / range) * (CHART_B - CHART_T);

  function bezierPath(pts: { x: number; y: number }[]) {
    return pts.reduce((d, p, i) => {
      if (i === 0) return `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
      const prev = pts[i - 1];
      const cpx = ((prev.x + p.x) / 2).toFixed(1);
      return `${d} C ${cpx} ${prev.y.toFixed(1)} ${cpx} ${p.y.toFixed(1)} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
    }, "");
  }

  const highPts = daily.map((e, i) => ({ x: xAt(i), y: yAt(e.high_f ?? rawMax) }));
  const lowPts  = daily.map((e, i) => ({ x: xAt(i), y: yAt(e.low_f  ?? rawMin) }));

  const highPath = bezierPath(highPts);
  const lowPath  = bezierPath(lowPts);
  const lowRevPath = bezierPath([...lowPts].reverse()).replace(/^M/, "L");
  const bandPath = `${highPath} ${lowRevPath} Z`;

  const step = n > 10 ? 2 : 1;
  const labelIdxs = [...new Set([0, ...daily.map((_, i) => i).filter(i => i % step === 0), n - 1])].sort((a, b) => a - b);

  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  function handleMouseMove(e: MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * VW;
    let nearest = 0, minDist = Infinity;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(xAt(i) - svgX);
      if (d < minDist) { minDist = d; nearest = i; }
    }
    setHoverIdx(nearest);
  }

  const hEntry = hoverIdx !== null ? daily[hoverIdx] : null;
  const hX = hoverIdx !== null ? xAt(hoverIdx) : 0;
  const hHighY = hEntry ? yAt(hEntry.high_f ?? rawMax) : 0;
  const hLowY  = hEntry ? yAt(hEntry.low_f  ?? rawMin) : 0;
  const hAnchor = hoverIdx !== null && hoverIdx <= 1 ? "start" : hoverIdx !== null && hoverIdx >= n - 2 ? "end" : "middle";
  const tooltipX = hAnchor === "start" ? hX : hAnchor === "end" ? hX - 64 : hX - 32;

  return (
    <svg ref={svgRef} viewBox={`0 0 ${VW} ${VH}`} className="h-auto w-full cursor-crosshair"
      aria-hidden="true" onMouseMove={handleMouseMove} onMouseLeave={() => setHoverIdx(null)}>
      <defs>
        <linearGradient id="dBandGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(103,232,249,0.22)" />
          <stop offset="100%" stopColor="rgba(103,232,249,0.04)" />
        </linearGradient>
      </defs>

      <path d={bandPath} fill="url(#dBandGrad)" />
      <path d={highPath} fill="none" stroke="rgba(103,232,249,0.85)"
        strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
      <path d={lowPath} fill="none" stroke="rgba(103,232,249,0.30)"
        strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" />

      {hoverIdx === null && (
        <line x1={0} x2={VW} y1={VH - 12} y2={VH - 12}
          stroke="rgba(255,255,255,0.07)" strokeWidth={0.5} />
      )}

      {hoverIdx === null && labelIdxs.map(i => {
        const e = daily[i];
        const x = xAt(i);
        const anchor = i === 0 ? "start" : i >= n - 1 ? "end" : "middle";
        return (
          <g key={i}>
            <text x={x} y={yAt(e.high_f ?? rawMax) - 5} textAnchor={anchor}
              fontSize={9} fontWeight="500" fill="rgba(255,255,255,0.75)">
              {e.high_f != null ? `${e.high_f}°` : ""}
            </text>
            <text x={x} y={yAt(e.low_f ?? rawMin) + 13} textAnchor={anchor}
              fontSize={8} fill="rgba(255,255,255,0.22)">
              {e.low_f != null ? `${e.low_f}°` : ""}
            </text>
            <text x={x} y={VH - 4} textAnchor={anchor}
              dominantBaseline="alphabetic"
              fontSize={5.8} fontWeight="400" fill="rgba(168,200,216,0.55)">
              {formatDayLabel(e.date, i)}
            </text>
          </g>
        );
      })}

      {/* Invisible overlay captures mouse events */}
      <rect x={0} y={0} width={VW} height={VH} fill="transparent" />

      {hoverIdx !== null && hEntry && (
        <g>
          <line x1={hX} y1={CHART_T - 4} x2={hX} y2={VH}
            stroke="rgba(255,255,255,0.18)" strokeWidth={1} strokeDasharray="3 3" />
          <circle cx={hX} cy={hHighY} r={3} fill="rgba(103,232,249,1)" />
          <circle cx={hX} cy={hLowY}  r={3} fill="rgba(103,232,249,0.4)" />
          <rect x={tooltipX} y={hHighY - 24} width={64} height={18} rx={3}
            fill="rgba(7,17,31,0.88)" />
          <text x={tooltipX + 32} y={hHighY - 11} textAnchor="middle"
            fontSize={9.5} fontWeight="500" fill="rgba(255,255,255,0.90)">
            {formatDayLabel(hEntry.date, hoverIdx)} · {hEntry.high_f ?? "--"}° / {hEntry.low_f ?? "--"}°
          </text>
        </g>
      )}
    </svg>
  );
}

// ── Hourly Strip ──────────────────────────────────────────────────────

function HourlyStrip({ hourly }: { hourly: HourlyEntry[] }) {
  const entries = hourly.slice(0, 24);
  return (
    <div className="flex gap-1 overflow-x-auto py-1 pb-2 forecast-scroll">
      {entries.map((entry, i) => {
        const pop = entry.pop_pct ?? 0;
        const isCurrent = i === 0;
        return (
          <div
            key={i}
            className={`flex-none flex flex-col items-center gap-1.5 rounded-lg px-3 py-2.5 min-w-[3.5rem] transition-colors ${
              isCurrent
                ? "bg-white/[0.07] ring-[0.5px] ring-white/[0.10]"
                : "hover:bg-white/[0.03]"
            }`}
          >
            <span className="text-[11px] text-white/40">{formatHour(entry.time)}</span>
            <WeatherIcon code={entry.weather_code} size={30} />
            <span className="text-[13px] font-medium text-white">{entry.temperature_f ?? "--"}°</span>
            {pop > 0
              ? <span className={`text-[10px] ${precipColor(pop)}`}>{pop}%</span>
              : <span className="h-[14px]" />
            }
          </div>
        );
      })}
    </div>
  );
}

// ── Daily Range Rows ─────────────────────────────────────────────────

function DailyDetailGrid({ entry }: { entry: DailyEntry }) {
  const details = [
    { label: "Forecast", value: entry.short_text ?? "--" },
    { label: "Precip", value: entry.pop_pct != null ? `${entry.pop_pct}% chance` : "--" },
    { label: "Rain", value: entry.qpf_in != null ? `${entry.qpf_in.toFixed(2)} in` : "--" },
    { label: "Snow", value: entry.snow_in != null ? `${entry.snow_in.toFixed(1)} in` : "--" },
    { label: "Wind", value: entry.wind_speed_mph != null ? `${entry.wind_speed_mph} mph${entry.wind_gust_mph != null ? `, gusts ${entry.wind_gust_mph}` : ""}` : "--" },
  ];
  return (
    <div className="grid gap-3 rounded-lg border border-cyan-300/10 bg-cyan-300/[0.035] p-3 sm:grid-cols-5">
      {details.map(detail => (
        <div key={detail.label}>
          <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-cyan-200/55">{detail.label}</div>
          <div className="mt-1 text-[12px] leading-5 text-white/75">{detail.value}</div>
        </div>
      ))}
    </div>
  );
}

function DailyRangeRows({
  daily,
  limit,
  expandable = false,
}: {
  daily: DailyEntry[];
  limit?: number;
  expandable?: boolean;
}) {
  const entries = typeof limit === "number" ? daily.slice(0, limit) : daily;
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  if (!entries.length) return null;

  const lows  = entries.map(e => e.low_f  ?? null).filter((v): v is number => v !== null);
  const highs = entries.map(e => e.high_f ?? null).filter((v): v is number => v !== null);
  if (!lows.length || !highs.length) return null;

  const globalMin = Math.min(...lows);
  const globalMax = Math.max(...highs);
  const span = globalMax - globalMin || 1;

  function toggle(i: number) {
    if (!expandable) return;
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  return (
    <div>
      {entries.map((entry, i) => {
        const low  = entry.low_f  ?? globalMin;
        const high = entry.high_f ?? globalMax;
        const leftPct  = ((low  - globalMin) / span) * 100;
        const widthPct = ((high - low) / span) * 100;
        const highPct = leftPct + widthPct;
        const pop = entry.pop_pct ?? 0;
        const isOpen = expanded.has(i);
        const lowLabelStyle = leftPct < 5
          ? { top: "0px", left: 0, transform: "none" as const }
          : { top: "0px", left: `${leftPct}%`, transform: "translateX(-50%)" as const };
        const highLabelStyle = highPct > 95
          ? { top: "0px", right: 0, left: "auto", transform: "none" as const }
          : { top: "0px", left: `${highPct}%`, transform: "translateX(-50%)" as const };
        const row = (
          <div className="grid grid-cols-[44px_34px_1fr_40px] items-center gap-3 py-3">
            <div className="text-[13px] font-medium text-white/65">
              {formatDayLabel(entry.date, i)}
            </div>
            <WeatherIcon code={entry.icon} size={30} className="flex-none" />
            <div className="relative" style={{ paddingTop: 24 }}>
              <span
                className="absolute text-[11px] font-medium text-white/55"
                style={lowLabelStyle}
              >
                {entry.low_f ?? "--"}°
              </span>
              <span
                className="absolute text-[12px] font-semibold text-white/90"
                style={highLabelStyle}
              >
                {entry.high_f ?? "--"}°
              </span>
              <div className="relative h-[6px] overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.07]">
                <div
                  className="absolute inset-y-0 rounded-full bg-sky-400/80 dark:bg-gradient-to-r dark:from-sky-400/32 dark:to-cyan-300/85"
                  style={{ left: `${leftPct}%`, width: `${Math.max(widthPct, 1)}%` }}
                />
              </div>
            </div>
            <div className={`w-8 flex-none text-right text-[13px] font-medium ${precipColor(entry.pop_pct)}`}>
              {pop > 0 ? `${pop}%` : ""}
            </div>
          </div>
        );
        return (
          <div
            key={i}
            className={`rounded-md transition-colors hover:bg-white/[0.035] ${i < entries.length - 1 ? "border-b-[0.5px] border-white/[0.06]" : ""}`}
          >
            {expandable ? (
              <button
                type="button"
                onClick={() => toggle(i)}
                aria-expanded={isOpen}
                className="w-full text-left"
              >
                {row}
              </button>
            ) : row}
            {expandable && isOpen && (
              <div className="pb-3 sm:pl-[90px] sm:pr-[52px]">
                <DailyDetailGrid entry={entry} />
              </div>
            )}
          </div>
        );
      })}
      <div className="mt-2 grid grid-cols-[44px_34px_1fr_40px] items-center gap-3 border-t border-white/[0.05] pt-2">
        <div />
        <div />
        <div className="flex items-center justify-between text-[11px] font-medium text-white/42">
          <span>{`< ${globalMin}° week low`}</span>
          <span>{`${globalMax}° week high >`}</span>
        </div>
        <div className="text-right text-[11px] font-medium text-cyan-200/55">PoP</div>
      </div>
    </div>
  );
}

// ── Current Tab ───────────────────────────────────────────────────────

function CurrentMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[minmax(7.5rem,0.9fr)_1fr] items-baseline gap-4">
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-cyan-200/75">{label}</div>
      <div className="text-[15px] font-medium text-white/90">{value}</div>
    </div>
  );
}

function CurrentConditionsCard({ current }: { current: CurrentData }) {
  const feelsLike = feelsLikeF(current.temperature_f, current.wind_speed_mph, current.humidity_pct);
  const metrics = [
    feelsLike !== null && feelsLike !== current.temperature_f
      ? { label: "Feels Like", value: `${feelsLike}°` }
      : null,
    current.dewpoint_f != null ? { label: "Dew Point", value: `${current.dewpoint_f}°` } : null,
    current.humidity_pct != null ? { label: "Humidity", value: `${current.humidity_pct}%` } : null,
    {
      label: "Wind",
      value: `${degreesToCardinal(current.wind_dir_deg)} ${current.wind_speed_mph ?? "--"} mph${current.wind_gust_mph ? ` · G${current.wind_gust_mph}` : ""}`,
    },
    current.pressure_mb != null ? { label: "Pressure", value: `${current.pressure_mb} mb` } : null,
    current.visibility_mi != null ? { label: "Visibility", value: `${current.visibility_mi} mi` } : null,
  ].filter((metric): metric is { label: string; value: string } => metric !== null);

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.18)] md:p-5">
      <h2 className="text-[11px] font-medium uppercase tracking-[0.26em] text-white/45">
        Current Conditions
      </h2>

      <div className="mt-5 grid gap-6 md:grid-cols-[minmax(0,0.95fr)_minmax(15rem,1fr)] md:items-center">
        <div className="flex items-center gap-4 md:min-h-[9.5rem] md:justify-center">
          <WeatherIcon code={current.icon} size={74} className="flex-none" />
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-[4.5rem] font-medium leading-none text-white drop-shadow-[0_10px_30px_rgba(0,0,0,0.28)] md:text-[5rem]">
                {current.temperature_f ?? "--"}°
              </span>
              <span className="text-2xl font-medium text-white/48">F</span>
            </div>
            {current.short_text && (
              <div className="mt-2 text-lg text-white/62 md:text-xl">{current.short_text}</div>
            )}
          </div>
        </div>

        <div className="space-y-4 border-white/[0.08] md:border-l md:pl-8">
          {metrics.map((metric) => (
            <CurrentMetric key={metric.label} label={metric.label} value={metric.value} />
          ))}
        </div>
      </div>

      <div className="mt-5 text-[12px] text-white/42">{currentAgeLabel(current)}</div>
    </section>
  );
}

function CurrentRadarCard({ lat, lon }: { lat: number; lon: number }) {
  return (
    <section className="flex h-full flex-col rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>
        Live Radar
      </h2>
      <RadarPreviewCard lat={lat} lon={lon} className="mt-3 w-full flex-1" mapHeightClassName="h-full flex-1" />
    </section>
  );
}

function RadialGauge({
  value,
  maxValue,
  color,
  valueLabel,
  secondaryLabel,
}: {
  value: number | null;
  maxValue: number;
  color: string;
  valueLabel: string;
  secondaryLabel: string;
}) {
  const progress = value === null ? 0 : clamp(value / maxValue, 0, 1);
  const dash = progress * 100;
  return (
    <div className="relative h-[7.5rem] w-[7.5rem]">
      <svg viewBox="0 0 100 100" className="h-full w-full overflow-visible" aria-hidden="true">
        <path
          d="M 25.9 84.4 A 42 42 0 1 1 74.1 84.4"
          pathLength={100}
          className="fill-none stroke-white/[0.10]"
          strokeWidth="6.25"
          strokeLinecap="round"
        />
        <path
          d="M 25.9 84.4 A 42 42 0 1 1 74.1 84.4"
          pathLength={100}
          className="fill-none"
          stroke={color}
          strokeWidth="6.25"
          strokeLinecap="round"
          strokeDasharray={`${dash} 100`}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <div className="text-3xl font-medium leading-none text-white">{valueLabel}</div>
        <div className="mt-1 text-sm text-white/72">{secondaryLabel}</div>
      </div>
    </div>
  );
}

function CurrentSunCard({ current, daily, timeZone }: { current: CurrentData; daily: DailyEntry[]; timeZone: string | null }) {
  const today = daily[0];
  if (!today) {
    return (
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
        <h2 className={cardHeadingClassName()}>Sun</h2>
        <p className="mt-5 text-sm text-white/45">Sun data is unavailable for this location.</p>
      </section>
    );
  }

  const sunrise = today?.sunrise ?? null;
  const sunset = today?.sunset ?? null;
  const progress = sunProgress(current.observed_at, sunrise, sunset, timeZone);
  const angle = Math.PI * (1 - progress);
  const radius = 68;
  const centerX = 80;
  const centerY = 74;
  const sunX = centerX + Math.cos(angle) * radius;
  const sunY = centerY - Math.sin(angle) * radius;
  const sunRaySegments = Array.from({ length: 8 }, (_, index) => {
    const rayAngle = (index / 8) * Math.PI * 2;
    const innerRadius = 8;
    const outerRadius = 12;
    return {
      x1: sunX + Math.cos(rayAngle) * innerRadius,
      y1: sunY + Math.sin(rayAngle) * innerRadius,
      x2: sunX + Math.cos(rayAngle) * outerRadius,
      y2: sunY + Math.sin(rayAngle) * outerRadius,
    };
  });

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>Sun</h2>
      <div className="mt-3">
        <svg viewBox="0 -8 160 84" className="h-auto w-full" aria-hidden="true">
          <path
            d="M 12 74 A 68 68 0 0 1 148 74"
            fill="none"
            stroke="rgba(251, 191, 36, 0.6)"
            strokeWidth="2"
            strokeLinecap="round"
          />
          {sunRaySegments.map((segment, index) => (
            <line
              key={index}
              x1={segment.x1}
              y1={segment.y1}
              x2={segment.x2}
              y2={segment.y2}
              stroke="rgba(251, 191, 36, 0.55)"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          ))}
          <circle cx={sunX} cy={sunY} r="5.5" fill="#fbbf24" />
        </svg>
      </div>
      <div className="mt-2">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-lg font-medium text-white">{formatClockTime(sunrise)}</div>
            <div className="text-sm text-white/55">Sunrise</div>
          </div>
          <div className="text-right">
            <div className="text-lg font-medium text-white">{formatClockTime(sunset)}</div>
            <div className="text-sm text-white/55">Sunset</div>
          </div>
        </div>
        <div className="mt-2 text-center text-sm text-white/50">Daylight: {daylightDurationLabel(sunrise, sunset)}</div>
      </div>
    </section>
  );
}

function CurrentAirQualityCard({ airQuality }: { airQuality: AirQualityData }) {
  const displayAqi = airQuality?.us_aqi ?? airQuality?.driver?.aqi ?? null;
  if (!airQuality || displayAqi === null) {
    return (
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
        <h2 className={cardHeadingClassName()}>Air Quality</h2>
        <p className="mt-5 text-sm text-white/45">Current air-quality data is unavailable for this location.</p>
      </section>
    );
  }

  const gaugeColor = airQuality.color || "#3ecf6a";

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>Air Quality</h2>
      <div className="mt-3 flex flex-col items-center gap-4">
        <RadialGauge
          value={displayAqi}
          maxValue={100}
          color={gaugeColor}
          valueLabel={String(displayAqi)}
          secondaryLabel={airQuality.category || "AQI"}
        />
        <div className="w-full space-y-2">
          <p className="text-[15px] leading-6 text-white/72">{airQualityDescription(airQuality)}</p>
        </div>
      </div>
    </section>
  );
}

function CurrentPollenCard({ pollen }: { pollen: PollenData }) {
  if (!pollen || pollen.index === null) {
    return (
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
        <h2 className={cardHeadingClassName()}>Pollen</h2>
        <p className="mt-5 text-sm text-white/45">Pollen guidance is unavailable for this location.</p>
      </section>
    );
  }

  const visibleTypes = pollen.types.filter(type => type.index !== null).slice(0, 3);
  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>Pollen</h2>
      <div className="mt-3 flex flex-col items-center gap-4">
        <RadialGauge
          value={pollen.index}
          maxValue={5}
          color={pollen.color || "#ffb423"}
          valueLabel={String(pollen.index)}
          secondaryLabel={pollen.category || "Pollen"}
        />
        <div className="w-full space-y-2">
          <p className="text-[15px] leading-6 text-white/72">{pollen.summary || `${pollen.category || "Current"} ${pollen.dominant_type?.toLowerCase() || "pollen"} levels are expected today.`}</p>
          <div className="space-y-2">
            {visibleTypes.map((type) => (
              <div key={type.code} className="flex items-center justify-between gap-4 text-sm">
                <span className="text-white/78">{type.label} Pollen</span>
                <span className="text-white/55">{type.category || "--"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function TodayNarrativeCard({ textForecast }: { textForecast: ForecastPayload["official_text_forecast"] }) {
  if (!textForecast || !textForecast.periods.length) return null;

  const dayIndex = textForecast.periods.findIndex(period => period.is_daytime === true);
  if (dayIndex < 0 || dayIndex + 1 >= textForecast.periods.length) return null;

  const todayPeriod = textForecast.periods[dayIndex];
  const nextPeriod = textForecast.periods[dayIndex + 1];
  const todayText = todayPeriod.detailed_text ?? todayPeriod.short_text;
  const nextText = nextPeriod.detailed_text ?? nextPeriod.short_text;
  if (!todayText || !nextText) return null;

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <div className="space-y-4">
        <div>
          <h2 className={cardHeadingClassName()}>Today&apos;s Weather</h2>
          <p className="mt-2 text-[15px] leading-7 text-white/78">{todayText}</p>
        </div>
        <div className="border-t border-white/[0.06] pt-4">
          <h2 className={cardHeadingClassName()}>Looking Ahead</h2>
          <p className="mt-2 text-[15px] leading-7 text-white/78">{nextText}</p>
        </div>
      </div>
    </section>
  );
}

function CurrentPrecipCard({ observedPrecip }: { observedPrecip: ObservedPrecipData }) {
  if (!observedPrecip) {
    return (
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
        <h2 className={cardHeadingClassName()}>Observed Precipitation</h2>
        <p className="mt-5 text-sm text-white/45">Observed precipitation data is unavailable for this location.</p>
      </section>
    );
  }

  const ytd = observedPrecip.ytd;
  const ytdSummaryAvailable = ytd?.percent_of_normal != null || ytd?.departure_in != null;
  const rows = [
    { label: "Last 6 Hours", value: formatPrecipInches(observedPrecip.last_6h_in) },
    { label: "Last 24 Hours", value: formatPrecipInches(observedPrecip.last_24h_in) },
    { label: "Last 72 Hours", value: formatPrecipInches(observedPrecip.last_72h_in) },
  ];

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>Observed Precipitation</h2>
      <div className="mt-4 divide-y divide-white/[0.07] rounded-lg border border-white/[0.06] bg-black/10">
        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-4 py-3.5">
            <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-cyan-200/75">{row.label}</div>
            <div className="text-right text-[15px] font-medium text-white/90">{row.value}</div>
          </div>
        ))}

        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 px-4 py-3.5">
          <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-cyan-200/75">Year to Date</div>
          <div className="text-right">
            <div className="text-[15px] font-medium text-white/90">{formatPrecipInches(ytd?.actual_in ?? null)}</div>
            <div className="mt-1 flex items-center justify-end gap-2 text-[12px]">
              {ytdSummaryAvailable ? (
                <>
                  <span className="text-white/55">{ytd?.percent_of_normal != null ? `${ytd.percent_of_normal}%` : "--"}</span>
                  <span className={departureColorClass(ytd?.departure_in ?? null)}>{formatSignedPrecipInches(ytd?.departure_in ?? null)}</span>
                </>
              ) : (
                <span className="text-white/45">--</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CurrentTemperatureHistoryCard({ temperatureHistory }: { temperatureHistory: TemperatureHistoryData }) {
  if (!temperatureHistory) {
    return (
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
        <h2 className={cardHeadingClassName()}>Temperature History</h2>
        <p className="mt-5 text-sm text-white/45">Temperature history is unavailable for this location.</p>
      </section>
    );
  }

  const highDeparture =
    temperatureHistory.today_high_f != null && temperatureHistory.normal_high_f != null
      ? temperatureHistory.today_high_f - temperatureHistory.normal_high_f
      : null;
  const lowDeparture =
    temperatureHistory.today_low_f != null && temperatureHistory.normal_low_f != null
      ? temperatureHistory.today_low_f - temperatureHistory.normal_low_f
      : null;

  const rows = [
    {
      kind: "primary" as const,
      label: "Today's High",
      value: temperatureHistory.today_high_f != null ? `${temperatureHistory.today_high_f}°` : "--",
      normalValue: temperatureHistory.normal_high_f != null ? `${temperatureHistory.normal_high_f}°` : "--",
    },
    {
      kind: "departure" as const,
      label: "High Departure",
      value: highDeparture != null ? `${highDeparture > 0 ? "+" : highDeparture < 0 ? "-" : ""}${Math.abs(highDeparture)}°` : "--",
      valueClassName: departureColorClass(highDeparture),
    },
    {
      kind: "primary" as const,
      label: "Today's Low",
      value: temperatureHistory.today_low_f != null ? `${temperatureHistory.today_low_f}°` : "--",
      normalValue: temperatureHistory.normal_low_f != null ? `${temperatureHistory.normal_low_f}°` : "--",
    },
    {
      kind: "departure" as const,
      label: "Low Departure",
      value: lowDeparture != null ? `${lowDeparture > 0 ? "+" : lowDeparture < 0 ? "-" : ""}${Math.abs(lowDeparture)}°` : "--",
      valueClassName: departureColorClass(lowDeparture),
    },
  ];

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.16)] md:p-5">
      <h2 className={cardHeadingClassName()}>Temperature History</h2>
      <div className="mt-4 divide-y divide-white/[0.07] rounded-lg border border-white/[0.06] bg-black/10">
        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 px-4 py-3.5">
            <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-cyan-200/75">{row.label}</div>
            <div className="text-right">
              <div className={`text-[15px] font-medium ${row.kind === "departure" ? row.valueClassName : "text-white/90"}`}>{row.value}</div>
              {row.kind === "primary" && (
                <div className="mt-1 flex items-center justify-end gap-2 text-[12px]">
                  <span className="text-white/55">{`Normal ${row.normalValue}`}</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function CurrentTab({
  forecast,
  checkingAlerts,
}: {
  forecast: ForecastPayload;
  checkingAlerts: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(20rem,1fr)]">
        <CurrentConditionsCard current={forecast.current} />
        <CurrentRadarCard lat={forecast.location.latitude} lon={forecast.location.longitude} />
      </div>
      <AlertsBanner alerts={forecast.alerts} checking={checkingAlerts} />
      <TodayNarrativeCard textForecast={forecast.official_text_forecast} />
      <div className="grid gap-4 lg:grid-cols-3">
        <CurrentSunCard current={forecast.current} daily={forecast.daily} timeZone={forecast.location.timezone} />
        <CurrentAirQualityCard airQuality={forecast.air_quality} />
        <CurrentPollenCard pollen={forecast.pollen} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <CurrentPrecipCard observedPrecip={forecast.observed_precip} />
        <CurrentTemperatureHistoryCard temperatureHistory={forecast.temperature_history} />
      </div>
    </div>
  );
}

// ── Hourly Tab ────────────────────────────────────────────────────────

function HourlyTab({ hourly }: { hourly: HourlyEntry[] }) {
  if (!hourly.length) {
    return <div className="py-16 text-center text-[13px] text-white/35">No hourly data available.</div>;
  }
  const chartEntries = hourly.slice(0, 24);
  const timeIdx = [0, 6, 12, 18, chartEntries.length - 1].filter((v, i, a) => a.indexOf(v) === i);
  return (
    <div className="space-y-5">
      <div className="rounded-xl bg-white/[0.03] p-4 md:p-5">
        <p className="mb-3 text-[11px] font-medium uppercase tracking-[0.20em] text-white/40">
          Temperature · Next 24 Hours
        </p>
        <HourlyChart hourly={hourly} />
        <div className="relative h-5 mt-1.5">
          {timeIdx.map(i => {
            const pct = chartEntries.length > 1 ? (i / (chartEntries.length - 1)) * 100 : 0;
            const align = i === 0 ? "" : i >= chartEntries.length - 1 ? "-translate-x-full" : "-translate-x-1/2";
            return (
              <span
                key={i}
                className={`absolute top-0 text-[10px] text-white/35 ${align}`}
                style={{ left: `${pct}%` }}
              >
                {formatHour(chartEntries[i].time)}
              </span>
            );
          })}
        </div>
      </div>
      <HourlyStrip hourly={hourly} />
    </div>
  );
}

// ── Alerts ────────────────────────────────────────────────────────────

function AlertsBanner({ alerts, checking }: { alerts: AlertEntry[]; checking?: boolean }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  function toggle(i: number) {
    setExpanded(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });
  }
  if (alerts.length === 0) {
    return (
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-3">
        {checking ? (
          <p className="flex items-center gap-2 text-sm text-white/45">
            <Loader2 className="h-3.5 w-3.5 flex-none animate-spin text-cyan-300" aria-hidden />
            Checking alerts…
          </p>
        ) : (
          <p className="flex items-center gap-2 text-sm text-white/45">
            <Check className="h-3.5 w-3.5 flex-none text-cyan-300" aria-hidden />
            No active alerts
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {alerts.map((alert, i) => {
        const s = alertStyles(alert.severity);
        const isOpen = expanded.has(i);
        return (
          <div key={alert.id ?? i} className={`rounded-xl border ${s.border} ${s.bg} overflow-hidden`}>
            <button type="button" onClick={() => toggle(i)} className="flex w-full items-start gap-3 p-4 text-left">
              <AlertTriangle className={`mt-0.5 h-4 w-4 flex-none ${s.text}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`text-sm font-medium ${s.text}`}>{alert.event ?? "Alert"}</span>
                </div>
                {alert.headline && <p className="mt-1 text-sm text-white/70">{alert.headline}</p>}
                {alert.areas.length > 0 && <p className="mt-0.5 text-xs text-white/38">{alert.areas.slice(0, 3).join(" · ")}</p>}
              </div>
              <div className="flex-none text-white/35">
                {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </div>
            </button>
            {isOpen && alert.description && (
              <div className="border-t border-white/8 px-4 pb-4 pt-3">
                <p className="text-sm leading-7 text-white/60 whitespace-pre-wrap">{alert.description}</p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Day List Table (7-day tab) ────────────────────────────────────────

function DayListTable({ daily }: { daily: DailyEntry[] }) {
  return <DailyRangeRows daily={daily} limit={7} />;
}

// ── NWS Cards Grid (7-day tab) ────────────────────────────────────────

function NWSCardsGrid({ data }: { data: NonNullable<ForecastPayload["official_text_forecast"]> }) {
  const [showAll, setShowAll] = useState(false);
  if (!data.periods.length) return null;
  const visible = showAll ? data.periods : data.periods.slice(0, 6);

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((period, i) => (
          <div key={i} className="rounded-xl bg-white/[0.04] border border-white/[0.06] p-4">
            <div className="text-[11px] uppercase tracking-[0.16em] text-white/40">
              {period.name ?? (period.is_daytime ? "Day" : "Night")}
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-xl font-medium text-white">{period.temperature_f ?? "--"}°</span>
              <span className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                {period.is_daytime ? "High" : "Low"}
              </span>
            </div>
            <div className="mt-1.5 text-[13px] text-white/75">{period.short_text ?? ""}</div>
            {period.wind_text && (
              <div className="mt-1 text-[12px] text-white/40">Wind: {period.wind_text}</div>
            )}
            {period.detailed_text && (
              <p className="mt-3 border-t-[0.5px] border-white/[0.06] pt-3 text-[12px] leading-[1.6] text-white/40">
                {period.detailed_text}
              </p>
            )}
          </div>
        ))}
      </div>
      {data.periods.length > 6 && (
        <button
          type="button"
          onClick={() => setShowAll(v => !v)}
          className="mt-4 flex items-center gap-1.5 text-[12px] text-white/40 transition hover:text-white/60"
        >
          {showAll
            ? <><ChevronUp className="h-3.5 w-3.5" /> Show fewer</>
            : <><ChevronDown className="h-3.5 w-3.5" /> Show all {data.periods.length} periods</>
          }
        </button>
      )}
    </div>
  );
}

// ── 7-day Tab ─────────────────────────────────────────────────────────

function SevenDayTab({ daily, textForecast }: {
  daily: DailyEntry[];
  textForecast: ForecastPayload["official_text_forecast"];
}) {
  return (
    <div className="space-y-8">
      <DayListTable daily={daily} />
      {textForecast && <NWSCardsGrid data={textForecast} />}
    </div>
  );
}

// ── Extended Tab ──────────────────────────────────────────────────────

function ExtendedTab({ daily, attribution }: { daily: DailyEntry[]; attribution: string | null }) {
  if (!daily.length) return null;

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-white/[0.03] p-4 md:p-5">
        <p className="mb-3 text-[11px] font-medium uppercase tracking-[0.20em] text-white/40">
          Temperature · 15-Day Outlook
        </p>
        <DailyTempChart daily={daily} />
      </div>
      <div>
      <DailyRangeRows daily={daily} expandable />
      </div>
    </div>
  );
}

// ── Models Tab ────────────────────────────────────────────────────────
// Models top-level tab content lives in components/model-guidance/ModelsTabContent.

// ── Discussion Tab ────────────────────────────────────────────────────

function DiscussionTab({ afd }: { afd: ForecastPayload["afd"] }) {
  if (!afd || !afd.text) {
    return (
      <div className="py-16 text-center text-[13px] text-white/35">
        No forecast discussion available.
      </div>
    );
  }
  return (
    <div className="rounded-xl bg-white/[0.03] p-5 md:p-6">
      <p className="mb-4 text-[11px] text-white/30">
        {afd.office}{afd.issued_at ? ` · ${formatIssuedAt(afd.issued_at)}` : ""}
      </p>
      <pre className="font-mono text-xs leading-[1.7] text-white/55 whitespace-pre-wrap break-words">
        {afd.text}
      </pre>
    </div>
  );
}

// ── Open-Meteo core + async NWS enrichment ────────────────────────────
// The page first paints from the fast Open-Meteo core (current/hourly/daily),
// then merges NWS pieces (real-observation current, 7-day narrative, alerts,
// AFD) when the slower /forecast-page call returns. Hourly/daily stay
// Open-Meteo. `source_status.nws === "pending"` on the core marks a US location
// that has NWS enrichment to fetch.
function buildForecastParams(lat: number, lon: number, hint?: Partial<LocationResult>): URLSearchParams {
  const params = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  if (hint?.display_name) params.set("display_name", hint.display_name);
  if (hint?.timezone) params.set("timezone", hint.timezone);
  if (hint?.country_code) params.set("country_code", hint.country_code);
  if (hint?.admin1) params.set("admin1", hint.admin1);
  if (hint?.country) params.set("country", hint.country);
  return params;
}

function mergeNwsEnrichment(core: ForecastPayload, full: ForecastPayload): ForecastPayload {
  return {
    ...core,
    // Upgrade to the real NWS observation when present; otherwise keep the
    // Open-Meteo modeled current. hourly/daily/location stay from the core.
    current: full.attribution?.current === "NWS" ? full.current : core.current,
    air_quality: full.air_quality ?? core.air_quality,
    pollen: full.pollen ?? core.pollen,
    temperature_history: full.temperature_history ?? core.temperature_history,
    observed_precip: full.observed_precip ?? core.observed_precip,
    official_text_forecast: full.official_text_forecast ?? core.official_text_forecast,
    alerts: full.alerts ?? core.alerts,
    afd: full.afd ?? core.afd,
    attribution: { ...core.attribution, ...full.attribution },
    source_status: full.source_status,
    freshness: { ...core.freshness, ...full.freshness },
  };
}

const NWS_ENRICHMENT_RETRY_DELAYS_MS = [1_500, 5_000] as const;
const NWS_DEGRADED_RETRY_DELAY_MS = 65_000;

type PendingNwsEnrichmentRetry = {
  lat: number;
  lon: number;
  hint: Partial<LocationResult> | undefined;
  ctrl: AbortController;
  nextAttempt: number;
  retryAt: number;
  resumeImmediatelyOnVisible: boolean;
};

// ── Main Page ─────────────────────────────────────────────────────────

export default function Forecast() {
  const { user } = useUser();
  const { getToken, isSignedIn } = useAuth();
  const { canAccessProduct, isLoaded: entitlementsLoaded } = useEntitlements();
  const { start: startSiteLoading } = useSiteLoading();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialRestorePending = (() => {
    const lat = readFiniteSearchParam(searchParams, "lat");
    const lon = readFiniteSearchParam(searchParams, "lon");
    const q = searchParams.get("q")?.trim();
    return (lat !== null && lon !== null) || Boolean(q);
  })();
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<LocationResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [forecast, setForecast] = useState<ForecastPayload | null>(null);
  const [isLoading, setIsLoading] = useState(initialRestorePending);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>(() => {
    const tabParam = searchParams.get("tab");
    return isTabId(tabParam) ? tabParam : "current";
  });
  const [favoriteLimitMessage, setFavoriteLimitMessage] = useState<string | null>(null);
  const {
    favorites,
    displayChips,
    addFavorite,
    removeFavorite,
    removeRecent,
    isFavorite,
    addRecent,
  } = useForecastLocations(user?.id);

  const searchContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchGenerationRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);
  const nwsRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingNwsRetryRef = useRef<PendingNwsEnrichmentRetry | null>(null);
  const favoriteLimitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initialRestorePendingRef = useRef(initialRestorePending);

  useEffect(() => {
    captureProductAnalyticsEvent("forecast_page_viewed", {
      location_type: "geocoded",
    });
  }, []);

  const meteogramPrefetchModelsKey = useMemo(() => {
    if (!forecast || !entitlementsLoaded) return "";
    return eligibleTemperatureModels(
      forecast.location.latitude,
      forecast.location.longitude,
      canAccessProduct,
    ).join(",");
  }, [
    forecast?.location.latitude,
    forecast?.location.longitude,
    entitlementsLoaded,
    canAccessProduct,
  ]);

  // Warm meteogram cache as soon as a Forecast location is ready — before Models tab open.
  useEffect(() => {
    if (!forecast || !entitlementsLoaded) return;
    const { latitude: lat, longitude: lon } = forecast.location;
    const models = eligibleTemperatureModels(lat, lon, canAccessProduct);
    if (models.length === 0) return;

    prefetchMeteogram(
      {
        lat,
        lon,
        models,
        variables: [...MODELS_TAB_VARIABLES],
        getAuthHeaders: () => meteogramAuthHeaders(getToken, isSignedIn === true),
      },
      "forecast-page-prefetch",
    );
  }, [
    forecast?.location.latitude,
    forecast?.location.longitude,
    meteogramPrefetchModelsKey,
    entitlementsLoaded,
    getToken,
    isSignedIn,
  ]);

  // Deep-link / reload directly onto a coordinate URL: warm the meteogram from
  // the URL coords as soon as entitlements load, in parallel with the
  // forecast-page fetch, instead of waiting for it. The forecast-page echoes
  // coordinate input verbatim, so this hits the same cache key ModelsTabContent
  // uses — prefetchMeteogram dedupes the later forecast-page-prefetch to a cache
  // hit, so it is the same single request fired ~2s earlier (no double fetch).
  // Only coordinate deep-links benefit; `q=` searches have no coords until the
  // geocode resolves and fall through to the forecast-page prefetch above.
  useEffect(() => {
    if (!entitlementsLoaded) return;
    const lat = readFiniteSearchParam(searchParams, "lat");
    const lon = readFiniteSearchParam(searchParams, "lon");
    if (lat === null || lon === null) return;
    const models = eligibleTemperatureModels(lat, lon, canAccessProduct);
    if (models.length === 0) return;
    prefetchMeteogram(
      {
        lat,
        lon,
        models,
        variables: [...MODELS_TAB_VARIABLES],
        getAuthHeaders: () => meteogramAuthHeaders(getToken, isSignedIn === true),
      },
      "forecast-url-prefetch",
    );
    // Fire once when entitlements become available, using the initial URL coords.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entitlementsLoaded]);

  useEffect(() => {
    return () => {
      if (favoriteLimitTimerRef.current) clearTimeout(favoriteLimitTimerRef.current);
    };
  }, []);

  useEffect(() => {
    function onOut(e: globalThis.MouseEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        cancelActiveSearch();
      }
    }
    document.addEventListener("mousedown", onOut);
    return () => document.removeEventListener("mousedown", onOut);
  }, []);

  function cancelActiveSearch() {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    searchGenerationRef.current += 1;
    setIsSearching(false);
    setShowDropdown(false);
  }

  useEffect(() => {
    const lat = readFiniteSearchParam(searchParams, "lat");
    const lon = readFiniteSearchParam(searchParams, "lon");
    const displayName = searchParams.get("name")?.trim() || searchParams.get("q")?.trim() || undefined;
    const timezone = searchParams.get("timezone")?.trim() || undefined;
    const countryCode = searchParams.get("country_code")?.trim() || undefined;
    const admin1 = searchParams.get("admin1")?.trim() || undefined;
    const country = searchParams.get("country")?.trim() || undefined;
    const q = searchParams.get("q")?.trim();
    if (lat !== null && lon !== null) {
      if (displayName) setQuery(displayName);
      void loadByCoords(lat, lon, displayName, {
        display_name: displayName,
        timezone,
        country_code: countryCode,
        admin1,
        country,
      });
      return;
    }
    if (q) {
      setQuery(q);
      void loadByQuery(q);
      return;
    }
    initialRestorePendingRef.current = false;
    setIsLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function syncLocationSearchParams(lat: number, lon: number, name: string, locationHint?: Partial<LocationResult>) {
    const nextParams: Record<string, string> = {
      lat: String(lat),
      lon: String(lon),
      name,
      q: name,
    };
    if (locationHint?.timezone) nextParams.timezone = locationHint.timezone;
    if (locationHint?.country_code) nextParams.country_code = locationHint.country_code;
    if (locationHint?.admin1) nextParams.admin1 = locationHint.admin1;
    if (locationHint?.country) nextParams.country = locationHint.country;
    // On the initial deep-link restore, carry the shared tab + Models-tab view
    // state (set by ModelsTabContent) through this rebuild so the link lands on
    // the right tab/mode. A fresh user-initiated search starts clean (Current),
    // mirroring the setActiveTab reset below.
    if (initialRestorePendingRef.current) {
      const tab = searchParams.get("tab");
      const section = searchParams.get("section");
      const detailModel = searchParams.get("detail_model");
      const models = searchParams.get("models");
      const pinnedRuns = searchParams.get("pinned_runs");
      const ensemblePinnedRuns = searchParams.get("ensemble_pinned_runs");
      const ensembleView = searchParams.get("ensemble_view");
      const ensembleVar = searchParams.get("ensemble_var");
      if (tab) nextParams.tab = tab;
      if (section) nextParams.section = section;
      if (detailModel) nextParams.detail_model = detailModel;
      if (models != null) nextParams.models = models;
      if (pinnedRuns) nextParams.pinned_runs = pinnedRuns;
      if (ensemblePinnedRuns) nextParams.ensemble_pinned_runs = ensemblePinnedRuns;
      if (ensembleView) nextParams.ensemble_view = ensembleView;
      if (ensembleVar) nextParams.ensemble_var = ensembleVar;
    }
    setSearchParams(nextParams, { replace: true });
  }

  function handleSelectTab(nextTab: TabId) {
    setActiveTab(nextTab);
    const next = new URLSearchParams(searchParams);
    // "current" is the default — omit it to keep URLs clean.
    if (nextTab === "current") next.delete("tab");
    else next.set("tab", nextTab);
    setSearchParams(next, { replace: true });
  }

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();

    if (trimmed.length < 2 || (pendingName && query === pendingName)) {
      if (trimmed.length < 2) { setSearchResults([]); setShowDropdown(false); }
      return;
    }

    const generation = searchGenerationRef.current + 1;
    searchGenerationRef.current = generation;
    setIsSearching(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API_V4_BASE}/locations/search?q=${encodeURIComponent(trimmed)}`, {
          cache: "no-store",
        });
        if (searchGenerationRef.current !== generation) {
          return;
        }
        if (!res.ok) throw new Error();
        const data = (await res.json()) as { results?: LocationResult[] };
        const results = data.results ?? [];
        setSearchResults(results);
        setShowDropdown(results.length > 0);
      } catch {
        if (searchGenerationRef.current !== generation) {
          return;
        }
        setSearchResults([]);
        setShowDropdown(false);
      } finally {
        if (searchGenerationRef.current === generation) {
          setIsSearching(false);
        }
      }
    }, 300);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [query, pendingName]);

  // Background NWS enrichment for an already-rendered core. Best-effort: failures
  // leave the Open-Meteo core in place while bounded retries recover transient
  // failures. Guards against a superseded load merging stale data over a newer
  // location.
  function clearNwsRetryTimer() {
    if (nwsRetryTimerRef.current !== null) {
      clearTimeout(nwsRetryTimerRef.current);
      nwsRetryTimerRef.current = null;
    }
  }

  function cancelPendingNwsRetry() {
    clearNwsRetryTimer();
    pendingNwsRetryRef.current = null;
  }

  function resolveNwsUnavailable(ctrl: AbortController) {
    if (ctrl.signal.aborted || loadAbortRef.current !== ctrl) return;
    cancelPendingNwsRetry();
    setForecast((prev) =>
      prev && prev.source_status?.nws === "pending"
        ? { ...prev, source_status: { ...prev.source_status, nws: "unavailable" } }
        : prev,
    );
  }

  function resumePendingNwsRetry() {
    if (document.visibilityState !== "visible") return;
    const pending = pendingNwsRetryRef.current;
    if (!pending) return;
    const remainingDelay = pending.retryAt - Date.now();
    if (remainingDelay > 0) {
      clearNwsRetryTimer();
      nwsRetryTimerRef.current = setTimeout(() => {
        nwsRetryTimerRef.current = null;
        resumePendingNwsRetry();
      }, remainingDelay);
      return;
    }
    clearNwsRetryTimer();
    pendingNwsRetryRef.current = null;
    void enrichWithNws(
      pending.lat,
      pending.lon,
      pending.hint,
      pending.ctrl,
      pending.nextAttempt,
    );
  }

  function scheduleNwsRetry(
    lat: number,
    lon: number,
    hint: Partial<LocationResult> | undefined,
    ctrl: AbortController,
    attempt: number,
    options?: {
      delayMs?: number;
      resumeImmediatelyOnVisible?: boolean;
    },
  ) {
    if (ctrl.signal.aborted || loadAbortRef.current !== ctrl) return;
    if (attempt >= NWS_ENRICHMENT_RETRY_DELAYS_MS.length) {
      resolveNwsUnavailable(ctrl);
      return;
    }

    clearNwsRetryTimer();
    const delayMs = options?.delayMs ?? NWS_ENRICHMENT_RETRY_DELAYS_MS[attempt];
    const resumeImmediatelyOnVisible = options?.resumeImmediatelyOnVisible ?? true;
    pendingNwsRetryRef.current = {
      lat,
      lon,
      hint,
      ctrl,
      nextAttempt: attempt + 1,
      retryAt: Date.now() + delayMs,
      resumeImmediatelyOnVisible,
    };
    if (document.visibilityState !== "visible") {
      if (resumeImmediatelyOnVisible) {
        pendingNwsRetryRef.current.retryAt = Date.now();
      }
      return;
    }

    nwsRetryTimerRef.current = setTimeout(() => {
      nwsRetryTimerRef.current = null;
      resumePendingNwsRetry();
    }, delayMs);
  }

  async function enrichWithNws(
    lat: number,
    lon: number,
    hint: Partial<LocationResult> | undefined,
    ctrl: AbortController,
    attempt = 0,
  ) {
    try {
      const params = buildForecastParams(lat, lon, hint);
      const res = await fetch(`${API_V4_BASE}/forecast-page?${params.toString()}`, {
        signal: ctrl.signal,
        cache: "no-store",
      });
      if (!res.ok) {
        scheduleNwsRetry(lat, lon, hint, ctrl, attempt);
        return;
      }
      const full = (await res.json()) as ForecastPayload;
      if (ctrl.signal.aborted || loadAbortRef.current !== ctrl) return;
      if (full.source_status?.nws === "unavailable") {
        scheduleNwsRetry(lat, lon, hint, ctrl, attempt);
        return;
      }
      if (full.source_status?.nws === "degraded" && full.attribution?.current !== "NWS") {
        setForecast((prev) => (prev ? mergeNwsEnrichment(prev, full) : prev));
        scheduleNwsRetry(lat, lon, hint, ctrl, attempt, {
          delayMs: NWS_DEGRADED_RETRY_DELAY_MS,
          resumeImmediatelyOnVisible: false,
        });
        return;
      }
      cancelPendingNwsRetry();
      setForecast((prev) => (prev ? mergeNwsEnrichment(prev, full) : prev));
    } catch {
      scheduleNwsRetry(lat, lon, hint, ctrl, attempt);
    }
  }

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        resumePendingNwsRetry();
      } else {
        clearNwsRetryTimer();
        if (pendingNwsRetryRef.current?.resumeImmediatelyOnVisible) {
          pendingNwsRetryRef.current.retryAt = Date.now();
        }
      }
    };
    const onOnline = () => resumePendingNwsRetry();
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", onOnline);
      cancelPendingNwsRetry();
      loadAbortRef.current?.abort();
    };
    // Request identity is carried by refs so listeners stay stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadByCoords(lat: number, lon: number, preferredName?: string, locationHint?: Partial<LocationResult>) {
    cancelPendingNwsRetry();
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    const stopSiteLoading = startSiteLoading("Loading forecast");
    setIsLoading(true); setError(null); setShowDropdown(false);
    try {
      const params = buildForecastParams(lat, lon, locationHint);
      let res = await fetch(`${API_V4_BASE}/forecast-page/core?${params.toString()}`, { signal: ctrl.signal });
      if (!res.ok) {
        // Fallback for a backend without the core endpoint (staged deploy). The
        // full endpoint returns the same shape, already NWS-enriched, so the
        // enrich step below no-ops (its nws status is never "pending").
        res = await fetch(`${API_V4_BASE}/forecast-page?${params.toString()}`, { signal: ctrl.signal });
      }
      if (!res.ok) throw new Error("Forecast unavailable for this location.");
      const data = (await res.json()) as ForecastPayload;
      const name = isCoordString(data.location.display_name) && preferredName
        ? preferredName
        : data.location.display_name;
      const persistedHint: Partial<LocationResult> = {
        display_name: name,
        timezone: locationHint?.timezone ?? data.location.timezone,
        country_code: locationHint?.country_code ?? data.location.country_code,
        admin1: locationHint?.admin1 ?? data.location.admin1,
        country: locationHint?.country,
      };
      setForecast({ ...data, location: { ...data.location, display_name: name } });
      setQuery(name);
      setPendingName(name);
      // Reset to Current only for a fresh user-initiated search; the initial
      // deep-link restore keeps the tab parsed from the URL.
      if (!initialRestorePendingRef.current) setActiveTab("current");
      syncLocationSearchParams(data.location.latitude, data.location.longitude, name, persistedHint);
      addRecent(toForecastLocation(name, data.location.latitude, data.location.longitude, persistedHint));
      // Fill in NWS pieces (real-obs current, 7-day narrative, alerts, AFD)
      // without blocking the now-rendered core.
      if (data.source_status?.nws === "pending") {
        void enrichWithNws(data.location.latitude, data.location.longitude, persistedHint, ctrl);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable.");
    } finally {
      stopSiteLoading();
      if (loadAbortRef.current === ctrl) {
        setIsLoading(false);
        // Only the active (non-superseded) load clears the restore flag. A
        // StrictMode/abort-superseded earlier load must not flip it first, or
        // the real load's URL sync would treat this deep link as a fresh search
        // and drop the tab / Models-view params.
        if (initialRestorePendingRef.current) initialRestorePendingRef.current = false;
      }
    }
  }

  async function loadByQuery(q: string) {
    cancelPendingNwsRetry();
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    const stopSiteLoading = startSiteLoading("Loading forecast");
    setIsLoading(true); setError(null); setShowDropdown(false);
    try {
      let res = await fetch(`${API_V4_BASE}/forecast-page/by-query/core?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
      if (!res.ok) {
        // Fallback for a backend without the by-query core endpoint (staged deploy).
        res = await fetch(`${API_V4_BASE}/forecast-page/by-query?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? "Forecast unavailable. Try selecting a location from the dropdown.");
      }
      const data = (await res.json()) as ForecastPayload;
      const name = isCoordString(data.location.display_name) ? q : data.location.display_name;
      const persistedHint: Partial<LocationResult> = {
        display_name: name,
        timezone: data.location.timezone,
        country_code: data.location.country_code,
        admin1: data.location.admin1,
      };
      setForecast({ ...data, location: { ...data.location, display_name: name } });
      setQuery(name);
      setPendingName(name);
      // Reset to Current only for a fresh user-initiated search; the initial
      // deep-link restore keeps the tab parsed from the URL.
      if (!initialRestorePendingRef.current) setActiveTab("current");
      syncLocationSearchParams(data.location.latitude, data.location.longitude, name, persistedHint);
      addRecent(toForecastLocation(name, data.location.latitude, data.location.longitude, persistedHint));
      // Enrich the rendered core with NWS using the resolved coordinates.
      if (data.source_status?.nws === "pending") {
        void enrichWithNws(data.location.latitude, data.location.longitude, persistedHint, ctrl);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable. Try selecting from the dropdown suggestions.");
    } finally {
      stopSiteLoading();
      if (loadAbortRef.current === ctrl) {
        setIsLoading(false);
        // Only the active (non-superseded) load clears the restore flag. A
        // StrictMode/abort-superseded earlier load must not flip it first, or
        // the real load's URL sync would treat this deep link as a fresh search
        // and drop the tab / Models-view params.
        if (initialRestorePendingRef.current) initialRestorePendingRef.current = false;
      }
    }
  }

  function selectLocation(loc: LocationResult) {
    setPendingName(loc.display_name);
    setQuery(loc.display_name);
    setShowDropdown(false);
    setSearchResults([]);
    void loadByCoords(loc.latitude, loc.longitude, loc.display_name, loc);
  }

  function selectForecastLocation(location: ForecastLocation) {
    setPendingName(location.label);
    setQuery(location.label);
    setShowDropdown(false);
    setSearchResults([]);
    void loadByCoords(location.lat, location.lon, location.label, forecastLocationHint(location));
  }

  function showFavoriteLimitMessage() {
    setFavoriteLimitMessage("Remove a favorite first");
    if (favoriteLimitTimerRef.current) clearTimeout(favoriteLimitTimerRef.current);
    favoriteLimitTimerRef.current = setTimeout(() => setFavoriteLimitMessage(null), 2200);
  }

  function toggleFavorite(location: ForecastLocation) {
    if (isFavorite(location.id)) {
      removeFavorite(location.id);
      setFavoriteLimitMessage(null);
      return;
    }

    if (favorites.length >= 5) {
      showFavoriteLimitMessage();
      return;
    }

    addFavorite(location);
    setFavoriteLimitMessage(null);
  }

  function clearSearch() {
    cancelActiveSearch();
    setQuery(""); setPendingName(null); setForecast(null); setError(null);
    setFavoriteLimitMessage(null);
    setSearchResults([]);
    cancelPendingNwsRetry();
    if (loadAbortRef.current) loadAbortRef.current.abort();
    initialRestorePendingRef.current = false;
    setIsLoading(false);
    setSearchParams({}, { replace: true });
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  if (initialRestorePendingRef.current && forecast === null && !error) {
    return null;
  }

  // ── LOADED STATE ───────────────────────────────────────────────────

  if (forecast !== null) {
    const f = forecast;
    const freshChip = freshnessChip(f.freshness.current.state, f.freshness.current.age_minutes);
    const freshnessLabel = freshChip.label.includes("ago") ? `Updated ${freshChip.label}` : freshChip.label;

    const stationParts: string[] = [];
    if (f.attribution.current) stationParts.push(f.attribution.current);
    if (f.current.station?.name) stationParts.push(f.current.station.name);
    if (f.current.station?.distance_km != null) stationParts.push(`${f.current.station.distance_km} km`);
    const stationMeta = stationParts.join(" · ");
    const currentLocation = toForecastLocation(f.location.display_name, f.location.latitude, f.location.longitude, {
      display_name: f.location.display_name,
      latitude: f.location.latitude,
      longitude: f.location.longitude,
      timezone: f.location.timezone,
      country_code: f.location.country_code,
      admin1: f.location.admin1,
    });
    const currentIsFavorite = isFavorite(currentLocation.id);

    return (
      <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 md:-mt-16 pt-16 min-h-screen bg-[#07111f] text-white">

        {/* Top Bar */}
        <div>
          <div className="mx-auto max-w-6xl px-5 md:px-8 py-3 flex items-center gap-3 border-b-[0.5px] border-white/[0.08]">
            <button
              type="button"
              onClick={clearSearch}
              className="flex-none flex items-center gap-1 text-[12px] text-white/35 transition hover:text-white/60"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Search
            </button>
            <div className="flex-1 min-w-0 flex items-baseline gap-2 overflow-hidden">
              <h1 className="text-[15px] font-medium text-white truncate">{f.location.display_name}</h1>
              <button
                type="button"
                onClick={() => toggleFavorite(currentLocation)}
                title={currentIsFavorite ? "Remove favorite" : "Save favorite"}
                aria-label={currentIsFavorite ? "Remove favorite" : "Save favorite"}
                aria-pressed={currentIsFavorite}
                className={`flex-none text-white/35 transition duration-200 hover:text-amber-200 focus:outline-none focus-visible:text-amber-200 ${currentIsFavorite ? "scale-105 text-amber-300" : ""}`}
              >
                <Star className="h-3.5 w-3.5 transition-all duration-200" fill={currentIsFavorite ? "currentColor" : "none"} />
              </button>
              {favoriteLimitMessage && (
                <span className="text-[12px] text-amber-200/85 whitespace-nowrap">{favoriteLimitMessage}</span>
              )}
              {stationMeta && (
                <span className="hidden sm:inline text-[12px] text-white/35 whitespace-nowrap">{stationMeta}</span>
              )}
            </div>
            <div className="flex-none flex items-center gap-2">
              <span className={`text-[12px] ${freshChip.color}`}>{freshnessLabel}</span>
              <button
                type="button"
                onClick={() => void loadByCoords(f.location.latitude, f.location.longitude, f.location.display_name, { country_code: f.location.country_code ?? undefined })}
                disabled={isLoading}
                title="Refresh forecast"
                className="text-white/30 transition hover:text-white/60 disabled:opacity-30"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
              </button>
            </div>
            <Link
              to={viewerHref(f.location.latitude, f.location.longitude)}
              className="flex-none hidden sm:inline-flex items-center gap-1.5 rounded-lg border border-[0.5px] border-cyan-300/30 bg-cyan-300/[0.06] px-3 py-1.5 text-[12px] font-medium text-cyan-200 transition hover:bg-cyan-300/[0.10]"
            >
              Open In Viewer <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
        </div>

        {/* Tab Bar */}
        <div>
          <div className="mx-auto max-w-6xl px-5 md:px-8 border-b-[0.5px] border-white/[0.08]">
            <div className="flex overflow-x-auto -mb-px">
              {TABS.map(tab => (
                <button
                  key={tab.id}
                  type="button"
                  data-forecast-tab={tab.id}
                  aria-selected={activeTab === tab.id}
                  onClick={() => handleSelectTab(tab.id)}
                  className={`flex-none px-4 py-3 text-[13px] whitespace-nowrap border-b-2 transition-colors ${
                    activeTab === tab.id
                      ? "border-white text-white font-medium"
                      : "border-transparent text-white/45 hover:text-white/65"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Tab Content */}
        <div className="mx-auto max-w-6xl px-5 md:px-8 py-6 pb-12">
          {activeTab === "current"    && <CurrentTab forecast={f} checkingAlerts={f.source_status?.nws === "pending"} />}
          {activeTab === "hourly"     && <HourlyTab hourly={f.hourly} />}
          {activeTab === "7day"       && <SevenDayTab daily={f.daily} textForecast={f.official_text_forecast} />}
          {activeTab === "extended"   && <ExtendedTab daily={f.daily} attribution={f.attribution.daily} />}
          {activeTab === "models"     && (
            <ModelsTabContent
              lat={f.location.latitude}
              lon={f.location.longitude}
              timezone={f.location.timezone}
              locationText={formatLocationText(f.location.display_name, f.location.latitude, f.location.longitude)}
            />
          )}
          {activeTab === "ensembles" && (
            <EnsemblesTabContent
              lat={f.location.latitude}
              lon={f.location.longitude}
              timezone={f.location.timezone}
              locationText={formatLocationText(f.location.display_name, f.location.latitude, f.location.longitude)}
            />
          )}
          {activeTab === "discussion" && <DiscussionTab afd={f.afd} />}
        </div>

      </div>
    );
  }

  // ── EMPTY STATE ────────────────────────────────────────────────────

  const searchBox = (
    <div ref={searchContainerRef} className="relative">
      <label className="flex items-center gap-3 rounded-[1.4rem] border border-white/12 bg-slate-950/24 px-5 py-4 backdrop-blur-sm shadow-[0_18px_50px_rgba(0,0,0,0.18)] transition focus-within:border-cyan-200/28 focus-within:bg-slate-950/30">
        <Search className="h-3.5 w-3.5 flex-none text-cyan-200/75" />
        <input
          ref={inputRef}
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            if (e.target.value !== pendingName) setPendingName(null);
          }}
          onKeyDown={e => {
            if (e.key === "Enter" && query.trim().length >= 2) {
              if (searchResults.length > 0) {
                selectLocation(searchResults[0]);
              } else {
                void loadByQuery(query.trim());
              }
            }
            if (e.key === "Escape") cancelActiveSearch();
          }}
          onBlur={() => {
            window.setTimeout(() => {
              if (!searchContainerRef.current?.contains(document.activeElement)) {
                cancelActiveSearch();
              }
            }, 0);
          }}
          onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
          placeholder="Search city or zip code"
          className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/35"
          autoComplete="off"
          spellCheck={false}
        />
        {isSearching ? (
          <div className="h-3 w-3 flex-none animate-spin rounded-full border border-cyan-300/25 border-t-cyan-300" />
        ) : query ? (
          <button type="button" onClick={clearSearch} className="flex-none rounded-full p-0.5 text-white/30 transition hover:text-white/60">
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </label>

      {showDropdown && searchResults.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-20 mt-3 space-y-1 rounded-[1.4rem] border border-white/10 bg-[#091221]/92 p-2 shadow-[0_28px_70px_rgba(0,0,0,0.34)] backdrop-blur-md">
          {searchResults.slice(0, 6).map((r, i) => (
            <button
              key={i}
              type="button"
              onMouseDown={e => e.preventDefault()}
              onClick={() => selectLocation(r)}
              className="w-full rounded-xl border border-white/8 bg-white/[0.03] px-4 py-2.5 text-left text-sm font-medium text-white transition hover:border-white/14 hover:bg-white/[0.05]"
            >
              {r.display_name}
            </button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 space-y-0 text-white md:-mt-16">
      <section className="relative overflow-hidden border-b border-white/8 bg-[#07111f] px-5 pb-10 pt-20 md:px-8 md:pb-14 md:pt-28 lg:pt-32">
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-92"
          style={{
            backgroundImage: `
              radial-gradient(circle at 16% 24%, rgba(125,211,252,0.14), transparent 0 28%),
              radial-gradient(circle at 82% 18%, rgba(56,189,248,0.12), transparent 0 24%),
              radial-gradient(circle at 72% 74%, rgba(34,211,238,0.08), transparent 0 22%),
              linear-gradient(115deg, rgba(8,18,34,0.98) 0%, rgba(9,22,39,0.9) 34%, rgba(7,17,31,0.76) 58%, rgba(4,10,20,0.94) 100%),
              linear-gradient(180deg, rgba(7,17,31,0.58), rgba(7,17,31,0.88))
            `,
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)",
            backgroundSize: "88px 88px",
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle at 78% 24%, rgba(6,12,24,0.5), rgba(6,12,24,0.22) 18%, transparent 42%), radial-gradient(circle at 18% 78%, rgba(8,18,34,0.42), transparent 0 30%)",
          }}
        />

        <div className="relative mx-auto grid max-w-6xl items-center gap-10 py-8 lg:min-h-[calc(100svh-8rem)] lg:grid-cols-[1.1fr_0.9fr] lg:gap-14">
          <div className="max-w-4xl text-center lg:text-left">
            <SectionEyebrow>Forecast</SectionEyebrow>
            <h1 className="mt-4 max-w-4xl text-balance text-4xl font-semibold tracking-[-0.04em] text-white drop-shadow-[0_8px_28px_rgba(0,0,0,0.45)] sm:text-5xl lg:mt-8 lg:text-7xl lg:leading-[0.98]">
              Local weather,
              <br />
              <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic tracking-[-0.03em] text-cyan-200">
                clearly briefed.
              </span>
            </h1>
            <p className="mx-auto mt-8 max-w-2xl text-balance text-base leading-8 text-white/74 md:text-lg lg:mx-0 lg:text-left">
              Official NWS conditions and forecast periods, plus a 15-day extended outlook with a direct handoff to the viewer for deeper analysis.
            </p>
            <div className="mx-auto mt-10 max-w-xl lg:mx-0">
              {searchBox}
              {!isLoading && displayChips.length > 0 && (
                <div className="forecast-scroll mt-6 flex max-w-full flex-nowrap gap-2 overflow-x-auto pb-1 lg:max-w-xl">
                  {displayChips.map(location => {
                    const chipIsFavorite = isFavorite(location.id);
                    return (
                      <div key={location.id} className="group relative flex-none">
                        <button
                          type="button"
                          onClick={() => selectForecastLocation(location)}
                          className={`whitespace-nowrap rounded-xl border border-white/10 bg-slate-950/18 py-1.5 text-xs text-white/58 backdrop-blur-sm transition hover:border-white/18 hover:bg-white/[0.05] hover:text-white/78 focus:outline-none focus-visible:border-cyan-200/45 focus-visible:text-white/82 ${chipIsFavorite ? "pl-2.5 pr-7" : "pl-3 pr-7"}`}
                        >
                          {chipIsFavorite ? <span className="mr-1.5 text-amber-300">★</span> : null}
                          {location.label}
                        </button>
                        <button
                          type="button"
                          onClick={event => {
                            event.stopPropagation();
                            if (chipIsFavorite) {
                              removeFavorite(location.id);
                            } else {
                              removeRecent(location.id);
                            }
                          }}
                          title={chipIsFavorite ? "Remove favorite" : "Remove from recent searches"}
                          aria-label={chipIsFavorite ? `Remove ${location.label} from favorites` : `Remove ${location.label} from recent searches`}
                          className="absolute right-1.5 top-1/2 flex h-4 w-4 -translate-y-1/2 items-center justify-center rounded-full text-white/0 transition hover:bg-white/10 hover:text-white/75 focus:bg-white/10 focus:text-white/75 focus:outline-none group-hover:text-white/45 group-focus-within:text-white/45"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            {error && (
              <div className="mx-auto mt-4 max-w-xl rounded-2xl border border-rose-300/18 bg-rose-300/10 px-4 py-3 text-left text-sm text-rose-100 lg:mx-0">
                {error}
                <div className="mt-1 text-xs text-rose-200/70">Try selecting a location from the search dropdown.</div>
              </div>
            )}
          </div>

          <div className="relative hidden lg:block">
            <div className="absolute -left-6 top-12 h-28 w-px bg-gradient-to-b from-transparent via-cyan-200/35 to-transparent" />
            {isLoading ? (
              <div className="pl-10 animate-pulse">
                <div className="h-3 w-28 rounded-lg bg-white/8" />
                <div className="mt-8 space-y-4">
                  <div className="h-20 rounded-[1.4rem] border border-white/8 bg-white/[0.03]" />
                  <div className="h-20 rounded-[1.4rem] border border-white/8 bg-white/[0.03]" />
                  <div className="h-20 rounded-[1.4rem] border border-white/8 bg-white/[0.03]" />
                </div>
              </div>
            ) : (
              <div className="pl-10">
                <SectionLabel>Forecast Desk</SectionLabel>
                <div className="mt-6 space-y-4">
                  <div className="rounded-[1.45rem] border border-white/10 bg-slate-950/20 p-5 backdrop-blur-sm">
                    <div className="text-sm font-medium text-white">Official U.S. Forecast</div>
                    <div className="mt-2 max-w-sm text-sm leading-7 text-white/56">
                      Current observations, forecast periods, active alerts, and Area Forecast Discussions are surfaced when NWS coverage is available.
                    </div>
                  </div>
                  <div className="rounded-[1.45rem] border border-white/10 bg-slate-950/16 p-5 backdrop-blur-sm">
                    <div className="text-sm font-medium text-white">Extended Outlook</div>
                    <div className="mt-2 max-w-sm text-sm leading-7 text-white/56">
                      Open-Meteo carries the longer-range daily outlook so the page stays useful beyond the official forecast window.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="mt-10 flex justify-center lg:col-span-2 lg:mt-0">
            <div className="h-6 w-6 rounded-full border border-white/12 bg-white/[0.03] text-white/40">
              <div className="flex h-full items-center justify-center text-base leading-none">⌄</div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-white/8 bg-[#0b1527] px-5 py-14 md:px-8">
        <div className="mx-auto grid max-w-6xl gap-5 lg:grid-cols-3">
          <div className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
            <MapPinned className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-4 text-lg font-semibold tracking-tight text-white">Any location, instantly</h2>
            <p className="mt-2 text-sm leading-7 text-white/55">
              U.S. city, state, zip code, or Canada location. U.S. queries route through the NWS hybrid pipeline; international uses Open-Meteo.
            </p>
          </div>
          <div className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
            <CloudSun className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-4 text-lg font-semibold tracking-tight text-white">Official data, clearly surfaced</h2>
            <p className="mt-2 text-sm leading-7 text-white/55">
              Current obs from the best available NWS station. Text forecasts and Area Forecast Discussions included for U.S. locations.
            </p>
          </div>
          <div className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
            <ArrowRight className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-4 text-lg font-semibold tracking-tight text-white">One click to the viewer</h2>
            <p className="mt-2 text-sm leading-7 text-white/55">
              Location stays locked as you move into the interactive map. Models, radar, SPC outlooks, and more are a single click away.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
