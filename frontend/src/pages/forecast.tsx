import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronLeft,
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

import { API_V4_BASE, MAP_VIEW_DEFAULTS } from "@/lib/config";
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
    country_code: string | null;
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
    case "extreme": return { border: "border-rose-300/25", bg: "bg-rose-300/10", text: "text-rose-100", badge: "bg-rose-300/18 text-rose-100" };
    case "severe":  return { border: "border-orange-300/20", bg: "bg-orange-300/8", text: "text-orange-100", badge: "bg-orange-300/16 text-orange-100" };
    case "moderate": return { border: "border-amber-300/20", bg: "bg-amber-300/8", text: "text-amber-100", badge: "bg-amber-300/14 text-amber-100" };
    default:        return { border: "border-yellow-300/16", bg: "bg-yellow-300/[0.05]", text: "text-yellow-100", badge: "bg-yellow-300/12 text-yellow-100" };
  }
}

function freshnessChip(state: string | null, ageMinutes: number | null): { label: string; color: string } {
  if (state === "fresh")  return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Fresh", color: "text-emerald-400" };
  if (state === "aging")  return { label: ageMinutes != null ? `${ageMinutes}m ago` : "Aging", color: "text-amber-400" };
  if (state === "stale")  return { label: ageMinutes != null ? `${ageMinutes}m ago · stale` : "Stale", color: "text-rose-400" };
  if (state === "modeled") return { label: "Modeled", color: "text-white/45" };
  return { label: "Recent", color: "text-white/45" };
}

function viewerHref(lat: number, lon: number): string {
  return `/viewer${buildPermalinkSearch({ region: MAP_VIEW_DEFAULTS.region, lat, lon, z: 7 })}`;
}

// ── Weather Icon ──────────────────────────────────────────────────────

function WeatherIcon({ code, className }: { code: string; className?: string }) {
  const cls = className ?? "h-5 w-5";
  switch (code) {
    case "clear-day":          return <Sun className={cls} />;
    case "clear-night":        return <Moon className={cls} />;
    case "partly-cloudy-day":  return <CloudSun className={cls} />;
    case "partly-cloudy-night": return <CloudMoon className={cls} />;
    case "cloudy": case "fog": return <Cloud className={cls} />;
    case "drizzle":            return <CloudDrizzle className={cls} />;
    case "rain": case "sleet": return <CloudRain className={cls} />;
    case "snow":               return <CloudSnow className={cls} />;
    case "thunderstorm":       return <CloudLightning className={cls} />;
    case "wind":               return <Wind className={cls} />;
    default:                   return <Cloud className={cls} />;
  }
}

