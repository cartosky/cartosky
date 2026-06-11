import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowRight, CloudSun, Layers3, Radar, ShieldAlert, Users } from "lucide-react";

import { PrefetchLink } from "@/components/PrefetchLink";

import { fetchCapabilities, type CapabilitiesResponse } from "@/lib/api";
import { normalizeModelRows, viewerModelGroup } from "@/lib/app-utils";

type ModelReference = {
  eyebrow: string;
  oneLiner: string;
  coverage: string;
  cadence: string;
  focus: string[];
  notes: string[];
};

type ModelCategoryId = "MODELS" | "ENSEMBLES" | "FORECASTS" | "OBSERVATIONS";

const MODEL_CATEGORY_SECTIONS: Array<{
  id: ModelCategoryId;
  eyebrow: string;
  title: string;
  description: string;
  layout: "rows" | "cards";
}> = [
  {
    id: "MODELS",
    eyebrow: "Core Models",
    title: "The guidance that anchors the viewer.",
    description: "Deterministic and AI-global model families for short-range through long-range forecasting.",
    layout: "rows",
  },
  {
    id: "ENSEMBLES",
    eyebrow: "Ensemble Suites",
    title: "Spread-aware context without leaving the map.",
    description: "Ensemble-mean fields for comparing consensus against deterministic guidance.",
    layout: "rows",
  },
  {
    id: "FORECASTS",
    eyebrow: "Official Forecasts",
    title: "Official forecast products in the same workflow.",
    description: "NWS and partner forecast layers for planning beyond single deterministic model runs.",
    layout: "cards",
  },
  {
    id: "OBSERVATIONS",
    eyebrow: "Observations & Situational Layers",
    title: "Present-tense context belongs beside the guidance.",
    description: "Radar, satellite, analysis, and live hazard layers for grounding the forecast read.",
    layout: "cards",
  },
];

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
    oneLiner: "AI-based ECMWF global guidance with surface thermal and moisture fields, upper-level flow, precipitation, snowfall, anomalies, and precip anomalies.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Surface temperature, dew point, RH, and 10m wind versus classic IFS guidance",
      "850mb and 300mb thermal and wind structure for advection and jet context",
      "Precipitable water, total precip, snowfall, and multi-day precip anomaly signals",
      "Surface, 850mb, and 500mb anomaly fields for pattern comparison",
    ],
    notes: ["Useful as a fast AI-global comparison layer against deterministic ECMWF and other global guidance."],
  },
  aigfs: {
    eyebrow: "Core Model",
    oneLiner: "NOAA AI GFS guidance with surface temperature, precipitation, wind, upper-level flow, vorticity, anomalies, and precip anomalies.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Surface temperature and 10m wind versus classic GFS guidance",
      "850mb and 300mb thermal and wind structure for low-level forcing context",
      "500mb vorticity and height pattern for shortwave and lift diagnostics",
      "Total precip plus surface, 850mb, and 500mb anomaly fields",
      "Multi-day precip anomaly footprints for medium-range pattern checks",
    ],
    notes: ["Operational AI GFS stream for comparing AI-guided global structure against traditional GFS output."],
  },
  gefs: {
    eyebrow: "Ensemble Suite",
    oneLiner: "NOAA GEFS ensemble-mean guidance for thermal, moisture, instability, wind, and accumulation context across the medium range.",
    coverage: "North America",
    cadence: "Every 6 hours",
    focus: [
      "Ensemble-mean surface temperature, RH, wind, and CAPE corridors",
      "850mb and 300mb flow means for low-level and upper-level support",
      "Mean precipitation, snowfall, and multi-day precip anomaly signals",
      "Surface and 850mb temperature anomaly means for pattern comparison",
    ],
    notes: ["Ensemble-mean product. Pair with deterministic guidance when you need spread and run-to-run context."],
  },
  eps: {
    eyebrow: "Ensemble Suite",
    oneLiner: "ECMWF EPS ensemble-mean guidance for surface thermal and wind structure, RH, anomalies, and medium-range precip anomalies.",
    coverage: "Global",
    cadence: "Every 6 hours",
    focus: [
      "Ensemble-mean surface temperature and 10m wind versus deterministic ECMWF",
      "700mb RH mean for mid-level moisture context",
      "Surface and 850mb temperature anomaly means",
      "500mb height anomaly and 15-day precip anomaly signals",
    ],
    notes: ["ECMWF ensemble anchor alongside GEFS for consensus and spread checks without jumping to deterministic-only reads."],
  },
  ndfd: {
    eyebrow: "Official Forecast",
    oneLiner: "NWS NDFD official forecast grids for daily temperatures, QPF, snowfall, ice, and peak wind gusts.",
    coverage: "CONUS",
    cadence: "Issuance-driven",
    focus: [
      "Official min and max temperature planning",
      "6h, 24h, and 48h QPF and snowfall accumulation windows",
      "Freezing-rain ice accumulation and peak gust timing",
    ],
    notes: ["Official NWS forecast layer rather than deterministic model output."],
  },
  spc: {
    eyebrow: "Official Forecast",
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
  cpc: {
    eyebrow: "Official Forecast",
    oneLiner: "CPC extended-range temperature and precipitation outlooks for 6-10 and 8-14 day planning.",
    coverage: "CONUS",
    cadence: "Issuance-driven",
    focus: [
      "Extended temperature outlook context",
      "Extended precipitation outlook context",
      "Medium-range pattern persistence checks",
    ],
    notes: ["Official CPC outlook product for planning beyond the deterministic model window."],
  },
  wpc: {
    eyebrow: "Official Forecast",
    oneLiner: "WPC precipitation forecast totals for synoptic-scale rain and winter-event planning.",
    coverage: "CONUS",
    cadence: "Issuance-driven",
    focus: [
      "Official storm-total precipitation screening",
      "Cross-checking model QPF against WPC guidance",
      "Synoptic-scale wet-corridor context",
    ],
    notes: ["Official WPC precipitation guidance rather than model-derived QPF."],
  },
  nws_hazards: {
    eyebrow: "Situational Layer",
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
    eyebrow: "Observed Layer",
    oneLiner: "Observed MRMS radar and recent-precip products for present-tense weather context.",
    coverage: "CONUS",
    cadence: "Observed / frequently updated",
    focus: [
      "Current reflectivity and precipitation-type structure",
      "Recent 6h, 24h, and 72h precipitation totals",
      "Observed comparison against short-range guidance",
    ],
    notes: ["Observed layer, useful as a live anchor before switching into forecast products."],
  },
  current_analysis: {
    eyebrow: "Observed Analysis",
    oneLiner: "Real-time RTMA-RU surface analysis for current temperature, dew point, wind, and gust conditions.",
    coverage: "CONUS",
    cadence: "Frequently updated",
    focus: [
      "Present-tense surface temperature and dew point",
      "Current 10m wind and gust conditions",
      "Ground-truth context before reading forecast guidance",
    ],
    notes: ["Analysis layer, not forecast guidance."],
  },
  "goes-east": {
    eyebrow: "Observed Analysis",
    oneLiner: "GOES-East clean IR satellite imagery for cloud-top structure and large-scale system organization.",
    coverage: "GOES-East sector",
    cadence: "Frequently updated",
    focus: [
      "Cloud-top temperature and organization",
      "Large-scale system structure and mesoscale cluster context",
      "Satellite context before diving into model fields",
    ],
    notes: ["Satellite observation rather than model-derived output."],
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
  category: ModelCategoryId;
};

function categoryIcon(category: ModelCategoryId) {
  if (category === "MODELS") return <Layers3 className="h-5 w-5" />;
  if (category === "ENSEMBLES") return <Users className="h-5 w-5" />;
  if (category === "FORECASTS") return <ShieldAlert className="h-5 w-5" />;
  return <Radar className="h-5 w-5" />;
}

function modelCardIcon(modelId: string) {
  if (modelId === "spc") return <Radar className="h-5 w-5" />;
  if (modelId === "mrms") return <Radar className="h-5 w-5" />;
  if (modelId === "goes-east") return <CloudSun className="h-5 w-5" />;
  if (modelId === "current_analysis") return <CloudSun className="h-5 w-5" />;
  return <ShieldAlert className="h-5 w-5" />;
}

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

function ModelCard({ model }: { model: DisplayModel }) {
  return (
    <div className="rounded-[1.2rem] border border-white/8 bg-white/[0.02] px-4 py-5">
      <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-cyan-200">
        {modelCardIcon(model.id)}
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
    const orderedIds = normalizeModelRows(capabilities, supportedIds).map((entry) => entry.id);

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
        category: viewerModelGroup(modelId) as ModelCategoryId,
      };
    };

    const models = orderedIds.map(buildModel).filter(Boolean) as DisplayModel[];
    const grouped = MODEL_CATEGORY_SECTIONS.reduce<Record<ModelCategoryId, DisplayModel[]>>((acc, section) => {
      acc[section.id] = models.filter((model) => model.category === section.id);
      return acc;
    }, {
      MODELS: [],
      ENSEMBLES: [],
      FORECASTS: [],
      OBSERVATIONS: [],
    });

    const categoryCounts = MODEL_CATEGORY_SECTIONS.map((section) => ({
      id: section.id,
      label: section.eyebrow,
      count: grouped[section.id].length,
    })).filter((row) => row.count > 0);

    return {
      grouped,
      categoryCounts,
      totalModelCount: models.length,
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
                ensemble suites, official forecast layers, and observed situational context.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <PrefetchLink
                  to="/viewer"
                  className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
                >
                  Open Viewer
                  <ArrowRight className="h-4 w-4" />
                </PrefetchLink>
                <PrefetchLink
                  to="/forecast"
                  className="inline-flex items-center gap-2 rounded-xl border border-white/14 bg-white/[0.04] px-5 py-3 text-sm font-semibold text-white/86 transition duration-200 hover:border-white/22 hover:bg-white/[0.07]"
                >
                  Open Forecast
                </PrefetchLink>
              </div>
            </div>

            <div className="border-l border-white/8 pl-5 lg:pl-7">
              <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-white/42">Current Support</div>
              <div className="mt-5 space-y-4">
                <div>
                  <div className="text-2xl font-semibold tracking-tight text-white">{modelState.totalModelCount || "Live"}</div>
                  <div className="mt-1 text-sm text-white/58">Supported model and layer sources.</div>
                </div>
                <div className="h-px bg-white/8" />
                {modelState.categoryCounts.map((row) => (
                  <div key={row.id}>
                    <div className="text-lg font-semibold tracking-tight text-white">{row.count}</div>
                    <div className="mt-1 text-sm text-white/58">{row.label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {MODEL_CATEGORY_SECTIONS.map((section, index) => {
        const models = modelState.grouped[section.id];
        if (!models.length) {
          return null;
        }

        return (
          <section
            key={section.id}
            className={`${index % 2 === 0 ? "bg-[#0b1527]" : "border-t border-white/8 bg-[#0c172b]"} px-5 py-16 md:px-8 md:py-18`}
          >
            <div className="mx-auto max-w-6xl">
              <div className="flex items-start gap-4">
                <div className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
                  {categoryIcon(section.id)}
                </div>
                <div>
                  <SectionEyebrow>{section.eyebrow}</SectionEyebrow>
                  <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight text-white md:text-4xl">
                    {section.title}
                  </h2>
                  <p className="mt-3 max-w-3xl text-sm leading-7 text-white/62 md:text-base">{section.description}</p>
                </div>
              </div>

              <div className={section.layout === "cards" ? "mt-10 grid gap-4 lg:grid-cols-3" : "mt-10"}>
                {section.layout === "rows"
                  ? models.map((model) => (
                      <ModelRow
                        key={model.id}
                        model={model}
                        isOpen={openId === model.id}
                        onToggle={() => setOpenId((current) => (current === model.id ? "" : model.id))}
                      />
                    ))
                  : models.map((model) => <ModelCard key={model.id} model={model} />)}
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}
