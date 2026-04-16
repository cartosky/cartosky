import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Layers3, Map, Radar, ShieldAlert } from "lucide-react";

import { fetchCapabilities, type CapabilitiesResponse } from "@/lib/api";

type ModelReference = {
  eyebrow: string;
  oneLiner: string;
  coverage: string;
  cadence: string;
  focus: string[];
  notes: string[];
};

const CORE_MODEL_ORDER = ["hrrr", "gfs", "nam", "nbm", "ecmwf", "aifs", "aigfs"] as const;
const SPECIAL_LAYER_ORDER = ["spc", "nws_hazards", "mrms"] as const;

const MODEL_REFERENCE: Record<string, ModelReference> = {
  hrrr: {
    eyebrow: "Core Model",
    oneLiner: "Convection-permitting short-range guidance for storms, wind, and fast mesoscale structure.",
    coverage: "CONUS",
    cadence: "Hourly cycles",
    focus: [
      "Rapid convective evolution and storm mode hints",
      "Short-fuse winter banding and mesoscale thermal structure",
      "Wind maxima and tight near-term gradients",
    ],
    notes: ["Best used when the setup is already inside the short-range window."],
  },
  gfs: {
    eyebrow: "Core Model",
    oneLiner: "Global pattern guidance for synoptic structure, longer lead time, and broader trend recognition.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Large-scale trough and ridge timing",
      "Longer-range temperature and precip pattern changes",
      "Baseline context before model-to-model comparison",
    ],
    notes: ["Use it to set the pattern first, then tighten the read with higher-resolution guidance."],
  },
  nam: {
    eyebrow: "Core Model",
    oneLiner: "Mesoscale bridge guidance between global pattern context and storm-scale detail.",
    coverage: "CONUS",
    cadence: "Every 6 hours",
    focus: [
      "Frontal structure and thermal-gradient placement",
      "Short-to-mid range synoptic-to-mesoscale evolution",
      "Pattern continuity between GFS and HRRR-style guidance",
    ],
    notes: ["Strong context model when you want more structure than a global field without going full storm-scale."],
  },
  nbm: {
    eyebrow: "Core Model",
    oneLiner: "Blended baseline guidance for sensible weather expectations without as much single-model noise.",
    coverage: "CONUS and PNW",
    cadence: "Every 3 hours",
    focus: [
      "Consensus checks on temperatures, precip, snow, and wind",
      "Calmer baseline before diving into deterministic spread",
      "Extended-range overview for broad-brush impacts",
    ],
    notes: ["Useful as the calmer reference surface, not as the final read on fast mesoscale structure."],
  },
  ecmwf: {
    eyebrow: "Core Model",
    oneLiner: "Global deterministic guidance for pattern quality, thermal fields, and cleaner large-scale evolution.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Large-scale pattern evolution and medium-range structure",
      "Thermal fields and broader moisture placement",
      "Cross-checking GFS before committing to a deeper read",
    ],
    notes: ["A strong pattern anchor when you want a second global deterministic take."],
  },
  aifs: {
    eyebrow: "Core Model",
    oneLiner: "AI-based ECMWF global guidance sourced from the same open-data stream, now rolled out with surface temperature, dew point, 850mb temperature, 850mb heights and winds, 300mb heights and winds, precipitable water, total precip, total snowfall, and 10m wind speed.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Fast comparison against deterministic ECMWF low-level thermal and moisture structure",
      "Alternative 850mb thermal structure for advection and winter-profile context",
      "Alternative 850mb jet and height pattern for low-level forcing context",
      "Alternative 300mb jet and height pattern for upper-level support context",
      "Alternative whole-column moisture signal for plume depth and transport context",
      "Alternative large-scale precipitation placement and accumulation signal from the same upstream feed",
      "Alternative broad-brush snowfall footprint and accumulation signal from the same upstream feed",
      "Alternative large-scale surface wind and boundary-layer evolution from the same upstream feed",
      "Early read on where AIFS diverges from classic IFS guidance",
    ],
    notes: ["Initial rollout stays focused on near-surface and surface-accumulation fields while runtime behavior is validated across more cycles."],
  },
  aigfs: {
    eyebrow: "Core Model",
    oneLiner: "NOAA AI GFS guidance from the operational AIGFS stream, now rolled out with 2m temperature and derived 10m wind speed from the surface product plus 850mb temperature, 850mb heights and winds, and 300mb heights and winds from the pressure product.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Fast comparison against classic GFS surface thermal structure",
      "Alternative 850mb thermal structure for advection and winter-profile context",
      "Alternative 850mb jet and height pattern for low-level forcing context",
      "Alternative 300mb jet and height pattern for upper-level support context",
      "Alternative boundary-layer wind-speed signal from the operational AI GFS surface fields",
      "Alternative AI-guided 2m temperature signal for large-scale pattern checks",
      "Early detection of temperature spread between traditional and AI global guidance",
    ],
    notes: ["Initial rollout stays intentionally narrow: tmp2m and derived 10m wind speed from the NOAA/Herbie AIGFS surface product, plus tmp850, wspd850, and wspd300 from the pressure product, while runtime behavior is validated in production."],
  },
  spc: {
    eyebrow: "Operational Layer",
    oneLiner: "Official SPC Day 1-3 outlook products rendered directly into the CartoSky workflow.",
    coverage: "CONUS",
    cadence: "Issuance-driven",
    focus: [
      "Convective outlook context before diving into deterministic fields",
      "Tornado, wind, hail, and categorical probability inspection",
      "Fast alignment between official risk areas and model guidance",
    ],
    notes: ["Not a numerical model, but an official analysis layer that belongs in the same forecasting workflow."],
  },
  nws_hazards: {
    eyebrow: "Operational Layer",
    oneLiner: "Active NWS watches, warnings, advisories, and statements surfaced as a live situational layer.",
    coverage: "CONUS and supported marine zones",
    cadence: "Near-real-time",
    focus: [
      "Current warning-state awareness alongside model guidance",
      "Fast inspection of overlapping local hazards",
      "Live warning context without leaving the map",
    ],
    notes: ["Current-state situational layer rather than forecast guidance."],
  },
  mrms: {
    eyebrow: "Operational Layer",
    oneLiner: "Observed radar-style precipitation products for present-tense weather context.",
    coverage: "CONUS",
    cadence: "Observed / frequently updated",
    focus: [
      "Current precipitation structure and type context",
      "Radar-style comparison against forecast fields",
      "Bridging observed conditions with near-term guidance",
    ],
    notes: ["Observed layer, useful as a live anchor before switching into forecast products."],
  },
};

