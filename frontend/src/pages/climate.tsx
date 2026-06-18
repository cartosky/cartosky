import { useEffect, useRef, useState } from "react";

import { API_V4_BASE } from "@/lib/config";
import ClimateIndexWidget from "@/components/ClimateIndexWidget";

// ── Types ─────────────────────────────────────────────────────────────

type ClimateStateEntry = {
  value: number | null;
  state: string | null;
  valid_date: string | null;
};

type ClimateStateMJO = {
  phase: number | null;
  amplitude: number | null;
  state: string | null;
  valid_date: string | null;
};

type ClimateStatePayload = {
  enso: ClimateStateEntry | null;
  mjo: ClimateStateMJO | null;
  ao: ClimateStateEntry | null;
  nao: ClimateStateEntry | null;
  pna: ClimateStateEntry | null;
  valid_date: string | null;
};

// ── Section config ────────────────────────────────────────────────────

const SECTIONS = [
  { id: "sst",          label: "Sea Surface Temps" },
  { id: "mjo",          label: "MJO Forecast"      },
  { id: "enso",         label: "ENSO · Niño 3.4"   },
  { id: "oscillations", label: "AO / NAO / PNA"    },
  { id: "drought",      label: "Drought Monitor"   },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

// ── Image URL registry ────────────────────────────────────────────────

function proxy(url: string): string {
  return `${API_V4_BASE}/climate/image-proxy?url=${encodeURIComponent(url)}`;
}

const CORAL_BASE = "https://coralreefwatch.noaa.gov/data_current/5km/v3.1_op/daily/png";
const CPC_BASE   = "https://www.cpc.ncep.noaa.gov/products";

const IMG = {
  sstAnomaly:    `${CORAL_BASE}/ct5km_ssta_v3.1_global_current.png`,
  sstTrend7d:    `${CORAL_BASE}/ct5km_sst-trend-7d_v3.1_global_current.png`,
  mjoEcmf:       `${CPC_BASE}/precip/mjo/img/ECMF.png`,
  mjoEmon:       `${CPC_BASE}/precip/mjo/img/EMON.png`,
  ensoCfs:       `${CPC_BASE}/CFSv2/imagesInd3/nino34Mon.gif`,
  ensoCpcProb:   `${CPC_BASE}/analysis_monitoring/enso_advisory/figure07.gif`,
  ninoTidbits:   "https://www.tropicaltidbits.com/analysis/ocean/nino34.png",
  ao:            `${CPC_BASE}/precip/CWlink/daily_ao_index/ao.gefs.sprd2.png`,
  nao:           `${CPC_BASE}/precip/CWlink/pna/nao.gefs.sprd2.png`,
  pna:           `${CPC_BASE}/precip/CWlink/pna/pna.gefs.sprd2.png`,
  droughtCurrent:"https://droughtmonitor.unl.edu/data/png/current/current_usdm.png",
  droughtChange: "https://droughtmonitor.unl.edu/data/chng/png/current/current_conus_chng_4W.png",
} as const;

// ── Helpers ───────────────────────────────────────────────────────────

function formatValidDateUTC(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return (
    new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
      hour12: false,
    }).format(d) + " UTC"
  );
}

function getOldestValidDate(payload: ClimateStatePayload): string | null {
  const dates: string[] = [];
  if (payload.valid_date)      dates.push(payload.valid_date);
  if (payload.enso?.valid_date) dates.push(payload.enso.valid_date);
  if (payload.mjo?.valid_date)  dates.push(payload.mjo.valid_date);
  if (payload.ao?.valid_date)   dates.push(payload.ao.valid_date);
  if (payload.nao?.valid_date)  dates.push(payload.nao.valid_date);
  if (payload.pna?.valid_date)  dates.push(payload.pna.valid_date);
  if (!dates.length) return null;
  return dates.reduce((oldest, d) =>
    new Date(d).getTime() < new Date(oldest).getTime() ? d : oldest,
  );
}

function isStale(isoDate: string | null): boolean {
  if (!isoDate) return false;
  const d = new Date(isoDate);
  if (isNaN(d.getTime())) return true;
  return d.getTime() < Date.now() - 2 * 24 * 60 * 60 * 1000;
}

function ensoBadgeStyle(state: string | null): string {
  if (!state) return "bg-white/[0.06] text-white/45 border-white/10";
  const s = state.toLowerCase();
  if (s.includes("el ni") || s.includes("warm"))
    return "bg-orange-400/[0.12] text-orange-300 border-orange-400/20";
  if (s.includes("la ni") || s.includes("cool"))
    return "bg-blue-400/[0.12] text-blue-300 border-blue-400/20";
  return "bg-emerald-400/[0.12] text-emerald-300 border-emerald-400/20";
}

