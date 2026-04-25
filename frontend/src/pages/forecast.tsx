import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  CloudSun,
  MapPinned,
  RefreshCw,
  Search,
  X,
} from "lucide-react";

import { API_V4_BASE, MAP_VIEW_DEFAULTS, getReleaseSha } from "@/lib/config";
import { buildPermalinkSearch } from "@/lib/permalink";

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

const FEATURED_LOCATIONS = [
  { name: "Denver, CO", latitude: 39.7392, longitude: -104.9903 },
  { name: "Chicago, IL", latitude: 41.8781, longitude: -87.6298 },
  { name: "Miami, FL", latitude: 25.7617, longitude: -80.1918 },
  { name: "Seattle, WA", latitude: 47.6062, longitude: -122.3321 },
] as const;

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
    timezone: string | null;
    country_code: string | null;
    admin1: string | null;
    resolved_by: string;
  };
  source_status: { primary_region_mode: string; nws: string; open_meteo: string };
  current: CurrentData;
  hourly: HourlyEntry[];
  daily: DailyEntry[];
  official_text_forecast: { source: string; generated_at: string | null; periods: TextForecastPeriod[] } | null;
  afd: { office: string; issued_at: string | null; headline: string; text: string | null } | null;
  alerts: AlertEntry[];
  attribution: { current: string | null; hourly: string | null; daily: string | null };
  freshness: {
    current: { state: string | null; observed_at: string | null; age_minutes: number | null };
    afd: { state: string; issued_at: string | null; age_hours: number | null };
  };
};

// ── Tab config ────────────────────────────────────────────────────────

type TabId = "hourly" | "7day" | "extended" | "models" | "discussion";

const TABS: { id: TabId; label: string }[] = [
  { id: "hourly", label: "Hourly" },
  { id: "7day", label: "7-day" },
  { id: "extended", label: "Extended" },
  { id: "models", label: "Models" },
  { id: "discussion", label: "Discussion" },
];

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
    case "extreme":  return { border: "border-rose-300/25",   bg: "bg-rose-300/10",       text: "text-rose-100",   badge: "bg-rose-300/18 text-rose-100" };
    case "severe":   return { border: "border-orange-300/20", bg: "bg-orange-300/8",       text: "text-orange-100", badge: "bg-orange-300/16 text-orange-100" };
    case "moderate": return { border: "border-amber-300/20",  bg: "bg-amber-300/8",        text: "text-amber-100",  badge: "bg-amber-300/14 text-amber-100" };
    default:         return { border: "border-yellow-300/16", bg: "bg-yellow-300/[0.05]",  text: "text-yellow-100", badge: "bg-yellow-300/12 text-yellow-100" };
  }
}

