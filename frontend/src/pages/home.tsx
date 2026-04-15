import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Clock3,
  CloudLightning,
  Gauge,
  Globe2,
  Layers3,
  Move,
  Radar,
  Snowflake,
  Sparkles,
} from "lucide-react";

import { fetchCapabilities, type CapabilitiesResponse } from "@/lib/api";

const CORE_MODEL_IDS = ["hrrr", "gfs", "nam", "nbm", "ecmwf", "aifs"] as const;

function formatRunLabel(runId?: string | null): string {
  if (!runId) {
    return "Latest";
  }
  const normalized = runId.trim();
  if (!normalized) {
    return "Latest";
  }
  if (normalized.toLowerCase() === "latest") {
    return "Latest";
  }

  const runMatch = normalized.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})?z$/i);
  if (!runMatch) {
    return normalized;
  }

  const [, year, month, day, hour, minuteRaw] = runMatch;
  const minute = Number(minuteRaw ?? "0");
  const runDate = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), minute, 0));
  const dateLabel = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(runDate);
  const timeLabel = minute > 0 ? `${hour}:${String(minute).padStart(2, "0")}Z` : `${hour}Z`;
  return `${timeLabel} (${dateLabel})`;
}

function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.3em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

function ProofItem({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="border-white/8 px-5 py-5 first:pl-0 last:pr-0 md:border-l md:first:border-l-0 md:px-7">
      <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/45">{label}</div>
      <div className="mt-2 text-sm font-semibold text-white md:text-base">{value}</div>
      <div className="mt-1 text-sm text-white/55">{detail}</div>
    </div>
  );
}

