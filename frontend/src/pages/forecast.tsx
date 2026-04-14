import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CloudMoon, MapPinned, Search, Wind } from "lucide-react";

import { API_V4_BASE, MAP_VIEW_DEFAULTS } from "@/lib/config";
import { buildPermalinkSearch } from "@/lib/permalink";

type AnchorOption = {
  id: string;
  city: string;
  state: string;
  st: string;
  lon: number;
  lat: number;
};

type WeatherBundle = {
  city: string;
  state: string;
  st: string;
  observation: {
    stationName?: string | null;
    observedAt?: string | null;
    tempF?: number | null;
    dewpointF?: number | null;
    relativeHumidity?: number | null;
    windDirection?: string | null;
    windSpeedMph?: number | null;
    windGustMph?: number | null;
    textDescription?: string | null;
  } | null;
  forecast: {
    generatedAt?: string | null;
    periods: Array<{
      number: number;
      name: string;
      isDaytime: boolean;
      tempF?: number | null;
      windSpeed?: string | null;
      windDirection?: string | null;
      shortForecast?: string | null;
      detailedForecast?: string | null;
      precipProbability?: number | null;
    }>;
  } | null;
  meta?: {
    anchorId?: string;
  };
};

function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

function formatObservedAt(value?: string | null): string {
  if (!value) {
    return "Current observation";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Current observation";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function viewerHrefForAnchor(anchor: AnchorOption | null): string {
  if (!anchor) {
    return "/viewer";
  }
  return `/viewer${buildPermalinkSearch({
    region: MAP_VIEW_DEFAULTS.region,
    lat: anchor.lat,
    lon: anchor.lon,
    z: 7,
  })}`;
}

export default function Forecast() {
  const [anchors, setAnchors] = useState<AnchorOption[]>([]);
  const [query, setQuery] = useState("Chicago");
  const [selectedAnchorId, setSelectedAnchorId] = useState<string>("IL_1");
  const [bundle, setBundle] = useState<WeatherBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadAnchors() {
      try {
        const response = await fetch("/data/anchors_conus.geojson");
        if (!response.ok) {
          throw new Error(`Anchor load failed (${response.status})`);
        }
        const geojson = await response.json();
        const nextAnchors = (geojson.features ?? [])
          .map((feature: any) => ({
            id: String(feature.id ?? ""),
            city: String(feature.properties?.city ?? ""),
            state: String(feature.properties?.state ?? ""),
            st: String(feature.properties?.st ?? ""),
            lon: Number(feature.geometry?.coordinates?.[0]),
            lat: Number(feature.geometry?.coordinates?.[1]),
          }))
          .filter((anchor: AnchorOption) => anchor.id && anchor.city && Number.isFinite(anchor.lon) && Number.isFinite(anchor.lat));

        if (cancelled) {
          return;
        }
        setAnchors(nextAnchors);
        if (!nextAnchors.some((anchor: AnchorOption) => anchor.id === "IL_1") && nextAnchors[0]) {
          setSelectedAnchorId(nextAnchors[0].id);
          setQuery(`${nextAnchors[0].city}, ${nextAnchors[0].st}`);
        }
      } catch {
        if (!cancelled) {
          setError("Anchor search is temporarily unavailable.");
        }
      }
    }

    void loadAnchors();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredAnchors = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) {
      return anchors.slice(0, 8);
    }
    return anchors
      .filter((anchor) => {
        const haystack = `${anchor.city} ${anchor.state} ${anchor.st}`.toLowerCase();
        return haystack.includes(normalizedQuery);
      })
      .slice(0, 8);
  }, [anchors, query]);

  const selectedAnchor = useMemo(
    () => anchors.find((anchor) => anchor.id === selectedAnchorId) ?? null,
    [anchors, selectedAnchorId]
  );

  useEffect(() => {
    if (!selectedAnchorId) {
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${API_V4_BASE}/anchors/${selectedAnchorId}/weather`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Forecast request failed (${response.status})`);
        }
        return (await response.json()) as WeatherBundle;
      })
      .then((payload) => {
        setBundle(payload);
        if (selectedAnchor) {
          setQuery(`${selectedAnchor.city}, ${selectedAnchor.st}`);
        }
      })
      .catch((fetchError: unknown) => {
        if ((fetchError as any)?.name === "AbortError") {
          return;
        }
        setBundle(null);
        setError("Forecast guidance is temporarily unavailable for this location.");
      })
      .finally(() => {
        setLoading(false);
      });

    return () => controller.abort();
  }, [selectedAnchorId, selectedAnchor]);

  const headlineLocation = bundle ? `${bundle.city}, ${bundle.st}` : selectedAnchor ? `${selectedAnchor.city}, ${selectedAnchor.st}` : "Search a location";
  const currentObservation = bundle?.observation;
  const forecastPeriods = bundle?.forecast?.periods ?? [];
  const leadPeriod = forecastPeriods[0] ?? null;
  const secondaryPeriods = forecastPeriods.slice(1, 4);

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

        <div className="relative mx-auto grid min-h-[calc(100svh-10rem)] max-w-6xl items-center gap-12 py-10 lg:grid-cols-[0.95fr_1.05fr]">
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
              Search a place, pull up current conditions plus short-range forecast context, and hand the location straight off to the viewer when you want deeper analysis.
            </p>

            <div className="mt-10 rounded-[1.7rem] border border-white/10 bg-slate-950/35 p-4 shadow-[0_24px_70px_rgba(0,0,0,0.28)] backdrop-blur-md">
              <label className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                <Search className="h-4 w-4 text-cyan-200/85" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search city or state"
                  className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/35"
                />
              </label>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {filteredAnchors.map((anchor) => {
                  const active = anchor.id === selectedAnchorId;
                  return (
                    <button
                      key={anchor.id}
                      type="button"
                      onClick={() => setSelectedAnchorId(anchor.id)}
                      className={[
                        "rounded-2xl border px-4 py-3 text-left transition duration-150",
                        active
                          ? "border-cyan-300/24 bg-cyan-300/10 text-white"
                          : "border-white/8 bg-white/[0.03] text-white/72 hover:border-white/15 hover:bg-white/[0.05]",
                      ].join(" ")}
                    >
                      <div className="text-sm font-semibold">{anchor.city}</div>
                      <div className="mt-1 text-xs uppercase tracking-[0.18em] text-white/44">{anchor.state}</div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="rounded-[2rem] border border-white/10 bg-slate-950/35 p-5 shadow-[0_28px_90px_rgba(0,0,0,0.26)] backdrop-blur-md">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/8 pb-4">
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.26em] text-cyan-200/70">Location Briefing</div>
                <div className="mt-3 text-3xl font-semibold tracking-tight text-white">{headlineLocation}</div>
                <div className="mt-2 text-sm text-white/55">
                  {currentObservation ? formatObservedAt(currentObservation.observedAt) : "Select a city to load current observations and forecast."}
                </div>
              </div>
              <Link
                to={viewerHrefForAnchor(selectedAnchor)}
                className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-4 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
              >
                Open In Viewer
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>

            {error ? (
              <div className="mt-6 rounded-2xl border border-rose-300/18 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">
                {error}
              </div>
            ) : null}

            <div className="mt-6 grid gap-4 lg:grid-cols-[0.78fr_1.22fr]">
              <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-5">
                <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">Current Conditions</div>
                {loading ? (
                  <div className="mt-5 text-sm text-white/55">Loading current conditions…</div>
                ) : (
                  <>
                    <div className="mt-5 text-5xl font-semibold tracking-tight text-white">
                      {currentObservation?.tempF ?? "--"}°
                    </div>
                    <div className="mt-3 text-base text-cyan-100/88">
                      {currentObservation?.textDescription ?? "Awaiting current observation"}
                    </div>
                    <div className="mt-6 space-y-3 text-sm text-white/62">
                      <div className="flex items-center justify-between gap-4">
                        <span>Dew Point</span>
                        <span>{currentObservation?.dewpointF ?? "--"}°F</span>
                      </div>
                      <div className="flex items-center justify-between gap-4">
                        <span>Humidity</span>
                        <span>{currentObservation?.relativeHumidity ?? "--"}%</span>
                      </div>
                      <div className="flex items-center justify-between gap-4">
                        <span>Wind</span>
                        <span>
                          {currentObservation?.windDirection ?? "--"} {currentObservation?.windSpeedMph ?? "--"} mph
                        </span>
                      </div>
                    </div>
                  </>
                )}
              </div>

              <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-5">
                <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">Next Period</div>
                {loading ? (
                  <div className="mt-5 text-sm text-white/55">Loading forecast guidance…</div>
                ) : leadPeriod ? (
                  <>
                    <div className="mt-5 flex items-start justify-between gap-4">
                      <div>
                        <div className="text-2xl font-semibold tracking-tight text-white">{leadPeriod.name}</div>
                        <div className="mt-2 text-sm text-white/60">{leadPeriod.shortForecast ?? "Forecast summary unavailable"}</div>
                      </div>
                      <div className="text-right">
                        <div className="text-3xl font-semibold tracking-tight text-cyan-100">{leadPeriod.tempF ?? "--"}°</div>
                        <div className="mt-1 text-xs uppercase tracking-[0.18em] text-white/44">
                          {leadPeriod.isDaytime ? "Day" : "Night"}
                        </div>
                      </div>
                    </div>
                    <div className="mt-6 grid gap-3 sm:grid-cols-3">
                      <div className="rounded-2xl border border-white/8 bg-slate-950/28 px-4 py-3">
                        <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Wind</div>
                        <div className="mt-2 text-sm text-white/76">{leadPeriod.windDirection ?? "--"} {leadPeriod.windSpeed ?? "--"}</div>
                      </div>
                      <div className="rounded-2xl border border-white/8 bg-slate-950/28 px-4 py-3">
                        <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Precip</div>
                        <div className="mt-2 text-sm text-white/76">{leadPeriod.precipProbability ?? 0}% chance</div>
                      </div>
                      <div className="rounded-2xl border border-white/8 bg-slate-950/28 px-4 py-3">
                        <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Generated</div>
                        <div className="mt-2 text-sm text-white/76">{bundle?.forecast?.generatedAt ? formatObservedAt(bundle.forecast.generatedAt) : "NWS"}</div>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="mt-5 text-sm text-white/55">Select a city to load the forecast preview.</div>
                )}
              </div>
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              {secondaryPeriods.map((period) => (
                <div key={period.number} className="rounded-[1.4rem] border border-white/8 bg-white/[0.03] px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-white/42">{period.name}</div>
                  <div className="mt-3 text-2xl font-semibold tracking-tight text-white">{period.tempF ?? "--"}°</div>
                  <div className="mt-2 text-sm leading-6 text-white/62">{period.shortForecast ?? "Forecast summary unavailable"}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-white/8 bg-[#0b1527] px-5 py-16 md:px-8 md:py-20">
        <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-3">
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <MapPinned className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">Location-first briefing</h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              Search a city, get current observations first, then move into the short forecast without dropping into a generic consumer-weather layout.
            </p>
          </div>
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <CloudMoon className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">A quieter sibling to Viewer</h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              This page is deliberately narrower in scope than the viewer. It gives you the briefing first, then hands you off to deeper map analysis when needed.
            </p>
          </div>
          <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6">
            <Wind className="h-5 w-5 text-cyan-200" />
            <h2 className="mt-5 text-2xl font-semibold tracking-tight text-white">Built to grow later</h2>
            <p className="mt-3 text-sm leading-7 text-white/62">
              This pre-beta forecast surface is intentionally small now so it can expand into richer model and location workflows after the viewer redesign lands.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