function freshnessChip(state: string | null, ageMinutes: number | null): { label: string; color: string } {
  if (state === "fresh")   return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Fresh",             color: "text-emerald-400" };
  if (state === "aging")   return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Aging",             color: "text-amber-400" };
  if (state === "stale")   return { label: ageMinutes != null ? `${ageMinutes}m ago · stale` : "Stale",     color: "text-rose-400" };
  if (state === "modeled") return { label: "Modeled",                                                        color: "text-slate-400 dark:text-white/45" };
  return { label: "Recent", color: "text-slate-400 dark:text-white/45" };
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

function readFiniteSearchParam(searchParams: URLSearchParams, key: string): number | null {
  const rawValue = searchParams.get(key);
  if (rawValue === null) return null;
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : null;
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

// ── Metadata item (conditions strip) ─────────────────────────────────

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-white/35">{label}</div>
      <div className="mt-0.5 text-[13px] font-medium text-white/80">{value}</div>
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
  const PRECIP_T = 97;
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
  const hAnchor = hoverIdx !== null && hoverIdx <= 1 ? "start" : hoverIdx !== null && hoverIdx >= endIdx - 1 ? "end" : "middle";

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
          <line x1={0} y1={PRECIP_T - 1} x2={VW} y2={PRECIP_T - 1}
            stroke="rgba(255,255,255,0.07)" strokeWidth={1} />
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
            x={hAnchor === "start" ? hX : hAnchor === "end" ? hX - 52 : hX - 26}
            y={hY - 22} width={52} height={16} rx={3}
            fill="rgba(7,17,31,0.88)"
          />
          <text x={hAnchor === "start" ? hX + 26 : hAnchor === "end" ? hX - 26 : hX}
            y={hY - 10} textAnchor="middle" fontSize={9.5} fontWeight="500" fill="rgba(255,255,255,0.90)">
            {hEntry.temperature_f != null ? `${hEntry.temperature_f}°` : "--"} · {formatHour(hEntry.time)}
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
            <text x={x} y={VH - 3} textAnchor={anchor}
              fontSize={8.5} fill="rgba(255,255,255,0.30)">
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
            <WeatherIcon code={entry.weather_code} size={22} />
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

function AlertsBanner({ alerts }: { alerts: AlertEntry[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  function toggle(i: number) {
    setExpanded(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });
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
                  {alert.severity && (
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${s.badge}`}>
                      {alert.severity}
                    </span>
                  )}
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
  const entries = daily.slice(0, 7);
  if (!entries.length) return null;

  const lows  = entries.map(e => e.low_f  ?? null).filter((v): v is number => v !== null);
  const highs = entries.map(e => e.high_f ?? null).filter((v): v is number => v !== null);
  if (!lows.length || !highs.length) return null;

  const globalMin = Math.min(...lows);
  const globalMax = Math.max(...highs);
  const span = globalMax - globalMin || 1;

  return (
    <div>
      {entries.map((entry, i) => {
        const low  = entry.low_f  ?? globalMin;
        const high = entry.high_f ?? globalMax;
        const leftPct  = ((low  - globalMin) / span) * 100;
        const widthPct = ((high - low) / span) * 100;
        const pop = entry.pop_pct ?? 0;
        return (
          <div
            key={i}
            className={`flex items-center gap-3 py-3 ${i < entries.length - 1 ? "border-b-[0.5px] border-white/[0.06]" : ""}`}
          >
            <div className="w-10 flex-none text-[13px] font-medium text-white/60">
              {formatDayLabel(entry.date, i)}
            </div>
            <WeatherIcon code={entry.icon} size={16} className="flex-none" />
            <div className="flex-none w-52 text-[13px] text-white/55 truncate hidden sm:block">
              {entry.short_text ?? ""}
            </div>
            <div className="relative flex-1 h-[3px] rounded-full bg-slate-100 dark:bg-white/[0.07] overflow-hidden">
              <div
                className="absolute inset-y-0 rounded-full bg-sky-400/80 dark:bg-gradient-to-r dark:from-sky-400/60 dark:to-cyan-300/80"
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              />
            </div>
            <div className="flex gap-1.5 w-16 flex-none justify-end text-[13px]">
              <span className="font-medium text-white">{entry.high_f ?? "--"}°</span>
              <span className="text-white/30">{entry.low_f ?? "--"}°</span>
            </div>
            <div className={`w-8 flex-none text-right text-[13px] ${precipColor(entry.pop_pct)}`}>
              {pop > 0 ? `${pop}%` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── NWS Cards Grid (7-day tab) ────────────────────────────────────────

function NWSCardsGrid({ data }: { data: NonNullable<ForecastPayload["official_text_forecast"]> }) {
  const [showAll, setShowAll] = useState(false);
  if (!data.periods.length) return null;
  const visible = showAll ? data.periods : data.periods.slice(0, 6);

  return (
    <div>
      {data.generated_at && (
        <p className="mb-4 text-[11px] text-white/30">
          NWS Official · Generated {formatObservedAt(data.generated_at)}
        </p>
      )}
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

  const lows  = daily.map(e => e.low_f  ?? null).filter((v): v is number => v !== null);
  const highs = daily.map(e => e.high_f ?? null).filter((v): v is number => v !== null);
  const globalMin = lows.length  ? Math.min(...lows)  : 0;
  const globalMax = highs.length ? Math.max(...highs) : 1;
  const span = globalMax - globalMin || 1;

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-white/[0.03] p-4 md:p-5">
        <p className="mb-3 text-[11px] font-medium uppercase tracking-[0.20em] text-white/40">
          Temperature · 15-Day Outlook
        </p>
        <DailyTempChart daily={daily} />
      </div>
      <div>
      {attribution && (
        <p className="mb-4 text-[11px] text-white/30">Source: {attribution}</p>
      )}
      {daily.map((entry, i) => {
        const low  = entry.low_f  ?? globalMin;
        const high = entry.high_f ?? globalMax;
        const leftPct  = ((low  - globalMin) / span) * 100;
        const widthPct = ((high - low) / span) * 100;
        const pop = entry.pop_pct ?? 0;
        return (
          <div
            key={i}
            className={`flex items-center gap-3 py-3 ${i < daily.length - 1 ? "border-b-[0.5px] border-white/[0.06]" : ""}`}
          >
            <div className="w-10 flex-none text-[13px] font-medium text-white/60">
              {formatDayLabel(entry.date, i)}
            </div>
            <WeatherIcon code={entry.icon} size={16} className="flex-none" />
            <div className="flex-none w-52 text-[13px] text-white/50 truncate hidden sm:block">
              {entry.short_text ?? ""}
            </div>
            <div className="relative flex-1 h-[3px] rounded-full bg-slate-100 dark:bg-white/[0.07] overflow-hidden">
              <div
                className="absolute inset-y-0 rounded-full bg-sky-400/80 dark:bg-gradient-to-r dark:from-sky-400/60 dark:to-cyan-300/80"
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              />
            </div>
            <div className="flex gap-1.5 w-16 flex-none justify-end text-[13px]">
              <span className="font-medium text-white">{entry.high_f ?? "--"}°</span>
              <span className="text-white/30">{entry.low_f ?? "--"}°</span>
            </div>
            <div className={`w-8 flex-none text-right text-[13px] ${precipColor(entry.pop_pct)}`}>
              {pop > 0 ? `${pop}%` : ""}
            </div>
          </div>
        );
      })}
      </div>
    </div>
  );
}

// ── Models Tab ────────────────────────────────────────────────────────

function ModelsTab() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <p className="text-[15px] font-medium text-white/65">Model guidance coming soon</p>
      <p className="mt-2 text-[13px] text-white/35">
        Model and ensemble charts, including meteograms, are coming soon. In the meantime, you can view the data directly via the map viewer.
      </p>
    </div>
  );
}

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

// ── Main Page ─────────────────────────────────────────────────────────

export default function Forecast() {
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
  const [activeTab, setActiveTab] = useState<TabId>("hourly");

  const searchContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);
  const initialRestorePendingRef = useRef(initialRestorePending);

  useEffect(() => {
    function onOut(e: globalThis.MouseEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", onOut);
    return () => document.removeEventListener("mousedown", onOut);
  }, []);

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
    setSearchParams(nextParams, { replace: true });
  }

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();

    if (trimmed.length < 2 || (pendingName && query === pendingName)) {
      if (trimmed.length < 2) { setSearchResults([]); setShowDropdown(false); }
      return;
    }

    setIsSearching(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API_V4_BASE}/locations/search?q=${encodeURIComponent(trimmed)}`);
        if (!res.ok) throw new Error();
        const data = (await res.json()) as { results?: LocationResult[] };
        const results = data.results ?? [];
        setSearchResults(results);
        setShowDropdown(results.length > 0);
      } catch { setSearchResults([]); setShowDropdown(false); }
      finally { setIsSearching(false); }
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, pendingName]);

  async function loadByCoords(lat: number, lon: number, preferredName?: string, locationHint?: Partial<LocationResult>) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    setIsLoading(true); setError(null); setShowDropdown(false);
    try {
      const params = new URLSearchParams({ lat: String(lat), lon: String(lon) });
      const displayName = preferredName ?? locationHint?.display_name ?? null;
      if (displayName) params.set("display_name", displayName);
      if (locationHint?.timezone) params.set("timezone", locationHint.timezone);
      if (locationHint?.country_code) params.set("country_code", locationHint.country_code);
      if (locationHint?.admin1) params.set("admin1", locationHint.admin1);
      if (locationHint?.country) params.set("country", locationHint.country);

      const res = await fetch(`${API_V4_BASE}/forecast-page?${params.toString()}`, { signal: ctrl.signal });
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
      setActiveTab("hourly");
      syncLocationSearchParams(data.location.latitude, data.location.longitude, name, persistedHint);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable.");
    } finally {
      if (loadAbortRef.current === ctrl) setIsLoading(false);
      if (initialRestorePendingRef.current) initialRestorePendingRef.current = false;
    }
  }

  async function loadByQuery(q: string) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    setIsLoading(true); setError(null); setShowDropdown(false);
    try {
      const res = await fetch(`${API_V4_BASE}/forecast-page/by-query?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
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
      setActiveTab("hourly");
      syncLocationSearchParams(data.location.latitude, data.location.longitude, name, persistedHint);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable. Try selecting from the dropdown suggestions.");
    } finally {
      if (loadAbortRef.current === ctrl) setIsLoading(false);
      if (initialRestorePendingRef.current) initialRestorePendingRef.current = false;
    }
  }

  function selectLocation(loc: LocationResult) {
    setPendingName(loc.display_name);
    setQuery(loc.display_name);
    setShowDropdown(false);
    setSearchResults([]);
    void loadByCoords(loc.latitude, loc.longitude, loc.display_name, loc);
  }

  function clearSearch() {
    setQuery(""); setPendingName(null); setForecast(null); setError(null);
    setSearchResults([]); setShowDropdown(false);
    if (loadAbortRef.current) loadAbortRef.current.abort();
    initialRestorePendingRef.current = false;
    setIsLoading(false);
    setSearchParams({}, { replace: true });
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  if (initialRestorePendingRef.current && forecast === null && !error) {
    return (
      <div className="relative left-1/2 right-1/2 -mt-12 w-screen min-h-screen -translate-x-1/2 bg-[#07111f] pt-16 text-white md:-mt-16">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-6 md:px-8">
          <div className="h-10 w-48 animate-pulse rounded-xl bg-white/[0.06]" />
          <div className="grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
            <div className="space-y-4 rounded-[1.6rem] border border-white/[0.08] bg-white/[0.03] p-6">
              <div className="h-5 w-36 animate-pulse rounded-lg bg-white/[0.07]" />
              <div className="h-12 w-64 animate-pulse rounded-xl bg-white/[0.08]" />
              <div className="h-24 animate-pulse rounded-[1.25rem] bg-white/[0.05]" />
            </div>
            <div className="space-y-4 rounded-[1.6rem] border border-white/[0.08] bg-white/[0.03] p-6">
              <div className="h-4 w-24 animate-pulse rounded-lg bg-white/[0.07]" />
              <div className="h-20 animate-pulse rounded-[1.1rem] bg-white/[0.05]" />
              <div className="h-20 animate-pulse rounded-[1.1rem] bg-white/[0.05]" />
            </div>
          </div>
        </div>
      </div>
    );
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

        {/* Conditions Strip */}
        <div>
          <div className="mx-auto max-w-6xl px-5 md:px-8 py-5 flex flex-wrap items-center gap-5 border-b-[0.5px] border-white/[0.08]">
            <div className="flex items-center gap-3 flex-none">
              <WeatherIcon code={f.current.icon} size={32} className="flex-none" />
              <div>
                <div className="text-[36px] font-medium leading-none text-white">
                  {f.current.temperature_f ?? "--"}°
                </div>
                <div className="mt-1 text-[13px] text-white/55">
                  {f.current.short_text ?? ""}
                </div>
              </div>
            </div>

            <div className="hidden sm:block self-stretch w-px bg-white/[0.08] flex-none" style={{ minHeight: 44 }} />

            <div className="flex flex-wrap gap-x-6 gap-y-3">
              {(() => {
                const fl = feelsLikeF(f.current.temperature_f, f.current.wind_speed_mph, f.current.humidity_pct);
                return fl !== null && fl !== f.current.temperature_f
                  ? <MetaItem label="Feels Like" value={`${fl}°`} />
                  : null;
              })()}
              {f.current.dewpoint_f != null && (
                <MetaItem label="Dew Point" value={`${f.current.dewpoint_f}°`} />
              )}
              {f.current.humidity_pct != null && (
                <MetaItem label="Humidity" value={`${f.current.humidity_pct}%`} />
              )}
              <MetaItem
                label="Wind"
                value={`${degreesToCardinal(f.current.wind_dir_deg)} ${f.current.wind_speed_mph ?? "--"} mph${f.current.wind_gust_mph ? ` · G${f.current.wind_gust_mph}` : ""}`}
              />
              {f.current.pressure_mb != null && (
                <MetaItem label="Pressure" value={`${f.current.pressure_mb} mb`} />
              )}
              {f.current.visibility_mi != null && (
                <MetaItem label="Visibility" value={`${f.current.visibility_mi} mi`} />
              )}
            </div>
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
                  onClick={() => setActiveTab(tab.id)}
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
          {f.alerts.length > 0 && (
            <div className="mb-6">
              <AlertsBanner alerts={f.alerts} />
            </div>
          )}
          {activeTab === "hourly"     && <HourlyTab hourly={f.hourly} />}
          {activeTab === "7day"       && <SevenDayTab daily={f.daily} textForecast={f.official_text_forecast} />}
          {activeTab === "extended"   && <ExtendedTab daily={f.daily} attribution={f.attribution.daily} />}
          {activeTab === "models"     && <ModelsTab />}
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
            if (e.key === "Escape") setShowDropdown(false);
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
              {!isLoading && (
                <div className="mt-6 flex flex-wrap gap-2 lg:max-w-xl">
                  {FEATURED_LOCATIONS.map(place => (
                    <button
                      key={place.name}
                      type="button"
                      onClick={() => {
                        setPendingName(place.name);
                        setQuery(place.name);
                        void loadByCoords(place.latitude, place.longitude, place.name);
                      }}
                      className="rounded-xl border border-white/10 bg-slate-950/18 px-3 py-1.5 text-xs text-white/58 backdrop-blur-sm transition hover:border-white/18 hover:bg-white/[0.05] hover:text-white/78"
                    >
                      {place.name}
                    </button>
                  ))}
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
                  <div className="rounded-[1.45rem] border border-white/10 bg-slate-950/16 p-5 backdrop-blur-sm">
                    <div className="text-sm font-medium text-white">Fast Entry</div>
                    <div className="mt-2 max-w-sm text-sm leading-7 text-white/56">
                      Search by city, zip code, or jump straight in with the featured U.S. locations on the left.
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
              City, state, zip code, or international location. U.S. queries route through the NWS hybrid pipeline; international uses Open-Meteo.
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