function ViewerPreview({
  hrrrRunLabel,
  gfsRunLabel,
}: {
  hrrrRunLabel: string;
  gfsRunLabel: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-[2rem] border border-white/10 bg-[#081120] shadow-[0_32px_120px_rgba(0,0,0,0.45)]">
      <div
        aria-hidden="true"
        className="absolute inset-0 opacity-95"
        style={{
          backgroundImage: `
            linear-gradient(115deg, rgba(16,25,42,0.96), rgba(8,17,32,0.92)),
            url(/assets/hero-image.png)
          `,
          backgroundSize: "auto, cover",
          backgroundPosition: "center, center right",
        }}
      />
      <div
        aria-hidden="true"
        className="absolute inset-0 opacity-80"
        style={{
          backgroundImage: `
            linear-gradient(90deg, rgba(7,17,31,0.92) 0%, rgba(7,17,31,0.72) 34%, rgba(7,17,31,0.34) 70%, rgba(7,17,31,0.44) 100%),
            linear-gradient(180deg, rgba(7,17,31,0.2), rgba(7,17,31,0.58))
          `,
        }}
      />
      <div className="relative z-10 border-b border-white/10 bg-slate-950/55 px-4 py-3 backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium text-white/72">
          <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-cyan-100">
            HRRR
          </span>
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">CONUS</span>
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">2m Temp</span>
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">{hrrrRunLabel}</span>
          <span className="ml-auto inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1 text-emerald-100">
            <span className="h-2 w-2 rounded-full bg-emerald-300" />
            Current cycle
          </span>
        </div>
      </div>

      <div className="relative z-10 grid gap-4 p-4 sm:p-5">
        <div className="rounded-[1.35rem] border border-white/10 bg-slate-950/30 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] uppercase tracking-[0.2em] text-white/45">
            <span>Viewer showcase</span>
            <span className="text-cyan-200/70">Screenshot-ready stage</span>
          </div>
          <div className="mt-5 h-[260px] overflow-hidden rounded-[1.2rem] border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01))]">
            <div className="relative h-full w-full overflow-hidden">
              <div aria-hidden="true" className="absolute inset-0 bg-[linear-gradient(180deg,#102338_0%,#081120_100%)]" />
              <div
                aria-hidden="true"
                className="absolute inset-0 opacity-95"
                style={{
                  backgroundImage: `
                    linear-gradient(90deg, rgba(8,17,32,0.62) 0%, rgba(8,17,32,0.28) 46%, rgba(8,17,32,0.38) 100%),
                    url(/assets/hero-image.png)
                  `,
                  backgroundSize: "auto, cover",
                  backgroundPosition: "center, center right",
                }}
              />
              <div
                aria-hidden="true"
                className="absolute inset-0 opacity-30"
                style={{
                  backgroundImage:
                    "linear-gradient(0deg, transparent 0 88%, rgba(255,255,255,0.06) 88.1% 88.4%, transparent 88.5% 100%), linear-gradient(90deg, transparent 0 86%, rgba(255,255,255,0.05) 86.1% 86.4%, transparent 86.5% 100%)",
                }}
              />
              <div className="absolute left-4 top-4 rounded-full border border-white/15 bg-slate-950/60 px-3 py-1 text-[11px] font-medium text-white/75 backdrop-blur-md">
                HRRR · CONUS · 2m Temp
              </div>
              <div className="absolute right-4 top-4 rounded-full border border-white/15 bg-slate-950/60 px-3 py-1 text-[11px] font-medium text-white/75 backdrop-blur-md">
                Valid 18Z
              </div>
              <div className="absolute left-4 top-[27%] max-w-[15rem] rounded-2xl border border-white/12 bg-slate-950/58 p-4 text-left backdrop-blur-md">
                <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">Viewer Focus</div>
                <div className="mt-3 text-sm font-semibold text-white">Map-dominant forecast workflow</div>
                <div className="mt-2 text-xs leading-6 text-white/60">
                  This stage is reserved for the redesigned viewer screenshot that will ship before public beta.
                </div>
              </div>
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-white/45">Init 12Z</div>
            <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
              <div className="absolute inset-y-0 left-0 w-[52%] rounded-full bg-gradient-to-r from-cyan-300 via-sky-300 to-slate-200" />
              <div className="absolute left-[52%] top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-slate-950 bg-cyan-200 shadow-[0_0_16px_rgba(103,232,249,0.45)]" />
            </div>
            <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-white/45">F24</div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-white/58">
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">GFS · {gfsRunLabel}</span>
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">NAM · Latest</span>
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">NBM · Every 3 hours</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProductCard({
  eyebrow,
  title,
  description,
  icon,
  variant,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
  variant: "winter" | "severe";
}) {
  const backgroundStyle =
    variant === "winter"
      ? {
          backgroundImage: `
            radial-gradient(circle at 22% 24%, rgba(207,250,254,0.16), transparent 0 18%),
            linear-gradient(135deg, rgba(20,32,53,0.9), rgba(9,21,36,0.96)),
            linear-gradient(180deg, rgba(255,255,255,0.04), transparent 42%),
            linear-gradient(125deg, rgba(255,255,255,0.05) 0 18%, transparent 18% 100%)
          `,
        }
      : {
          backgroundImage: `
            radial-gradient(circle at 72% 30%, rgba(34,211,238,0.14), transparent 0 16%),
            radial-gradient(circle at 40% 62%, rgba(248,113,113,0.1), transparent 0 12%),
            linear-gradient(135deg, rgba(16,24,39,0.92), rgba(8,20,32,0.98)),
            linear-gradient(180deg, rgba(255,255,255,0.02), transparent 38%)
          `,
        };

  return (
    <div className="group relative overflow-hidden rounded-[1.7rem] border border-white/10 bg-white/[0.03] p-5 shadow-[0_18px_60px_rgba(0,0,0,0.22)]">
      <div aria-hidden="true" className="absolute inset-0 opacity-95 transition duration-300 group-hover:scale-[1.02]" style={backgroundStyle} />
      <div
        aria-hidden="true"
        className="absolute inset-0 opacity-60"
        style={{
          backgroundImage:
            variant === "winter"
              ? "linear-gradient(135deg, transparent 0 68%, rgba(255,255,255,0.06) 68% 69%, transparent 69% 100%), linear-gradient(90deg, transparent 0 82%, rgba(255,255,255,0.04) 82% 82.6%, transparent 82.6% 100%)"
              : "linear-gradient(180deg, transparent 0 74%, rgba(34,211,238,0.08) 74% 75%, transparent 75% 100%), linear-gradient(90deg, transparent 0 14%, rgba(34,211,238,0.08) 14% 14.6%, transparent 14.6% 100%), linear-gradient(90deg, transparent 0 86%, rgba(34,211,238,0.07) 86% 86.6%, transparent 86.6% 100%)",
        }}
      />
      <div className="relative z-10 flex min-h-[260px] flex-col">
        <div>
          <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-slate-950/35 text-cyan-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-sm">
            {icon}
          </div>
          <div className="mt-5 text-[10px] font-semibold uppercase tracking-[0.24em] text-white/55">{eyebrow}</div>
          <h3 className="mt-3 text-2xl font-semibold tracking-tight text-white">{title}</h3>
          <p className="mt-3 max-w-md text-sm leading-7 text-white/70">{description}</p>
        </div>
      </div>
    </div>
  );
}

function TrustPoint({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-[1.6rem] border border-white/8 bg-white/[0.03] p-6 shadow-[0_16px_40px_rgba(0,0,0,0.18)]">
      <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
        {icon}
      </div>
      <h3 className="mt-5 text-xl font-semibold tracking-tight text-white">{title}</h3>
      <p className="mt-3 text-sm leading-7 text-white/66">{description}</p>
    </div>
  );
}

export default function Home() {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetchCapabilities({ signal: controller.signal })
      .then((response) => setCapabilities(response))
      .catch(() => {
        // Keep homepage copy stable if the capability bootstrap is transiently unavailable.
      });

    return () => controller.abort();
  }, []);

  const homepageStats = useMemo(() => {
    const modelCatalog = capabilities?.model_catalog ?? {};
    const coreModels = CORE_MODEL_IDS.filter((modelId) => Boolean(modelCatalog[modelId]));
    const hrrrRunLabel = formatRunLabel(capabilities?.availability?.hrrr?.latest_run);
    const gfsRunLabel = formatRunLabel(capabilities?.availability?.gfs?.latest_run);

    return {
      coreModelCount: coreModels.length || 5,
      hrrrRunLabel,
      gfsRunLabel,
      freshnessDetail: `HRRR ${hrrrRunLabel} · GFS ${gfsRunLabel}`,
    };
  }, [capabilities]);

  return (
    <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 space-y-0 text-white md:-mt-16">
      <section className="relative overflow-hidden border-b border-white/8 bg-[#07111f] px-5 pb-10 pt-28 md:px-8 md:pb-14 md:pt-32">
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-95"
          style={{
            backgroundImage: `
              linear-gradient(90deg, rgba(6,12,24,0.94) 0%, rgba(6,12,24,0.82) 30%, rgba(6,12,24,0.46) 58%, rgba(6,12,24,0.62) 100%),
              linear-gradient(180deg, rgba(7,17,31,0.72), rgba(7,17,31,0.92)),
              url(/assets/hero-image.png)
            `,
            backgroundSize: "auto, auto, cover",
            backgroundPosition: "center, center, center right",
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              "radial-gradient(circle at 24% 34%, rgba(255,255,255,0.12), transparent 0 10%), radial-gradient(circle at 70% 56%, rgba(125,211,252,0.14), transparent 0 10%)",
          }}
        />

        <div className="relative mx-auto grid min-h-[calc(100svh-8rem)] max-w-6xl items-center gap-14 py-8 lg:grid-cols-[1.15fr_0.85fr] lg:gap-10">
          <div className="max-w-4xl text-center lg:text-left">
            <h1 className="mt-8 max-w-4xl text-balance text-5xl font-semibold tracking-[-0.04em] text-white drop-shadow-[0_8px_28px_rgba(0,0,0,0.45)] md:text-7xl md:leading-[0.98]">
              Weather data,
              <br />
              <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic tracking-[-0.03em] text-cyan-200">
                clearly rendered.
              </span>
            </h1>
            <p className="mt-8 max-w-2xl text-balance text-base leading-8 text-white/74 md:text-lg lg:text-left">
              Interactive weather maps, built for speed, without the clutter.
            </p>

            <div className="mt-10 flex flex-wrap items-center justify-center gap-3 lg:justify-start">
              <Link
                to="/viewer"
                className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.22)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
              >
                Open Viewer
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link
                to="/forecast"
                className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-slate-950/25 px-5 py-3 text-sm font-semibold text-white/88 backdrop-blur-sm transition duration-200 hover:border-white/25 hover:bg-white/[0.06]"
              >
                Open Forecast
              </Link>
            </div>
          </div>

          <div className="relative hidden lg:block">
            <div className="absolute -left-6 top-10 h-28 w-px bg-gradient-to-b from-transparent via-cyan-200/35 to-transparent" />
            <div className="rounded-[1.8rem] border border-white/10 bg-slate-950/24 p-6 shadow-[0_28px_70px_rgba(0,0,0,0.24)] backdrop-blur-sm">
              <div className="text-[10px] font-semibold uppercase tracking-[0.26em] text-white/42">Current Desk</div>
              <div className="mt-6 space-y-4">
                <div className="flex items-center justify-between gap-4 border-b border-white/8 pb-3">
                  <div>
                    <div className="text-sm font-semibold text-white">HRRR</div>
                    <div className="mt-1 text-xs text-white/52">Storm-scale short range</div>
                  </div>
                  <div className="text-sm font-medium text-cyan-100">{homepageStats.hrrrRunLabel}</div>
                </div>
                <div className="flex items-center justify-between gap-4 border-b border-white/8 pb-3">
                  <div>
                    <div className="text-sm font-semibold text-white">GFS</div>
                    <div className="mt-1 text-xs text-white/52">Global pattern guidance</div>
                  </div>
                  <div className="text-sm font-medium text-cyan-100">{homepageStats.gfsRunLabel}</div>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-semibold text-white">Core Workflow</div>
                    <div className="mt-1 text-xs text-white/52">Models, time, variables, freshness</div>
                  </div>
                  <div className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1 text-xs font-medium text-emerald-100">
                    <span className="h-2 w-2 rounded-full bg-emerald-300" />
                    Ready
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-10 flex justify-center lg:col-span-2 lg:mt-0">
            <div className="h-6 w-6 rounded-full border border-white/12 bg-white/[0.03] text-white/40">
              <div className="flex h-full items-center justify-center text-base leading-none">⌄</div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[#0b1527] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-[0.78fr_1.22fr]">
          <div className="max-w-lg">
            <SectionEyebrow>Core Interface</SectionEyebrow>
            <h2 className="mt-6 max-w-md text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
              Map-first analysis,
              <br />
              less friction.
            </h2>
            <p className="mt-5 text-base leading-8 text-white/66">
              CartoSky keeps the map dominant, with core controls always within reach - so you can move through data without breaking your flow.
            </p>

          <div className="mt-10 space-y-5">
            <div className="flex gap-4">
              <div className="mt-1 text-cyan-200">
                <Move className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold uppercase tracking-[0.18em] text-white/54">
                  Interactive Map
                </div>
                <p className="mt-2 text-sm leading-7 text-white/62">
                  Move through the map without interruption. Zoom, pan, and explore fluidly - no reloads, no static images.
                </p>
              </div>
            </div>

            <div className="mt-10 space-y-5">
              <div className="flex gap-4">
                <div className="mt-1 text-cyan-200">
                  <Layers3 className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-sm font-semibold uppercase tracking-[0.18em] text-white/54">
                    Model switching
                  </div>
                  <p className="mt-2 text-sm leading-7 text-white/62">
                    Switch models without losing your place. Viewport and context stay locked as you move between guidance.
                  </p>
                </div>
              </div>

              <div className="flex gap-4">
                <div className="mt-1 text-cyan-200">
                  <Activity className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-sm font-semibold uppercase tracking-[0.18em] text-white/54">
                    Trust signals
                  </div>
                  <p className="mt-2 text-sm leading-7 text-white/62">
                    Run freshness is always visible. Know exactly what you're looking at and how current it is, without hunting through menus.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>  

          <ViewerPreview
            hrrrRunLabel={homepageStats.hrrrRunLabel}
            gfsRunLabel={homepageStats.gfsRunLabel}
          />
        </div>
      </section>

      <section className="border-y border-white/8 bg-[#091423] px-5 md:px-8">
        <div className="mx-auto grid max-w-6xl gap-y-2 py-4 md:grid-cols-4 md:py-5">
          <ProofItem
            label="Models"
            value={`${homepageStats.coreModelCount} core models`}
            detail="HRRR, NAM, GFS, NBM, ECMWF, and AIFS in one workflow."
          />
          <ProofItem
            label="Products"
            value="~15 per model"
            detail="Surface, precip, severe, and upper-air products, with more on the way."
          />
          <ProofItem
            label="Coverage"
            value="CONUS and expanding"
            detail="Optimized for U.S. weather analysis, with broader regions coming soon."
          />
          <ProofItem
            label="Use Cases"
            value="Severe, winter, and more."
            detail="Designed for high-impact forecasting scenarios."
          />
        </div>
      </section>

      <section className="border-b border-white/8 bg-[#0c172b] px-5 py-16 md:px-8 md:py-18">
        <div className="mx-auto flex max-w-6xl flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <SectionEyebrow>Forecast</SectionEyebrow>
            <h2 className="mt-5 text-balance text-3xl font-semibold tracking-tight text-white md:text-4xl">
              Start with a local briefing.
            </h2>
            <p className="mt-4 text-base leading-8 text-white/64">
              Check current conditions and short-range context for any location then move straight into the map for deeper analysis. More forecast detail and model guidance coming soon.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Link
              to="/forecast"
              className="inline-flex items-center gap-2 rounded-xl border border-white/14 bg-white/[0.04] px-5 py-3 text-sm font-semibold text-white/88 transition duration-200 hover:border-white/24 hover:bg-white/[0.07]"
            >
              Open Forecast
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/viewer"
              className="inline-flex items-center gap-2 rounded-xl text-sm font-medium text-cyan-200/92 transition duration-200 hover:text-cyan-100"
            >
              Skip to Viewer
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </section>

      <section className="border-y border-white/6 bg-[#0d182d] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto max-w-6xl">
          <SectionEyebrow>Forecast Products</SectionEyebrow>
          <h2 className="mt-6 max-w-3xl text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
            Workflows for winter and severe weather.
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-white/64">
            Move from core fields into the products that matter when timing, structure, and impact come into focus.
          </p>

          <div className="mt-12 grid gap-6 lg:grid-cols-2">
            <ProductCard
              eyebrow="Winter Analysis"
              title="Understand where snow will actually accumulate."
              description="Use snowfall, thermal structure, and timing to see how a setup evolves and where real impacts are likely."
              icon={<Snowflake className="h-5 w-5" />}
              variant="winter"
            />
            <ProductCard
              eyebrow="Severe Analysis"
              title="See the full severe setup in one place."
              description="Models, SPC Outlooks, and radar come together in a single view so you can track how storms evolve in real time."
              icon={<CloudLightning className="h-5 w-5" />}
              variant="severe"
            />
          </div>
        </div>
      </section>

      <section className="bg-[#0a1425] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto max-w-6xl">
          <SectionEyebrow>Why CartoSky</SectionEyebrow>
          <h2 className="mt-6 max-w-3xl text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
            A better way to work the forecast.
          </h2>
          <div className="mt-12 grid gap-8 border-t border-white/8 pt-8 lg:grid-cols-3">
            <div className="border-l border-white/8 pl-5 first:border-l-0 first:pl-0">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
                <Gauge className="h-5 w-5" />
              </div>
              <h3 className="mt-5 text-xl font-semibold tracking-tight text-white">Work with the map, don't just view it</h3>
              <p className="mt-3 text-sm leading-7 text-white/66">
                Pan, zoom, and work the forecast directly on the map instead of stepping through static images.
              </p>
            </div>
            <div className="border-l border-white/8 pl-5">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
                <Sparkles className="h-5 w-5" />
              </div>
              <h3 className="mt-5 text-xl font-semibold tracking-tight text-white">More context in one place</h3>
              <p className="mt-3 text-sm leading-7 text-white/66">
                Models, forecasts, SPC outlooks, and live radar live in the same workflow, so you spend less time jumping between sites.
              </p>
            </div>
            <div className="border-l border-white/8 pl-5">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
                <Layers3 className="h-5 w-5" />
              </div>
              <h3 className="mt-5 text-xl font-semibold tracking-tight text-white">Built to stay focused</h3>
              <p className="mt-3 text-sm leading-7 text-white/66">
                A cleaner interface keeps the map and the highest-value controls in front, without burying the signal under clutter.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-white/6 bg-[#08111f] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto max-w-3xl text-center">
          <SectionEyebrow>Open The Viewer</SectionEyebrow>
          <h2 className="mt-6 text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
            Start with the map.
          </h2>
          <p className="mt-5 text-base leading-8 text-white/64 md:text-lg">
            Work the forecast directly - models, radar, SPC outlooks, and more in a single interactive view.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Link
              to="/viewer"
              className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
            >
              Open Viewer
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/forecast"
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-slate-950/30 px-5 py-3 text-sm font-semibold text-white/85 transition duration-200 hover:border-white/25 hover:bg-white/[0.06]"
            >
              Open Forecast
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
