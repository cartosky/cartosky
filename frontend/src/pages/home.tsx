import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Clock3,
  Gauge,
  Globe2,
  Layers3,
  Radar,
  Snowflake,
  Sparkles,
} from "lucide-react";

import { fetchCapabilities, type CapabilitiesResponse } from "@/lib/api";

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
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.26em] text-cyan-200/70">
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
            radial-gradient(circle at 72% 42%, rgba(34,197,255,0.18), transparent 0 15%),
            radial-gradient(circle at 42% 58%, rgba(16,185,129,0.16), transparent 0 18%),
            radial-gradient(circle at 68% 57%, rgba(245,158,11,0.16), transparent 0 12%),
            linear-gradient(115deg, rgba(16,25,42,0.96), rgba(8,17,32,0.92)),
            url(/assets/hero-space.webp)
          `,
          backgroundSize: "auto, auto, auto, auto, cover",
          backgroundPosition: "center",
        }}
      />
      <div
        aria-hidden="true"
        className="absolute inset-0 opacity-50"
        style={{
          backgroundImage: `
            repeating-linear-gradient(
              105deg,
              transparent 0 10px,
              rgba(103, 232, 249, 0.05) 10px 11px,
              transparent 11px 26px
            ),
            repeating-radial-gradient(
              circle at 20% 78%,
              rgba(56, 189, 248, 0.16) 0 2px,
              transparent 2px 28px
            )
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
            <span>Map-first viewport</span>
            <span className="text-cyan-200/70">Model switching stays fast</span>
          </div>
          <div className="mt-5 h-[260px] overflow-hidden rounded-[1.2rem] border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01))]">
            <div className="relative h-full w-full overflow-hidden">
              <div
                aria-hidden="true"
                className="absolute inset-0 opacity-[0.92]"
                style={{
                  backgroundImage: `
                    radial-gradient(circle at 50% 55%, rgba(194, 234, 255, 0.16), transparent 0 20%),
                    radial-gradient(circle at 53% 48%, rgba(52, 211, 153, 0.18), transparent 0 15%),
                    radial-gradient(circle at 57% 56%, rgba(244, 114, 182, 0.16), transparent 0 11%),
                    linear-gradient(180deg, rgba(227,240,229,0.88), rgba(158,177,158,0.84)),
                    linear-gradient(135deg, rgba(17,24,39,0.20), rgba(17,24,39,0.04))
                  `,
                }}
              />
              <div
                aria-hidden="true"
                className="absolute inset-0 opacity-70"
                style={{
                  backgroundImage: `
                    linear-gradient(93deg, transparent 0 14%, rgba(9, 21, 39, 0.65) 14.1% 14.5%, transparent 14.6% 100%),
                    linear-gradient(122deg, transparent 0 44%, rgba(9, 21, 39, 0.55) 44.1% 44.45%, transparent 44.55% 100%),
                    linear-gradient(166deg, transparent 0 59%, rgba(9, 21, 39, 0.5) 59.1% 59.45%, transparent 59.55% 100%),
                    linear-gradient(180deg, transparent 0 72%, rgba(255, 255, 255, 0.12) 72.1% 72.4%, transparent 72.5% 100%)
                  `,
                }}
              />
              <div
                aria-hidden="true"
                className="absolute inset-0 opacity-70"
                style={{
                  backgroundImage: `
                    repeating-linear-gradient(
                      116deg,
                      transparent 0 13px,
                      rgba(34,211,238,0.14) 13px 15px,
                      transparent 15px 31px
                    )
                  `,
                }}
              />
              <div className="absolute left-4 top-4 rounded-full border border-white/15 bg-slate-950/60 px-3 py-1 text-[11px] font-medium text-white/75 backdrop-blur-md">
                HRRR · CONUS · 2m Temp
              </div>
              <div className="absolute right-4 top-4 rounded-full border border-white/15 bg-slate-950/60 px-3 py-1 text-[11px] font-medium text-white/75 backdrop-blur-md">
                Valid 18Z
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
            radial-gradient(circle at 26% 26%, rgba(207,250,254,0.22), transparent 0 22%),
            radial-gradient(circle at 70% 38%, rgba(125,211,252,0.16), transparent 0 20%),
            linear-gradient(135deg, rgba(20,32,53,0.88), rgba(9,21,36,0.94)),
            linear-gradient(125deg, rgba(255,255,255,0.05) 0 18%, transparent 18% 100%)
          `,
        }
      : {
          backgroundImage: `
            radial-gradient(circle at 68% 34%, rgba(34,211,238,0.16), transparent 0 18%),
            radial-gradient(circle at 38% 60%, rgba(251,191,36,0.16), transparent 0 14%),
            radial-gradient(circle at 52% 48%, rgba(248,113,113,0.18), transparent 0 12%),
            linear-gradient(135deg, rgba(16,24,39,0.9), rgba(8,20,32,0.96))
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
              ? "repeating-linear-gradient(144deg, transparent 0 18px, rgba(255,255,255,0.06) 18px 19px, transparent 19px 38px)"
              : "repeating-linear-gradient(108deg, transparent 0 14px, rgba(34,211,238,0.10) 14px 16px, transparent 16px 30px)",
        }}
      />
      <div className="relative z-10 flex h-full min-h-[260px] flex-col justify-between">
        <div>
          <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/12 bg-slate-950/35 text-cyan-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-sm">
            {icon}
          </div>
          <div className="mt-5 text-[10px] font-semibold uppercase tracking-[0.24em] text-white/55">{eyebrow}</div>
          <h3 className="mt-3 text-2xl font-semibold tracking-tight text-white">{title}</h3>
          <p className="mt-3 max-w-md text-sm leading-7 text-white/70">{description}</p>
        </div>
        <div className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-cyan-100/90">
          <span>Viewer-ready workflow</span>
          <ArrowRight className="h-4 w-4" />
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
    const supportedModelCount = capabilities?.supported_models.length ?? 6;
    const availabilityEntries = Object.values(capabilities?.availability ?? {});
    const activeFeedCount = availabilityEntries.filter((entry) => entry.latest_run && entry.usable !== false).length;
    const variableCount = Object.values(capabilities?.model_catalog ?? {}).reduce((count, model) => {
      return count + Object.keys(model.variables ?? {}).length;
    }, 0);

    return {
      supportedModelCount,
      activeFeedCount,
      variableCount,
      hrrrRunLabel: formatRunLabel(capabilities?.availability?.hrrr?.latest_run),
      gfsRunLabel: formatRunLabel(capabilities?.availability?.gfs?.latest_run),
    };
  }, [capabilities]);

  return (
    <div className="-mx-5 -mt-12 space-y-0 text-white md:-mx-8 md:-mt-16">
      <section className="relative overflow-hidden border-b border-white/8 bg-[#07111f] px-5 pb-10 pt-28 md:px-8 md:pb-14 md:pt-32">
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-95"
          style={{
            backgroundImage: `
              radial-gradient(circle at 50% 36%, rgba(56,189,248,0.12), transparent 0 26%),
              radial-gradient(circle at 24% 80%, rgba(20,184,166,0.10), transparent 0 22%),
              linear-gradient(180deg, rgba(7,17,31,0.64), rgba(7,17,31,0.9)),
              url(/assets/hero-space.webp)
            `,
            backgroundSize: "auto, auto, auto, cover",
            backgroundPosition: "center",
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-45"
          style={{
            backgroundImage: `
              repeating-radial-gradient(
                circle at 72% 22%,
                rgba(103,232,249,0.14) 0 2px,
                transparent 2px 26px
              ),
              repeating-linear-gradient(
                108deg,
                transparent 0 12px,
                rgba(103,232,249,0.05) 12px 13px,
                transparent 13px 28px
              )
            `,
          }}
        />

        <div className="relative mx-auto flex min-h-[calc(100svh-8rem)] max-w-5xl flex-col items-center justify-center text-center">
          <SectionEyebrow>System Active</SectionEyebrow>
          <h1 className="mt-7 max-w-4xl text-balance text-5xl font-semibold tracking-tight text-white drop-shadow-[0_8px_28px_rgba(0,0,0,0.45)] md:text-7xl md:leading-[1.02]">
            Serious weather guidance,
            <br />
            <span className="bg-[linear-gradient(180deg,#e9fbff_0%,#91dcff_48%,#58bee9_100%)] bg-clip-text italic text-transparent">
              clearly rendered.
            </span>
          </h1>
          <p className="mt-7 max-w-2xl text-balance text-base leading-8 text-white/74 md:text-lg">
            Model data built for technical analysis, with a cleaner interface for switching models,
            scrubbing time, and staying oriented.
          </p>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Link
              to="/viewer"
              className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.22)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
            >
              Open Viewer
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/models"
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-slate-950/25 px-5 py-3 text-sm font-semibold text-white/88 backdrop-blur-sm transition duration-200 hover:border-white/25 hover:bg-white/[0.06]"
            >
              View Models
            </Link>
          </div>

          <div className="mt-20 text-[10px] font-semibold uppercase tracking-[0.28em] text-white/38">
            System active
          </div>
          <div className="mt-2 h-6 w-6 rounded-full border border-white/12 bg-white/[0.03] text-white/40">
            <div className="flex h-full items-center justify-center text-base leading-none">⌄</div>
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 bg-[#091423] px-5 md:px-8">
        <div className="mx-auto grid max-w-6xl gap-y-2 py-4 md:grid-cols-4 md:py-5">
          <ProofItem
            label="Models"
            value={`${homepageStats.supportedModelCount}+ supported`}
            detail="CONUS, regional, and global guidance in one workflow."
          />
          <ProofItem
            label="Products"
            value={`${homepageStats.variableCount}+ tracked`}
            detail="Surface, severe, winter, hydro, and upper-air fields."
          />
          <ProofItem
            label="Freshness"
            value={`${homepageStats.activeFeedCount || 1} active feeds`}
            detail={`HRRR ${homepageStats.hrrrRunLabel}`}
          />
          <ProofItem
            label="Update Cycles"
            value="Hourly to 6-hourly"
            detail={`GFS ${homepageStats.gfsRunLabel}`}
          />
        </div>
      </section>

      <section className="bg-[#0b1527] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-[0.78fr_1.22fr]">
          <div className="max-w-lg">
            <SectionEyebrow>Core Interface</SectionEyebrow>
            <h2 className="mt-6 max-w-md text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
              Map-first analysis,
              <br />
              minimal friction.
            </h2>
            <p className="mt-5 text-base leading-8 text-white/66">
              CartoSky keeps the map dominant while giving fast access to the controls advanced weather
              users actually hit most: model selection, forecast time, and product switching.
            </p>

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
                    Compare core models without losing context, viewport, or run orientation.
                  </p>
                </div>
              </div>

              <div className="flex gap-4">
                <div className="mt-1 text-cyan-200">
                  <Clock3 className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-sm font-semibold uppercase tracking-[0.18em] text-white/54">
                    Time scrubbing
                  </div>
                  <p className="mt-2 text-sm leading-7 text-white/62">
                    Forecast-hour control stays obvious and stable so loop timing feels immediate.
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
                    Freshness and run state stay visible without turning the interface into a warning panel.
                  </p>
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

      <section className="border-y border-white/6 bg-[#0d182d] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto max-w-6xl">
          <SectionEyebrow>Forecast Products</SectionEyebrow>
          <h2 className="mt-6 max-w-3xl text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
            Viewer-ready workflows for winter and severe weather.
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-white/64">
            The homepage should feel current with the season, but the product story stays grounded in
            real forecast analysis rather than abstract “weather tech” branding.
          </p>

          <div className="mt-12 grid gap-6 lg:grid-cols-2">
            <ProductCard
              eyebrow="Winter Analysis"
              title="Kuchera snowfall and cold-season structure."
              description="Use derived snow fields, thermal context, and timing-sensitive guidance to work higher-impact winter setups cleanly."
              icon={<Snowflake className="h-5 w-5" />}
              variant="winter"
            />
            <ProductCard
              eyebrow="Severe Workflow"
              title="Convective and mesoscale signal, without the mess."
              description="Surface fields, reflectivity-style products, and severe-weather context stay easy to scan when the forecast pace picks up."
              icon={<Radar className="h-5 w-5" />}
              variant="severe"
            />
          </div>
        </div>
      </section>

      <section className="bg-[#0a1425] px-5 py-20 md:px-8 md:py-24">
        <div className="mx-auto max-w-6xl">
          <SectionEyebrow>Why CartoSky</SectionEyebrow>
          <h2 className="mt-6 max-w-3xl text-balance text-4xl font-semibold tracking-tight text-white md:text-5xl">
            Built for serious guidance, not visual noise.
          </h2>
          <div className="mt-12 grid gap-6 lg:grid-cols-3">
            <TrustPoint
              icon={<Gauge className="h-5 w-5" />}
              title="Serious guidance"
              description="Forecast products stay tied to model context, run freshness, and the details that matter when you are actually making a read."
            />
            <TrustPoint
              icon={<Sparkles className="h-5 w-5" />}
              title="Clean interface"
              description="Hierarchy does the work. The map leads, the main controls stay obvious, and lower-frequency settings stop fighting for attention."
            />
            <TrustPoint
              icon={<Globe2 className="h-5 w-5" />}
              title="Product depth"
              description="From core surface fields to winter, severe, hydro, and upper-air workflows, CartoSky keeps expanding where weather users actually need it."
            />
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
            Explore live model guidance, forecast products, and seasonal workflows in a cleaner technical interface.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Link
              to="/viewer"
              className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
            >
              Launch Viewer
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/variables"
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-slate-950/30 px-5 py-3 text-sm font-semibold text-white/85 transition duration-200 hover:border-white/25 hover:bg-white/[0.06]"
            >
              Browse Variables
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