function oscillationBadgeStyle(state: string | null): string {
  if (!state) return "bg-white/[0.06] text-white/45 border-white/10";
  const s = state.toLowerCase();
  if (s.includes("positive") || s === "pos")
    return "bg-cyan-400/[0.12] text-cyan-300 border-cyan-400/20";
  if (s.includes("negative") || s === "neg")
    return "bg-amber-400/[0.12] text-amber-300 border-amber-400/20";
  return "bg-white/[0.06] text-white/45 border-white/10";
}

function formatIndexValue(value: number | null): string {
  if (value === null) return "—";
  return value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
}

// ── StatusPill ────────────────────────────────────────────────────────

function StatusPill({ state }: { state: ClimateStatePayload | null }) {
  if (!state) return null;
  const oldest   = getOldestValidDate(state);
  if (!oldest) return null;
  const stale    = isStale(oldest);
  const timeLabel = formatValidDateUTC(oldest);

  return (
    <div
      className={[
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
        stale
          ? "border border-amber-400/20 bg-amber-400/10 text-amber-300"
          : "border border-emerald-400/20 bg-emerald-400/10 text-emerald-300",
      ].join(" ")}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          stale ? "bg-amber-400" : "bg-emerald-400"
        }`}
      />
      {stale
        ? "Some sources stale"
        : `All sources verified${timeLabel ? ` ${timeLabel}` : ""}`}
    </div>
  );
}

// ── ClimateStatePanel ─────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] py-2.5 last:border-b-0">
      <div className="h-3 w-20 animate-pulse rounded bg-white/[0.07]" />
      <div className="h-3 w-16 animate-pulse rounded bg-white/[0.07]" />
    </div>
  );
}

function StateRow({
  label,
  value,
  badge,
  badgeStyle,
}: {
  label: string;
  value: string;
  badge: string | null;
  badgeStyle: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] py-2.5 last:border-b-0">
      <span className="text-[12px] font-medium text-white/60">{label}</span>
      <div className="flex shrink-0 items-center gap-2">
        <span className="text-[12px] font-medium text-white">{value}</span>
        {badge && (
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${badgeStyle}`}>
            {badge}
          </span>
        )}
      </div>
    </div>
  );
}

function ClimateStatePanel({
  state,
  loading,
}: {
  state: ClimateStatePayload | null;
  loading: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-[#07111f] p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.20em] text-white/40">
        Current Climate State
      </div>

      {loading && !state ? (
        <>
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
        </>
      ) : state ? (
        <>
          <StateRow
            label="ENSO"
            value={formatIndexValue(state.enso?.value ?? null)}
            badge={state.enso?.state ?? null}
            badgeStyle={ensoBadgeStyle(state.enso?.state ?? null)}
          />
          <StateRow
            label="MJO"
            value={
              state.mjo?.phase != null
                ? `Phase ${state.mjo.phase}${
                    state.mjo.amplitude != null
                      ? ` · ${state.mjo.amplitude.toFixed(1)}`
                      : ""
                  }`
                : "—"
            }
            badge={state.mjo?.state ?? null}
            badgeStyle="bg-white/[0.06] text-white/45 border-white/10"
          />
          <StateRow
            label="AO"
            value={formatIndexValue(state.ao?.value ?? null)}
            badge={state.ao?.state ?? null}
            badgeStyle={oscillationBadgeStyle(state.ao?.state ?? null)}
          />
          <StateRow
            label="NAO"
            value={formatIndexValue(state.nao?.value ?? null)}
            badge={state.nao?.state ?? null}
            badgeStyle={oscillationBadgeStyle(state.nao?.state ?? null)}
          />
          <StateRow
            label="PNA"
            value={formatIndexValue(state.pna?.value ?? null)}
            badge={state.pna?.state ?? null}
            badgeStyle={oscillationBadgeStyle(state.pna?.state ?? null)}
          />
        </>
      ) : (
        <p className="py-4 text-[12px] text-white/35">State data unavailable</p>
      )}
    </div>
  );
}

// ── SidebarNav ────────────────────────────────────────────────────────

function SidebarNav({ activeSection }: { activeSection: SectionId }) {
  function scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <nav aria-label="Climate sections">
      <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.24em] text-white/30">
        Sections
      </p>
      <div className="flex flex-col gap-0.5">
        {SECTIONS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => scrollTo(id)}
            className={[
              "w-full rounded-md px-3 py-1.5 text-left text-[13px] transition-colors",
              activeSection === id
                ? "bg-white/[0.07] font-medium text-white"
                : "text-white/50 hover:bg-white/[0.04] hover:text-white/80",
            ].join(" ")}
          >
            {label}
          </button>
        ))}
      </div>
    </nav>
  );
}