function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

function formatRunLabel(runId?: string | null): string {
  if (!runId) return "Latest pending";
  const normalized = runId.trim();
  if (!normalized) return "Latest pending";
  if (normalized.toLowerCase() === "latest") return "Latest";

  const runMatch = normalized.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})?z$/i);
  if (!runMatch) return normalized;

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

function DetailList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/42">{title}</div>
      <ul className="mt-3 space-y-2 text-sm leading-7 text-white/68">
        {items.map((item) => (
          <li key={item} className="flex gap-3">
            <span className="mt-3 h-1.5 w-1.5 rounded-full bg-cyan-200/70" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type DisplayModel = {
  id: string;
  name: string;
  latestRun: string;
  variableCount: number;
  coverage: string;
  cadence: string;
  eyebrow: string;
  oneLiner: string;
  focus: string[];
  notes: string[];
};

function ModelRow({
  model,
  isOpen,
  onToggle,
}: {
  model: DisplayModel;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border-t border-white/8 first:border-t-0">
      <div className="my-3 rounded-[1.2rem] border border-white/8 bg-white/[0.02] px-4 py-4 md:px-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-200/70">{model.eyebrow}</div>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
            <h3 className="text-xl font-semibold tracking-tight text-white">{model.name}</h3>
            <span className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-white/48">
              {model.variableCount} live products
            </span>
          </div>
          <p className="mt-2.5 max-w-2xl text-sm leading-6 text-white/68">{model.oneLiner}</p>
          <div className="mt-4 flex flex-wrap gap-2 text-[11px] font-medium text-white/60">
            <span className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1">{model.coverage}</span>
            <span className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1">{model.cadence}</span>
            <span className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1">Latest {model.latestRun}</span>
          </div>
        </div>

        <button
          type="button"
          onClick={onToggle}
          aria-expanded={isOpen}
          className="inline-flex items-center justify-center rounded-xl border border-white/10 px-4 py-2 text-sm font-medium text-white/78 transition duration-150 hover:border-white/20 hover:bg-white/[0.04]"
        >
          {isOpen ? "Hide details" : "Show details"}
        </button>
      </div>

      {isOpen ? (
          <div className="mt-5 grid gap-6 border-t border-white/8 pt-5 md:grid-cols-[1.1fr_0.9fr]">
            <DetailList title="Best used for" items={model.focus} />
            <DetailList title="Operational notes" items={model.notes} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function Models() {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [openId, setOpenId] = useState("");

  useEffect(() => {
    const controller = new AbortController();

    fetchCapabilities({ signal: controller.signal })
      .then((response) => setCapabilities(response))
      .catch(() => {
        // Reference page remains readable if capabilities bootstrap is unavailable.
      });

    return () => controller.abort();
  }, []);

  const modelState = useMemo(() => {
    const catalog = capabilities?.model_catalog ?? {};
    const availability = capabilities?.availability ?? {};
    const supported = capabilities?.supported_models ?? [];
    const supportedIds = supported.filter((modelId) => Boolean(catalog[modelId]));

    const buildModel = (modelId: string): DisplayModel | null => {
      const model = catalog[modelId];
      if (!model) return null;
      const reference = MODEL_REFERENCE[modelId];
      const variableEntries = Object.entries(model.variables ?? {}).filter(([, variable]) => {
        if (!variable || variable.buildable === false) return false;
        const varKey = String(variable.var_key ?? "");
        return !/^ptype_intensity_(rain|snow|ice)$/i.test(varKey);
      });

      return {
        id: modelId,
        name: model.name ?? modelId.toUpperCase(),
        latestRun: formatRunLabel(availability[modelId]?.latest_run ?? null),
        variableCount: variableEntries.length,
        coverage: reference?.coverage ?? model.canonical_region?.toUpperCase() ?? "Supported region",
        cadence: reference?.cadence ?? "Current operational cadence",
        eyebrow: reference?.eyebrow ?? "Supported Layer",
        oneLiner: reference?.oneLiner ?? "Supported in the current CartoSky catalog.",
        focus: reference?.focus ?? ["Current live support available in the CartoSky viewer."],
        notes: reference?.notes ?? ["Rendered through the same map-first forecasting workflow as the rest of the catalog."],
      };
    };

    const coreModels = CORE_MODEL_ORDER.map(buildModel).filter(Boolean) as DisplayModel[];
    const specialtyModels = [
      ...SPECIAL_LAYER_ORDER.map(buildModel).filter(Boolean),
      ...supportedIds
        .filter((modelId) => !CORE_MODEL_ORDER.includes(modelId as (typeof CORE_MODEL_ORDER)[number]) && !SPECIAL_LAYER_ORDER.includes(modelId as (typeof SPECIAL_LAYER_ORDER)[number]))
        .map(buildModel)
        .filter(Boolean),
    ] as DisplayModel[];

    return {
      coreModels,
      specialtyModels,
      totalModelCount: supportedIds.length,
      totalCoreVariables: coreModels.reduce((sum, model) => sum + model.variableCount, 0),
    };
  }, [capabilities]);

  return (
    <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 space-y-0 text-white md:-mt-16">
      <section className="border-b border-white/8 bg-[#07111f] px-5 pb-12 pt-24 md:px-8 md:pb-14 md:pt-28">
        <div className="mx-auto max-w-6xl">
          <div className="grid gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:items-end">
            <div className="max-w-3xl">
              <SectionEyebrow>Reference</SectionEyebrow>
              <h1 className="mt-6 text-balance text-4xl font-semibold tracking-[-0.04em] text-white md:text-6xl md:leading-[0.98]">
                The model catalog,
                <br />
                <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic text-cyan-200">
                  clearly scoped.
                </span>
              </h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-white/72 md:text-[1.02rem]">
                A cleaner reference surface for the guidance CartoSky supports right now: core models,
                official operational layers, cadence context, and the kinds of reads each source is best at.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Link
                  to="/viewer"
                  className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
                >
                  Open Viewer
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <Link
                  to="/forecast"
                  className="inline-flex items-center gap-2 rounded-xl border border-white/14 bg-white/[0.04] px-5 py-3 text-sm font-semibold text-white/86 transition duration-200 hover:border-white/22 hover:bg-white/[0.07]"
                >
                  Open Forecast
                </Link>
              </div>
            </div>

            <div className="border-l border-white/8 pl-5 lg:pl-7">
              <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-white/42">Current Support</div>
              <div className="mt-5 space-y-4">
                <div>
                  <div className="text-2xl font-semibold tracking-tight text-white">{modelState.coreModels.length || CORE_MODEL_ORDER.length}</div>
                  <div className="mt-1 text-sm text-white/58">Core model families.</div>
                </div>
                <div className="h-px bg-white/8" />
                <div>
                  <div className="text-2xl font-semibold tracking-tight text-white">{modelState.specialtyModels.length}</div>
                  <div className="mt-1 text-sm text-white/58">Operational layers.</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[#0b1527] px-5 py-16 md:px-8 md:py-18">
        <div className="mx-auto max-w-6xl">
          <div className="flex items-start gap-4">
            <div className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
              <Layers3 className="h-5 w-5" />
            </div>
            <div>
              <SectionEyebrow>Core Models</SectionEyebrow>
              <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight text-white md:text-4xl">
                The guidance that anchors the viewer.
              </h2>
            </div>
          </div>

          <div className="mt-10">
            {modelState.coreModels.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                isOpen={openId === model.id}
                onToggle={() => setOpenId((current) => (current === model.id ? "" : model.id))}
              />
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-white/8 bg-[#0c172b] px-5 py-16 md:px-8 md:py-18">
        <div className="mx-auto max-w-6xl">
          <div className="flex items-start gap-4">
            <div className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
              <ShieldAlert className="h-5 w-5" />
            </div>
            <div>
              <SectionEyebrow>Operational Layers</SectionEyebrow>
              <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight text-white md:text-4xl">
                Official and observed context belongs in the same workflow.
              </h2>
            </div>
          </div>

          <div className="mt-10 grid gap-4 lg:grid-cols-3">
            {modelState.specialtyModels.map((model) => (
              <div
                key={model.id}
                className="rounded-[1.2rem] border border-white/8 bg-white/[0.02] px-4 py-5"
              >
                <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-cyan-200">
                  {model.id === "spc" ? <Radar className="h-5 w-5" /> : model.id === "mrms" ? <Map className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
                </div>
                <div className="mt-5 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-200/70">{model.eyebrow}</div>
                <h3 className="mt-3 text-lg font-semibold tracking-tight text-white">{model.name}</h3>
                <p className="mt-2.5 text-sm leading-6 text-white/68">{model.oneLiner}</p>
                <div className="mt-4 flex flex-wrap gap-2 text-[11px] font-medium text-white/58">
                  <span>{model.coverage}</span>
                  <span className="text-white/22">/</span>
                  <span>{model.cadence}</span>
                  <span className="text-white/22">/</span>
                  <span>{model.variableCount} products</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