// ── Section label ─────────────────────────────────────────────────────

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-[0.26em] text-white/40">
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

  // SVG layout: temp chart on top, precip probability curve in a band below
  const VW = 460;
  const VH = 145;
  const TEMP_T = 18;    // top of temp zone
  const TEMP_B = 90;    // bottom of temp zone
  const PRECIP_T = 97;  // top of precip zone (7px gap acts as divider)
  const PRECIP_B = 135; // bottom of precip zone (38px tall = max curve height)

  const xAt = (i: number) => (i / (entries.length - 1)) * VW;
  const yAt = (t: number) => TEMP_T + (1 - (t - minT) / range) * (TEMP_B - TEMP_T);

  // Smooth bezier helpers
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

  // Precip probability curve — each point's y = PRECIP_B minus the probability height
  const precipPts = entries.map((e, i) => ({
    x: xAt(i),
    y: PRECIP_B - ((e.pop_pct ?? 0) / 100) * (PRECIP_B - PRECIP_T),
  }));
  const precipLinePath = bezierPath(precipPts);
  const precipAreaPath = `${precipLinePath} L ${VW} ${PRECIP_B} L 0 ${PRECIP_B} Z`;

  const labelIdx = [0, 6, 12, 18, 23].filter(i => i < entries.length);
  const hasPrecip = entries.some(e => (e.pop_pct ?? 0) > 0);

  return (
    <div>
      <SectionLabel>Next 24 Hours</SectionLabel>
      <div className="mt-4">
        <svg
          viewBox={`0 0 ${VW} ${VH}`}
          preserveAspectRatio="none"
          className="w-full"
          style={{ height: hasPrecip ? 145 : 105 }}
          aria-hidden="true"
        >
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

          {/* Temp area fill */}
          <path d={areaPath} fill="url(#hTempGrad)" />

          {/* Temp line */}
          <path d={linePath} fill="none" stroke="rgba(103,232,249,0.85)"
            strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />

          {/* Dots + labels at key positions */}
          {labelIdx.map(i => {
            const e = entries[i];
            const x = xAt(i);
            const y = yAt(e.temperature_f ?? (minT + range / 2));
            const anchor = i === 0 ? "start" : i === entries.length - 1 ? "end" : "middle";
            return (
              <g key={i}>
                <circle cx={x} cy={y} r={2.5} fill="rgba(103,232,249,1)" />
                <text x={x} y={y - 6} textAnchor={anchor} fontSize={9.5}
                  fontWeight="600" fill="rgba(255,255,255,0.82)">
                  {e.temperature_f != null ? `${e.temperature_f}°` : "--"}
                </text>
              </g>
            );
          })}

          {/* Precip probability area curve */}
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
        </svg>

        {/* Icon + time labels row — absolutely aligned with SVG x positions */}
        <div className="relative mt-2 h-12">
          {labelIdx.map(i => {
            const e = entries[i];
            const pct = (i / (entries.length - 1)) * 100;
            const align = i === 0 ? "items-start" : i === entries.length - 1 ? "items-end" : "items-center";
            const translate = i === 0 ? "" : i === entries.length - 1 ? "-translate-x-full" : "-translate-x-1/2";
            return (
              <div
                key={i}
                className={`absolute top-0 flex flex-col gap-0.5 ${align} ${translate}`}
                style={{ left: `${pct}%` }}
              >
                <WeatherIcon code={e.weather_code} className="h-4 w-4 text-cyan-200/65" />
                <span className="text-[10px] text-white/40">{formatHour(e.time)}</span>
                {(e.pop_pct ?? 0) > 0 && (
                  <span className="text-[10px] text-cyan-300/65">{e.pop_pct}%</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Daily Mini Chart ──────────────────────────────────────────────────

function DailyMiniChart({ daily }: { daily: DailyEntry[] }) {
  const entries = daily.slice(0, 7);
  if (entries.length === 0) return null;

  const lows  = entries.map(e => e.low_f  ?? null).filter((v): v is number => v !== null);
  const highs = entries.map(e => e.high_f ?? null).filter((v): v is number => v !== null);
  if (!lows.length || !highs.length) return null;

  const globalMin = Math.min(...lows);
  const globalMax = Math.max(...highs);
  const span = globalMax - globalMin || 1;

  return (
    <div className="border-t border-white/8 pt-4">
      <SectionLabel>7-Day Outlook</SectionLabel>
      <div className="mt-3 space-y-2.5">
        {entries.map((entry, i) => {
          const low  = entry.low_f  ?? globalMin;
          const high = entry.high_f ?? globalMax;
          const leftPct  = ((low  - globalMin) / span) * 100;
          const widthPct = ((high - low) / span) * 100;
          const pop = entry.pop_pct ?? 0;
          return (
            <div key={i} className="flex items-center gap-3">
              <div className="w-9 flex-none text-[10px] font-semibold uppercase tracking-[0.14em] text-white/45">
                {formatDayLabel(entry.date, i)}
              </div>
              <WeatherIcon code={entry.icon} className="h-3.5 w-3.5 flex-none text-cyan-200/60" />
              <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
                <div
                  className="absolute inset-y-0 rounded-full bg-gradient-to-r from-sky-400/60 to-cyan-300/80"
                  style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                />
              </div>
              <div className="flex w-16 flex-none justify-end gap-1.5 text-xs">
                <span className="font-medium text-white/80">{high}°</span>
                <span className="text-white/30">{low}°</span>
              </div>
              <div className="w-7 flex-none text-right text-[10px] text-emerald-400/70">
                {pop > 0 ? `${pop}%` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Current Conditions ────────────────────────────────────────────────

function CurrentConditionsCard({ current, freshness, attribution }: {
  current: CurrentData;
  freshness: ForecastPayload["freshness"]["current"];
  attribution: string | null;
}) {
  const chip = freshnessChip(freshness.state, freshness.age_minutes);

  return (
    <div className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
      <SectionLabel>Current Conditions</SectionLabel>

      <div className="mt-4 flex items-start gap-4">
        <WeatherIcon code={current.icon} className="mt-0.5 h-10 w-10 flex-none text-cyan-200/80" />
        <div className="min-w-0">
          <div className="text-5xl font-semibold tracking-tight text-white leading-none">
            {current.temperature_f ?? "--"}°
          </div>
          <div className="mt-1.5 text-base text-white/75">
            {current.short_text ?? ""}
          </div>
        </div>
      </div>

      <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="whitespace-nowrap text-white/45">Dew Point</dt>
        <dd className="text-right text-white/80">{current.dewpoint_f != null ? `${current.dewpoint_f}°` : "--"}</dd>
        <dt className="whitespace-nowrap text-white/45">Humidity</dt>
        <dd className="text-right text-white/80">{current.humidity_pct != null ? `${current.humidity_pct}%` : "--"}</dd>
        <dt className="whitespace-nowrap text-white/45">Wind</dt>
        <dd className="text-right text-white/80 whitespace-nowrap">
          {degreesToCardinal(current.wind_dir_deg)} {current.wind_speed_mph ?? "--"} mph{current.wind_gust_mph ? ` · G${current.wind_gust_mph}` : ""}
        </dd>
        {current.pressure_mb != null && (
          <>
            <dt className="whitespace-nowrap text-white/45">Pressure</dt>
            <dd className="text-right text-white/80">{current.pressure_mb} mb</dd>
          </>
        )}
        {current.visibility_mi != null && (
          <>
            <dt className="whitespace-nowrap text-white/45">Visibility</dt>
            <dd className="text-right text-white/80">{current.visibility_mi} mi</dd>
          </>
        )}
      </dl>

      <div className="mt-4 flex items-center gap-3 border-t border-white/8 pt-3 text-xs">
        {current.station?.name ? (
          <span className="text-white/35 truncate">{current.station.name}{current.station.distance_km != null ? ` · ${current.station.distance_km} km` : ""}</span>
        ) : attribution ? (
          <span className="text-white/35">{attribution}</span>
        ) : null}
        <span className={`ml-auto flex-none font-medium ${chip.color}`}>{chip.label}</span>
      </div>
    </div>
  );
}

// ── Daily Forecast ────────────────────────────────────────────────────

function DailyForecast({ daily }: { daily: DailyEntry[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(i: number) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  return (
    <div className="divide-y divide-white/[0.06] rounded-[1.4rem] border border-white/8 overflow-hidden">
      {daily.slice(0, 7).map((entry, i) => {
        const isOpen = expanded.has(i);
        const hasDetail = entry.wind_speed_mph != null || (entry.qpf_in != null && entry.qpf_in > 0) || (entry.snow_in != null && entry.snow_in > 0);
        return (
          <div key={i}>
            <button
              type="button"
              onClick={() => hasDetail && toggle(i)}
              className={`flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors ${hasDetail ? "hover:bg-white/[0.03]" : ""}`}
            >
              <div className="w-10 text-xs font-semibold uppercase tracking-[0.14em] text-white/55 flex-none">
                {formatDayLabel(entry.date, i)}
              </div>
              <WeatherIcon code={entry.icon} className="h-5 w-5 flex-none text-cyan-200/75" />
              <div className="flex items-baseline gap-2 min-w-[5rem]">
                <span className="text-sm font-semibold text-white">{entry.high_f ?? "--"}°</span>
                <span className="text-sm text-white/35">{entry.low_f ?? "--"}°</span>
              </div>
              <div className="flex-1 text-sm text-white/60 truncate">{entry.short_text ?? ""}</div>
              <div className="flex-none text-sm text-cyan-300/65 min-w-[2.5rem] text-right">
                {entry.pop_pct != null && entry.pop_pct > 0 ? `${entry.pop_pct}%` : ""}
              </div>
              {hasDetail && (
                <div className="flex-none text-white/30">
                  {isOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                </div>
              )}
            </button>
            {isOpen && hasDetail && (
              <div className="border-t border-white/[0.06] bg-white/[0.02] px-4 py-3">
                <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-xs text-white/55">
                  {entry.wind_speed_mph != null && (
                    <span>Wind <span className="text-white/75">{entry.wind_speed_mph} mph{entry.wind_gust_mph ? ` · G${entry.wind_gust_mph}` : ""}</span></span>
                  )}
                  {entry.qpf_in != null && entry.qpf_in > 0 && (
                    <span>Rain <span className="text-white/75">{entry.qpf_in}"</span></span>
                  )}
                  {entry.snow_in != null && entry.snow_in > 0 && (
                    <span>Snow <span className="text-white/75">{entry.snow_in}"</span></span>
                  )}
                  {entry.pop_pct != null && entry.pop_pct > 0 && (
                    <span>Precip <span className="text-cyan-300/80">{entry.pop_pct}%</span></span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
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
          <div key={alert.id ?? i} className={`rounded-[1.2rem] border ${s.border} ${s.bg} overflow-hidden`}>
            <button type="button" onClick={() => toggle(i)} className="flex w-full items-start gap-3 p-4 text-left">
              <AlertTriangle className={`mt-0.5 h-4 w-4 flex-none ${s.text}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`text-sm font-semibold ${s.text}`}>{alert.event ?? "Alert"}</span>
                  {alert.severity && <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${s.badge}`}>{alert.severity}</span>}
                </div>
                {alert.headline && <p className="mt-1 text-sm text-white/70">{alert.headline}</p>}
                {alert.areas.length > 0 && <p className="mt-0.5 text-xs text-white/38">{alert.areas.slice(0, 3).join(" · ")}</p>}
              </div>
              <div className="flex-none text-white/35">{isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</div>
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

// ── NWS Text Forecast ─────────────────────────────────────────────────

function TextForecastSection({ data }: { data: NonNullable<ForecastPayload["official_text_forecast"]> }) {
  const [showAll, setShowAll] = useState(false);
  if (!data.periods.length) return null;
  const visible = showAll ? data.periods : data.periods.slice(0, 6);
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <SectionLabel>NWS Forecast Periods</SectionLabel>
        {data.generated_at && <span className="text-xs text-white/30">Generated {formatObservedAt(data.generated_at)}</span>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((period, i) => (
          <div key={i} className="rounded-[1.2rem] border border-white/8 bg-white/[0.02] p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-white/45">{period.name ?? (period.is_daytime ? "Day" : "Night")}</div>
                <div className="mt-2 text-sm leading-6 text-white/78">{period.short_text ?? ""}</div>
                {period.wind_text && <div className="mt-1 text-xs text-white/40">Wind: {period.wind_text}</div>}
              </div>
              <div className="flex-none text-right">
                <div className="text-2xl font-semibold tracking-tight text-white">{period.temperature_f ?? "--"}°</div>
                <div className="mt-0.5 text-[10px] uppercase tracking-[0.14em] text-white/30">{period.is_daytime ? "High" : "Low"}</div>
              </div>
            </div>
            {period.detailed_text && (
              <p className="mt-3 border-t border-white/8 pt-3 text-xs leading-5.5 text-white/45">{period.detailed_text}</p>
            )}
          </div>
        ))}
      </div>
      {data.periods.length > 6 && (
        <button type="button" onClick={() => setShowAll(v => !v)} className="mt-4 flex items-center gap-1.5 text-xs text-white/40 transition hover:text-white/60">
          {showAll ? <><ChevronUp className="h-3.5 w-3.5" /> Show fewer periods</> : <><ChevronDown className="h-3.5 w-3.5" /> Show all {data.periods.length} periods</>}
        </button>
      )}
    </div>
  );
}

// ── AFD Section ───────────────────────────────────────────────────────

function AfdSection({ afd }: { afd: NonNullable<ForecastPayload["afd"]> }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <SectionLabel>Area Forecast Discussion · {afd.office}</SectionLabel>
        {afd.issued_at && <span className="text-xs text-white/30">{formatIssuedAt(afd.issued_at)}</span>}
      </div>
      <div className="rounded-[1.2rem] border border-white/8 bg-white/[0.02] overflow-hidden">
        <button type="button" onClick={() => setOpen(v => !v)} className="flex w-full items-center justify-between px-4 py-3.5 text-left">
          <span className="text-sm text-white/55">{open ? "Collapse discussion" : "Read Area Forecast Discussion"}</span>
          {open ? <ChevronUp className="h-4 w-4 flex-none text-white/35" /> : <ChevronDown className="h-4 w-4 flex-none text-white/35" />}
        </button>
        {open && afd.text && (
          <div className="border-t border-white/8 px-4 pb-5 pt-4">
            <pre className="max-h-96 overflow-y-auto font-mono text-xs leading-6 text-white/55 whitespace-pre-wrap break-words">{afd.text}</pre>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function Forecast() {
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<LocationResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [forecast, setForecast] = useState<ForecastPayload | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const searchContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function onOut(e: MouseEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", onOut);
    return () => document.removeEventListener("mousedown", onOut);
  }, []);

  // Handle URL param ?q= on mount
  useEffect(() => {
    const q = searchParams.get("q");
    if (q) { setQuery(q); void loadByQuery(q); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced live search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();

    // Don't re-search if query matches what's already loaded
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

  async function loadByCoords(lat: number, lon: number, preferredName?: string) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    setIsLoading(true); setError(null); setForecast(null); setShowDropdown(false);
    try {
      const res = await fetch(`${API_V4_BASE}/forecast-page?lat=${lat}&lon=${lon}`, { signal: ctrl.signal });
      if (!res.ok) throw new Error("Forecast unavailable for this location.");
      const data = (await res.json()) as ForecastPayload;
      // Use preferred name if the API returns coords
      const name = isCoordString(data.location.display_name) && preferredName
        ? preferredName
        : data.location.display_name;
      setForecast({ ...data, location: { ...data.location, display_name: name } });
      setQuery(name);
      setPendingName(name);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable.");
    } finally { setIsLoading(false); }
  }

  async function loadByQuery(q: string) {
    if (loadAbortRef.current) loadAbortRef.current.abort();
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    setIsLoading(true); setError(null); setForecast(null); setShowDropdown(false);
    try {
      const res = await fetch(`${API_V4_BASE}/forecast-page/by-query?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? "Forecast unavailable. Try selecting a location from the dropdown.");
      }
      const data = (await res.json()) as ForecastPayload;
      const name = isCoordString(data.location.display_name) ? q : data.location.display_name;
      setForecast({ ...data, location: { ...data.location, display_name: name } });
      setQuery(name);
      setPendingName(name);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Forecast guidance is temporarily unavailable. Try selecting from the dropdown suggestions.");
    } finally { setIsLoading(false); }
  }

  function selectLocation(loc: LocationResult) {
    setPendingName(loc.display_name);
    setQuery(loc.display_name);
    setShowDropdown(false);
    setSearchResults([]);
    void loadByCoords(loc.latitude, loc.longitude, loc.display_name);
  }

  function clearSearch() {
    setQuery(""); setPendingName(null); setForecast(null); setError(null);
    setSearchResults([]); setShowDropdown(false);
    if (loadAbortRef.current) loadAbortRef.current.abort();
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  const isLoaded = forecast !== null;
  const freshness = forecast?.freshness.current ?? null;
  const obsLabel = freshness?.observed_at ? formatObservedAt(freshness.observed_at) : null;

  // ── Search box (shared between empty + loaded states) ──────────────

  const searchBox = (
    <div ref={searchContainerRef} className="relative">
      <div className={`rounded-[1.6rem] border border-white/10 bg-slate-950/35 backdrop-blur-md ${isLoaded ? "p-3" : "p-4 shadow-[0_24px_70px_rgba(0,0,0,0.28)]"}`}>
        <label className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5">
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
            placeholder={isLoaded ? "Search another location…" : "Search city, state, or zip code"}
            className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/35"
            autoComplete="off"
            spellCheck={false}
          />
          {isSearching ? (
            <div className="h-3 w-3 flex-none animate-spin rounded-full border border-cyan-300/25 border-t-cyan-300" />
          ) : (isLoaded || query) ? (
            <button type="button" onClick={clearSearch} className="flex-none rounded-full p-0.5 text-white/30 transition hover:text-white/60">
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </label>

        {showDropdown && searchResults.length > 0 && (
          <div className="mt-2 space-y-1">
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
    </div>
  );

  // ── LOADED STATE ───────────────────────────────────────────────────

  if (isLoaded) {
    const f = forecast;
    return (
      <div className="-mx-5 -mt-12 md:-mx-8 md:-mt-16">
        {/* Location bar */}
        <div className="fixed inset-x-0 top-16 z-[55] border-b border-white/8 bg-[#07111f]/90 px-5 py-3 backdrop-blur-md md:px-8">
          <div className="mx-auto flex max-w-6xl items-center gap-4">
            <button
              type="button"
              onClick={clearSearch}
              className="flex items-center gap-1.5 text-xs text-white/40 transition hover:text-white/65"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Search
            </button>
            <div className="flex-1 min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h1 className="text-base font-semibold tracking-tight text-white truncate">
                  {f.location.display_name}
                </h1>
                {obsLabel && (
                  <span className={`text-xs ${freshnessChip(freshness?.state ?? null, freshness?.age_minutes ?? null).color}`}>
                    {freshnessChip(freshness?.state ?? null, freshness?.age_minutes ?? null).label}
                  </span>
                )}
                <span className="text-xs text-white/25 hidden sm:inline">
                  {f.location.latitude.toFixed(4)}, {f.location.longitude.toFixed(4)}
                </span>
              </div>
            </div>
            <Link
              to={viewerHref(f.location.latitude, f.location.longitude)}
              className="flex-none inline-flex items-center gap-1.5 rounded-lg border border-cyan-200/30 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3 py-1.5 text-xs font-semibold text-slate-950 shadow-[0_8px_24px_rgba(35,196,255,0.14)] transition hover:-translate-y-px hover:brightness-105"
            >
              Open In Viewer <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>

        {/* Main content */}
        <div className="bg-[#07111f] px-5 pb-8 pt-[7.5rem] md:px-8 md:pb-10">
          <div className="mx-auto max-w-6xl space-y-8">

            {/* Search + current + hourly row */}
            <div className="grid gap-5 lg:grid-cols-[1fr_1.5fr]">
              <div className="space-y-4">
                {searchBox}
                <CurrentConditionsCard
                  current={f.current}
                  freshness={f.freshness.current}
                  attribution={f.attribution.current}
                />
              </div>
              <div className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] p-5">
                <HourlyChart hourly={f.hourly} />
                <DailyMiniChart daily={f.daily} />
              </div>
            </div>

            {/* Alerts */}
            {f.alerts.length > 0 && (
              <div>
                <div className="mb-3 flex items-center gap-3">
                  <SectionLabel>Active Alerts</SectionLabel>
                  <span className="rounded-full border border-rose-300/20 bg-rose-300/10 px-2 py-0.5 text-[10px] font-semibold text-rose-200">
                    {f.alerts.length}
                  </span>
                </div>
                <AlertsBanner alerts={f.alerts} />
              </div>
            )}

            {/* 7-day forecast + viewer CTA */}
            <div>
              <div className="mb-3 flex items-center justify-between">
                <SectionLabel>7-Day Forecast</SectionLabel>
                {f.attribution.daily && <span className="text-xs text-white/25">Source: {f.attribution.daily}</span>}
              </div>
              <DailyForecast daily={f.daily} />

              <div className="mt-5 flex items-center justify-between gap-4 rounded-[1.2rem] border border-cyan-300/12 bg-cyan-300/[0.04] px-4 py-3.5">
                <div className="text-sm text-white/65">
                  Want to dig deeper? Open this location in the interactive map viewer.
                </div>
                <Link
                  to={viewerHref(f.location.latitude, f.location.longitude)}
                  className="flex-none inline-flex items-center gap-2 rounded-xl border border-cyan-200/30 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-4 py-2 text-sm font-semibold text-slate-950 shadow-[0_8px_24px_rgba(35,196,255,0.12)] transition hover:-translate-y-px hover:brightness-105"
                >
                  Open In Viewer <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>

            {/* NWS text forecast */}
            {f.official_text_forecast && (
              <div>
                <TextForecastSection data={f.official_text_forecast} />
              </div>
            )}

            {/* AFD */}
            {f.afd && (
              <div>
                <AfdSection afd={f.afd} />
              </div>
            )}

            <div className="h-4" />
          </div>
        </div>
      </div>
    );
  }

  // ── EMPTY STATE ────────────────────────────────────────────────────

  return (
    <div className="-mx-5 -mt-12 space-y-0 md:-mx-8 md:-mt-16">
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

        <div className="relative mx-auto grid min-h-[calc(100svh-10rem)] max-w-6xl items-center gap-12 py-10 lg:grid-cols-[0.9fr_1.1fr]">
          <div className="max-w-2xl">
            <SectionEyebrow>Forecast Preview</SectionEyebrow>
            <h1 className="mt-8 text-balance text-5xl font-semibold tracking-[-0.04em] text-white md:text-7xl md:leading-[0.98]">
              Local weather,
              <br />
              <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic text-cyan-200">
                clearly briefed.
              </span>
            </h1>
            <p className="mt-6 max-w-md text-base leading-8 text-white/65">
              Current conditions, 24-hour hourly, and a 7-day outlook — with a direct handoff to the viewer for deeper analysis.
            </p>
            <div className="mt-8">
              {searchBox}
            </div>
            {error && (
              <div className="mt-4 rounded-2xl border border-rose-300/18 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">
                {error}
                <div className="mt-1 text-xs text-rose-200/70">Try selecting a location from the search dropdown.</div>
              </div>
            )}
          </div>

          <div className="rounded-[2rem] border border-white/10 bg-slate-950/35 p-6 shadow-[0_28px_90px_rgba(0,0,0,0.26)] backdrop-blur-md">
            {isLoading ? (
              <div className="space-y-4 animate-pulse">
                <div className="h-4 w-32 rounded-lg bg-white/8" />
                <div className="h-8 w-48 rounded-xl bg-white/8" />
                <div className="mt-6 grid gap-4 sm:grid-cols-2">
                  <div className="h-40 rounded-[1.4rem] bg-white/[0.04]" />
                  <div className="h-40 rounded-[1.4rem] bg-white/[0.04]" />
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-white/25">
                  <Search className="h-6 w-6" />
                </div>
                <p className="mt-5 text-sm font-medium text-white/55">Search a location to get started</p>
                <p className="mt-2 max-w-xs text-xs leading-6 text-white/32">
                  Type a city name, state, or zip code in the search box. U.S. locations include NWS data; international locations use Open-Meteo.
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {["Denver, CO", "Chicago, IL", "Miami, FL", "Seattle, WA"].map(place => (
                    <button
                      key={place}
                      type="button"
                      onClick={() => { setPendingName(place); setQuery(place); void loadByQuery(place); }}
                      className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-white/55 transition hover:border-white/18 hover:bg-white/[0.05] hover:text-white/75"
                    >
                      {place}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Feature cards — only shown in empty state */}
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