// ── Section header ────────────────────────────────────────────────────

function SectionHeader({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-xl font-semibold tracking-tight text-white">{title}</h2>
      {description && (
        <p className="mt-1.5 text-[13px] leading-6 text-white/50">{description}</p>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────

export default function Climate() {
  const [climateState, setClimateState] = useState<ClimateStatePayload | null>(null);
  const [climateStateLoading, setClimateStateLoading] = useState(true);
  const [activeSection, setActiveSection] = useState<SectionId>("sst");
  const observerRef = useRef<IntersectionObserver | null>(null);

  // Fetch climate state (non-critical — silent on failure)
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function fetchState() {
      try {
        const res = await fetch(`${API_V4_BASE}/climate/state`, {
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as ClimateStatePayload;
        if (!cancelled) setClimateState(data);
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === "AbortError") return;
        // Non-critical panel — silently ignore failures
      } finally {
        if (!cancelled) setClimateStateLoading(false);
      }
    }

    void fetchState();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // Track active section for sidebar highlight
  useEffect(() => {
    if (observerRef.current) observerRef.current.disconnect();

    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id as SectionId);
          }
        }
      },
      { rootMargin: "-80px 0px -55% 0px", threshold: 0 },
    );

    observerRef.current = io;

    for (const { id } of SECTIONS) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }

    return () => io.disconnect();
  }, []);

  return (
    // Full-bleed breakout, matching the models / home page pattern
    <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 text-white md:-mt-16">

      {/* ── Page header ─────────────────────────────────────────────── */}
      <div className="border-b border-white/8 bg-[#07111f] px-5 pb-12 pt-24 md:px-8 md:pt-28">
        <div className="mx-auto max-w-6xl">
          <div className="lg:flex lg:items-start lg:justify-between lg:gap-12">

            {/* Left: eyebrow + title + description + status pill */}
            <div className="flex-1">
              <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
                <span className="h-px w-7 bg-cyan-300/45" />
                <span>Climate Indices</span>
              </div>
              <h1 className="mt-5 text-3xl font-semibold tracking-tight text-white md:text-4xl">
                Seasonal &amp; Climate Signals
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-7 text-white/60">
                Large-scale drivers that shape pattern evolution weeks to months out — SSTs,
                teleconnection indices, ENSO forecasts, and drought monitoring.
              </p>
              <div className="mt-5">
                <StatusPill state={climateState} />
              </div>
              <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1">
                {SECTIONS.map(({ id, label }) => (
                  <a
                    key={id}
                    href={`#${id}`}
                    className="text-[12px] text-cyan-400/60 transition hover:text-cyan-300"
                  >
                    {label}
                  </a>
                ))}
              </div>
            </div>

            {/* Right: Climate State Panel — lg+ only */}
            <div className="mt-8 hidden w-72 shrink-0 lg:mt-0 lg:block">
              <ClimateStatePanel state={climateState} loading={climateStateLoading} />
            </div>

          </div>
        </div>
      </div>

      {/* ── Two-column layout ────────────────────────────────────────── */}
      <div className="mx-auto max-w-6xl px-5 py-10 md:px-8 md:py-12">
        <div>

          {/* Main content */}
          <div className="space-y-14">

            {/* ── Sea Surface Temperatures ──────────────────────────── */}
            <section id="sst" className="scroll-mt-24">
              <SectionHeader title="Sea Surface Temperatures" />
              <div className="grid gap-4 sm:grid-cols-2">
                <ClimateIndexWidget
                  title="SST Anomaly"
                  source="NOAA Coral Reef Watch · 5km"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.sstAnomaly)}
                  sourceUrl={IMG.sstAnomaly}
                />
                <ClimateIndexWidget
                  title="SST 7-Day Trend"
                  source="NOAA Coral Reef Watch · 5km"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.sstTrend7d)}
                  sourceUrl={IMG.sstTrend7d}
                />
              </div>
            </section>

            {/* ── MJO Forecast ─────────────────────────────────────── */}
            <section id="mjo" className="scroll-mt-24">
              <SectionHeader title="MJO Forecast" />
              <div className="grid gap-4 sm:grid-cols-2">
                <ClimateIndexWidget
                  title="ECMWF 15-Day MJO"
                  source="CPC · ECMWF"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.mjoEcmf)}
                  sourceUrl={IMG.mjoEcmf}
                  aspectRatio="square"
                />
                <ClimateIndexWidget
                  title="ECMWF Extended 45-Day MJO"
                  source="CPC · ECMWF"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.mjoEmon)}
                  sourceUrl={IMG.mjoEmon}
                  aspectRatio="square"
                />
              </div>
              <div className="mt-4 flex flex-wrap gap-4">
                <a
                  href="https://www.atmos.albany.edu/facstaff/roundy/waves/rmmcyc/index200reg.html"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[12px] text-cyan-400/70 underline hover:text-cyan-300"
                >
                  View correlation maps ↗
                </a>
                <a
                  href="https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/mjo.shtml"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[12px] text-cyan-400/70 underline hover:text-cyan-300"
                >
                  CPC MJO data ↗
                </a>
              </div>
            </section>

            {/* ── ENSO — Niño 3.4 ──────────────────────────────────── */}
            <section id="enso" className="scroll-mt-24">
              <SectionHeader title="ENSO — Niño 3.4" />
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <ClimateIndexWidget
                  title="ECMWF ENSO Plumes"
                  source="CPC · CFSv2"
                  cadence="Monthly"
                  proxyUrl={proxy(IMG.ensoCfs)}
                  sourceUrl={IMG.ensoCfs}
                  aspectRatio="tall"
                />
                <ClimateIndexWidget
                  title="CPC ENSO Probability"
                  source="CPC"
                  cadence="Monthly"
                  proxyUrl={proxy(IMG.ensoCpcProb)}
                  sourceUrl={IMG.ensoCpcProb}
                  aspectRatio="tall"
                />
                <ClimateIndexWidget
                  title="CFSv2 Forecast"
                  source="CPC · CFSv2"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.ensoCfs)}
                  sourceUrl={IMG.ensoCfs}
                  aspectRatio="tall"
                />
                <ClimateIndexWidget
                  title="Niño 3.4 Observed"
                  source="Tropical Tidbits"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.ninoTidbits)}
                  sourceUrl={IMG.ninoTidbits}
                  aspectRatio="tall"
                />
              </div>
            </section>

            {/* ── AO / NAO / PNA ───────────────────────────────────── */}
            <section id="oscillations" className="scroll-mt-24">
              <SectionHeader
                title="AO / NAO / PNA"
                description="GEFS ensemble spread. Positive/negative phase determines blocking patterns and cold air delivery into CONUS."
              />
              <div className="grid gap-4 sm:grid-cols-3">
                <ClimateIndexWidget
                  title="Arctic Oscillation (AO)"
                  source="CPC · GEFS Ensemble"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.ao)}
                  sourceUrl={IMG.ao}
                  aspectRatio="tall"
                />
                <ClimateIndexWidget
                  title="North Atlantic Oscillation (NAO)"
                  source="CPC · GEFS Ensemble"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.nao)}
                  sourceUrl={IMG.nao}
                  aspectRatio="tall"
                />
                <ClimateIndexWidget
                  title="Pacific-North American Pattern (PNA)"
                  source="CPC · GEFS Ensemble"
                  cadence="Daily"
                  proxyUrl={proxy(IMG.pna)}
                  sourceUrl={IMG.pna}
                  aspectRatio="tall"
                />
              </div>
            </section>

            {/* ── Drought Monitor ──────────────────────────────────── */}
            <section id="drought" className="scroll-mt-24">
              <SectionHeader title="Drought Monitor" />
              <div className="grid gap-4 sm:grid-cols-2">
                <ClimateIndexWidget
                  title="Current Drought Conditions"
                  source="NDMC · USDA · NOAA"
                  cadence="Weekly · Thu"
                  proxyUrl={proxy(IMG.droughtCurrent)}
                  sourceUrl={IMG.droughtCurrent}
                />
                <ClimateIndexWidget
                  title="4-Week Drought Change"
                  source="NDMC · USDA · NOAA"
                  cadence="Weekly · Thu"
                  proxyUrl={proxy(IMG.droughtChange)}
                  sourceUrl={IMG.droughtChange}
                />
              </div>
            </section>

          </div>
        </div>
      </div>
    </div>
  );
}
