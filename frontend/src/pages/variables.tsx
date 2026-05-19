import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Droplets, Flame, Layers3, Snowflake, Wind } from "lucide-react";

import { fetchCapabilities, type CapabilityVariable, type CapabilitiesResponse } from "@/lib/api";

type VariableReference = {
  definition: string;
  bestFor: string[];
  interpretation: string[];
  limitations?: string[];
};

const VARIABLE_REFERENCE: Record<string, VariableReference> = {
  tmp2m: {
    definition: "Near-surface air temperature at roughly two meters above ground, used for impact timing, thermal gradients, and boundary placement.",
    bestFor: [
      "Tracking temperature gradients and freeze-line context",
      "Comparing broad thermal structure between guidance sources",
      "Situational awareness for surface impacts and boundary placement",
    ],
    interpretation: [
      "Use with dew point and wind to understand mixing, recovery, and boundary behavior.",
      "Treat it as air temperature guidance, not a direct road-surface or urban microclimate forecast.",
    ],
  },
  dp2m: {
    definition: "Near-surface dew point, used as a quick read on low-level moisture quality, saturation potential, and instability support.",
    bestFor: [
      "Moisture advection and dryline placement",
      "Low cloud, fog, and saturation context",
      "Pairing with temperature for instability setup reads",
    ],
    interpretation: [
      "Sharp dew point gradients often reveal important boundaries even when temperatures look smoother.",
      "Use with CAPE and wind fields to separate moisture presence from actual storm potential.",
    ],
  },
  rh2m: {
    definition: "Near-surface relative humidity, used to quickly assess saturation, drying, and low-level moisture depth near the ground.",
    bestFor: [
      "Fog, low cloud, and near-saturation context",
      "Boundary-layer drying and recovery trends",
      "Pairing with temperature and dew point for surface moisture reads",
    ],
    interpretation: [
      "High values show the air is close to saturation, but not whether deep moisture or lift is present.",
      "Use with dew point to separate truly moist air from cool air that is merely near saturation.",
    ],
  },
  tmp850: {
    definition: "850 mb temperature field for low-level thermal advection, warm nose diagnosis, and synoptic structure.",
    bestFor: [
      "Warm and cold advection pattern recognition",
      "Winter-weather thermal profile context",
      "Frontal-zone and baroclinic structure reads",
    ],
    interpretation: [
      "This is not a surface-temperature product, so it should be paired with 2m temperature and precipitation fields.",
    ],
  },
  wspd850: {
    definition: "850 mb wind speed shaded with height context to diagnose low-level jet structure, moisture transport, and warm advection.",
    bestFor: [
      "Low-level jet placement and evolution",
      "Moisture transport into heavy-rain or severe setups",
      "Frontal-zone and warm-advection pattern reads",
    ],
    interpretation: [
      "Use the speed maxima for focus, but keep the broader pattern in view with heights and thermodynamic fields.",
    ],
  },
  wspd300: {
    definition: "300 mb wind speed shaded with height context to show jet-stream structure and upper-level support.",
    bestFor: [
      "Jet streak placement and broader upper flow orientation",
      "Comparing upper support with lower-level moisture and instability",
      "Seeing the large-scale jet pattern quickly",
    ],
    interpretation: [
      "This is a synoptic support field, not a direct surface-impact map.",
    ],
  },
  vort500: {
    definition: "500 mb absolute vorticity with height context for troughs, shortwaves, and mid-level forcing structure.",
    bestFor: [
      "Shortwave tracking and synoptic timing",
      "Mid-level energy maxima and forcing context",
      "Pattern evolution during active setups",
    ],
    interpretation: [
      "Use it to locate the energy first, then pair it with moisture and instability to judge practical impact.",
    ],
  },
  sbcape: {
    definition: "Surface-based buoyant energy for parcels rooted at the ground, used in warm-sector severe-weather setups.",
    bestFor: [
      "Surface-rooted instability reads",
      "Comparing warm-sector quality across model runs",
      "Cross-checking with MLCAPE and MUCAPE in boundary-layer driven setups",
    ],
    interpretation: [
      "High CAPE alone does not guarantee storms; forcing, shear, and inhibition still decide the outcome.",
    ],
  },
  mlcape: {
    definition: "Mixed-layer buoyant energy for a representative boundary-layer parcel, often the cleaner broad-brush instability field.",
    bestFor: [
      "Warm-season instability corridors",
      "Comparing overlap between moisture, heating, and forcing",
      "Broad severe-weather setup assessment",
    ],
    interpretation: [
      "Best used with dew point, wind fields, and convective timing context rather than alone.",
    ],
  },
  mucape: {
    definition: "Most-unstable buoyant energy from the most unstable lower-tropospheric parcel, especially useful in elevated regimes.",
    bestFor: [
      "Elevated instability above shallow stable layers",
      "Nocturnal convection and elevated severe setups",
      "Comparing surface-based versus elevated storm potential",
    ],
    interpretation: [
      "Large MUCAPE can exist in capped or elevated setups that never fully realize surface-based storm intensity.",
    ],
  },
  pwat: {
    definition: "Integrated column moisture, used to judge how moisture-rich the atmosphere is before lift and storm coverage are considered.",
    bestFor: [
      "Moisture plume tracking",
      "Heavy-rain and tropical moisture setup context",
      "Recognizing moisture-limited versus moisture-loaded patterns",
    ],
    interpretation: [
      "It is a moisture field, not a rainfall forecast. Pair it with lift and convective coverage context.",
    ],
  },
  precip_total: {
    definition: "Accumulated liquid-equivalent precipitation over the forecast window.",
    bestFor: [
      "Precipitation axis placement and broader storm totals",
      "Comparing run-to-run shifts in precip swaths",
      "Baseline input for derived snowfall products",
    ],
    interpretation: [
      "Convective regimes can make QPF volatile from run to run, so use it with forcing context.",
    ],
  },
  snowfall_total: {
    definition: "Derived snowfall using a simpler fixed-ratio approach, useful as a quick baseline snow field.",
    bestFor: [
      "Broad-brush storm-total snow potential",
      "Comparing storm-track shifts through accumulation gradients",
      "Fast first-pass winter analysis",
    ],
    interpretation: [
      "It is convenient, but not as profile-aware as a temperature-dependent approach.",
    ],
  },
  snowfall_kuchera_total: {
    definition: "Derived snowfall using a temperature-dependent Kuchera-style ratio rather than a fixed 10:1 assumption.",
    bestFor: [
      "Profile-aware snowfall comparisons",
      "Colder versus wetter snow-regime analysis",
      "Higher-confidence winter storm mapping than simple fixed-ratio output",
    ],
    interpretation: [
      "Still inherits upstream QPF errors and boundary-layer issues, so treat it as guidance rather than observed depth.",
    ],
  },
  ice_total: {
    definition: "Accumulated freezing-rain liquid equivalent derived from precipitation steps gated by the model freezing-rain precipitation type.",
    bestFor: [
      "Freezing-rain accretion placement and magnitude awareness",
      "Comparing ice-risk corridors against precipitation type and surface temperature",
      "Winter-storm impact screening where glaze accumulation is possible",
    ],
    interpretation: [
      "Pair it with precipitation type, surface temperature, and QPF to evaluate marginal freezing-rain zones.",
      "Small spatial shifts matter, so compare runs and nearby guidance before treating the axis as fixed.",
    ],
  },
  wspd10m: {
    definition: "Sustained wind speed at 10 meters above ground, used for gradient wind and near-surface flow context.",
    bestFor: [
      "Gradient wind events and travel-impact planning",
      "Surface wind maxima in tighter pressure gradients",
      "Comparing broader low-level wind patterns",
    ],
    interpretation: [
      "Compare with gusts for a better read on mixing and turbulence potential.",
    ],
  },
  wgst10m: {
    definition: "Modeled peak gust potential at 10 meters above ground, useful for impact-level wind awareness.",
    bestFor: [
      "Peak wind impact corridors",
      "Post-frontal and dry-slot mixing reads",
      "Quick situational awareness for gust-sensitive setups",
    ],
    interpretation: [
      "Treat gust output as guidance, not a guarantee, especially outside convection-permitting scenarios.",
    ],
  },
  radar_ptype: {
    definition: "Simulated composite reflectivity with precipitation-type overlay for quick precip-structure and p-type context.",
    bestFor: [
      "Convective mode and precipitation shield overview",
      "Fast rain-versus-snow context in active systems",
      "Comparing modeled precip structure against other fields",
    ],
    interpretation: [
      "This is model-simulated reflectivity, not observed radar.",
    ],
  },
  ptype_intensity: {
    definition: "Readability-focused precipitation type and intensity display that collapses winter precip into rain, snow, and ice families.",
    bestFor: [
      "Fast rain/snow/ice placement awareness",
      "Winter-weather situational overview",
      "Checking thermal-boundary implications against total precip and snowfall",
    ],
    interpretation: [
      "Use it as a situational field, then verify marginal zones with thermal profiles and accumulation products.",
    ],
  },
  convective: {
    definition: "Official SPC categorical convective outlook polygons.",
    bestFor: [
      "Context-setting before deeper severe-weather analysis",
      "Fast official Day 1-3 risk inspection",
    ],
    interpretation: ["Official outlook layer, not a model-derived forecast field."],
  },
  tornado: {
    definition: "Official SPC tornado probability outlook layer.",
    bestFor: ["Official tornado-risk context in severe-weather setups"],
    interpretation: ["Use as official situational context alongside deterministic and mesoscale model guidance."],
  },
  wind: {
    definition: "Official SPC wind probability outlook layer.",
    bestFor: ["Official damaging-wind risk context in severe-weather setups"],
    interpretation: ["Best used as official context before drilling into model kinematics and instability."],
  },
  hail: {
    definition: "Official SPC hail probability outlook layer.",
    bestFor: ["Official hail-risk context in severe-weather setups"],
    interpretation: ["Best used as official context before drilling into instability, lapse rates, and storm mode."],
  },
  active: {
    definition: "Current active NWS hazards layer, including watches, warnings, advisories, and statements.",
    bestFor: [
      "Current-state warning awareness",
      "Checking active local hazard overlap before deeper forecast analysis",
    ],
    interpretation: ["Operational situational layer rather than forecast guidance."],
  },
  reflectivity: {
    definition: "Observed radar reflectivity field for present-tense precipitation structure.",
    bestFor: [
      "Current precipitation pattern awareness",
      "Observed-versus-forecast comparison against short-range guidance",
    ],
    interpretation: ["Observed layer rather than model guidance."],
  },
  mrms_radar_ptype: {
    definition: "Observed radar-style reflectivity and p-type context product.",
    bestFor: [
      "Present-tense rain/snow structure awareness",
      "Observed comparison against winter forecast fields",
    ],
    interpretation: ["Observed situational product rather than deterministic forecast output."],
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

type VariableSummary = {
  id: string;
  name: string;
  units: string;
  group: string;
  models: string[];
  order: number;
  definition: string;
  bestFor: string[];
  interpretation: string[];
  limitations: string[];
};

const GROUP_ORDER = ["SURFACE", "PRECIPITATION", "PRECIP ANOMALIES", "SEVERE", "UPPER AIR", "OBSERVATIONS"] as const;
const VIEWER_GROUP_BY_VARIABLE: Record<string, string> = {
  tmp2m: "SURFACE",
  dp2m: "SURFACE",
  rh2m: "SURFACE",
  td2m: "SURFACE",
  wspd10m: "SURFACE",
  wgst10m: "SURFACE",
  tmp850: "UPPER AIR",
  wspd850: "UPPER AIR",
  wspd300: "UPPER AIR",
  vort500: "UPPER AIR",
  sbcape: "SEVERE",
  mlcape: "SEVERE",
  mucape: "SEVERE",
  pwat: "PRECIPITATION",
  precip_total: "PRECIPITATION",
  qpf: "PRECIPITATION",
  snowfall_total: "PRECIPITATION",
  snow10to1: "PRECIPITATION",
  snowfall_kuchera_total: "PRECIPITATION",
  snowkuchera: "PRECIPITATION",
  ice_total: "PRECIPITATION",
  ptype_intensity: "PRECIPITATION",
  radar_ptype: "PRECIPITATION",
  precip_5d_anom: "PRECIP ANOMALIES",
  precip_7d_anom: "PRECIP ANOMALIES",
  precip_10d_anom: "PRECIP ANOMALIES",
  precip_15d_anom: "PRECIP ANOMALIES",
  convective: "OBSERVATIONS",
  tornado: "OBSERVATIONS",
  wind: "OBSERVATIONS",
  hail: "OBSERVATIONS",
  active: "OBSERVATIONS",
  reflectivity: "OBSERVATIONS",
  mrms_radar_ptype: "OBSERVATIONS",
};

function groupSortKey(group: string): number {
  const index = GROUP_ORDER.indexOf(group as (typeof GROUP_ORDER)[number]);
  return index === -1 ? GROUP_ORDER.length : index;
}

function groupIcon(group: string) {
  if (group === "SURFACE" || group === "SEVERE") return <Flame className="h-5 w-5" />;
  if (group === "UPPER AIR") return <Wind className="h-5 w-5" />;
  if (group === "PRECIPITATION" || group === "PRECIP ANOMALIES") return <Droplets className="h-5 w-5" />;
  if (group === "OBSERVATIONS") return <Snowflake className="h-5 w-5" />;
  return <Layers3 className="h-5 w-5" />;
}

function canonicalViewerGroup(varKey: string, backendGroup?: string | null): string {
  const mapped = VIEWER_GROUP_BY_VARIABLE[varKey];
  if (mapped) return mapped;

  const normalized = backendGroup?.trim().toLowerCase();
  switch (normalized) {
    case "surface":
    case "temperature":
    case "wind":
      return "SURFACE";
    case "precipitation":
    case "moisture":
    case "radar & precipitation type":
    case "radar":
      return "PRECIPITATION";
    case "anomalies":
    case "precip anomalies":
      return "PRECIP ANOMALIES";
    case "severe":
    case "instability":
      return "SEVERE";
    case "upper air":
    case "dynamics":
      return "UPPER AIR";
    case "observations":
      return "OBSERVATIONS";
    default:
      return "OBSERVATIONS";
  }
}

export default function Variables() {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [openId, setOpenId] = useState("");

  useEffect(() => {
    const controller = new AbortController();

    fetchCapabilities({ signal: controller.signal })
      .then((response) => setCapabilities(response))
      .catch(() => {
        // Keep the reference page useful even if capabilities loading fails.
      });

    return () => controller.abort();
  }, []);

  const variableState = useMemo(() => {
    const catalog = capabilities?.model_catalog ?? {};
    const modelsByVariable = new Map<string, Set<string>>();
    const metaByVariable = new Map<string, CapabilityVariable>();

    for (const model of Object.values(catalog)) {
      for (const [varKey, variable] of Object.entries(model.variables ?? {})) {
        if (!variable || variable.buildable === false) continue;
        if (/^ptype_intensity_(rain|snow|ice)$/i.test(varKey)) continue;

        const modelSet = modelsByVariable.get(varKey) ?? new Set<string>();
        modelSet.add(model.name ?? model.model_id.toUpperCase());
        modelsByVariable.set(varKey, modelSet);

        const existing = metaByVariable.get(varKey);
        if (!existing || (variable.order ?? 999) < (existing.order ?? 999)) {
          metaByVariable.set(varKey, variable);
        }
      }
    }

    const variables: VariableSummary[] = Array.from(metaByVariable.entries())
      .map(([varKey, variable]) => {
        const reference = VARIABLE_REFERENCE[varKey];
        const displayName = variable.display_name?.trim() || varKey;
        const group = canonicalViewerGroup(varKey, variable.group);

        return {
          id: varKey,
          name: displayName,
          units: variable.units?.trim() || "Contextual units",
          group,
          models: Array.from(modelsByVariable.get(varKey) ?? []),
          order: variable.order ?? 999,
          definition: reference?.definition ?? "Supported in the current CartoSky catalog.",
          bestFor: reference?.bestFor ?? ["Reference support is live in the current viewer catalog."],
          interpretation: reference?.interpretation ?? ["Use alongside related fields for a fuller forecasting read."],
          limitations: reference?.limitations ?? [],
        };
      })
      .sort((a, b) => groupSortKey(a.group) - groupSortKey(b.group) || a.order - b.order || a.name.localeCompare(b.name));

    const grouped = variables.reduce<Record<string, VariableSummary[]>>((acc, variable) => {
      acc[variable.group] = [...(acc[variable.group] ?? []), variable];
      return acc;
    }, {});

    return {
      variables,
      grouped,
      groupNames: Object.keys(grouped).sort((a, b) => groupSortKey(a) - groupSortKey(b) || a.localeCompare(b)),
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
                Forecast products,
                <br />
                <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic text-cyan-200">
                  clearly explained.
                </span>
              </h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-white/72 md:text-[1.02rem]">
                A product-facing reference for the variables CartoSky supports right now, grouped the way a serious
                forecast workflow actually reads them: temperatures, wind, moisture, precipitation, instability, dynamics,
                and official layers.
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
                  to="/models"
                  className="inline-flex items-center gap-2 rounded-xl border border-white/14 bg-white/[0.04] px-5 py-3 text-sm font-semibold text-white/86 transition duration-200 hover:border-white/22 hover:bg-white/[0.07]"
                >
                  Browse Models
                </Link>
              </div>
            </div>

            <div className="border-l border-white/8 pl-5 lg:pl-7">
              <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-white/42">Current Library</div>
              <div className="mt-5 space-y-4">
                <div>
                  <div className="text-2xl font-semibold tracking-tight text-white">{variableState.variables.length || "Live"}</div>
                  <div className="mt-1 text-sm text-white/58">Supported product entries.</div>
                </div>
                <div className="h-px bg-white/8" />
                <div>
                  <div className="text-2xl font-semibold tracking-tight text-white">{variableState.groupNames.length || "Multi"}</div>
                  <div className="mt-1 text-sm text-white/58">Viewer group buckets.</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[#0b1527] px-5 py-16 md:px-8 md:py-18">
        <div className="mx-auto max-w-6xl">
          {variableState.groupNames.map((groupName) => (
            <div key={groupName} className="mb-12 last:mb-0">
              <div className="flex items-start gap-4">
                <div className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/12 bg-cyan-300/10 text-cyan-200">
                  {groupIcon(groupName)}
                </div>
                <div>
                  <h2 className="text-balance text-2xl font-semibold tracking-tight text-white md:text-3xl">
                    {groupName}
                  </h2>
                </div>
              </div>

              <div className="mt-8 border-t border-white/8">
                {variableState.grouped[groupName]?.map((variable) => {
                  const isOpen = openId === variable.id;
                  return (
                    <div
                      key={variable.id}
                      className="border-b border-white/8 last:border-b-0"
                    >
                      <div className="my-3 rounded-[1.2rem] border border-white/8 bg-white/[0.02] px-4 py-4 md:px-5">
                        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                          <div className="max-w-3xl">
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                            <h3 className="text-xl font-semibold tracking-tight text-white">{variable.name}</h3>
                            <span className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-white/48">
                              {variable.units}
                            </span>
                          </div>
                          <p className="mt-2.5 text-sm leading-6 text-white/68">{variable.definition}</p>
                          <div className="mt-4 flex flex-wrap gap-2 text-[11px] font-medium text-white/58">
                            {variable.models.map((modelName) => (
                              <span key={modelName} className="rounded-full border border-white/8 bg-white/[0.03] px-3 py-1">
                                {modelName}
                              </span>
                            ))}
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => setOpenId((current) => (current === variable.id ? "" : variable.id))}
                          aria-expanded={isOpen}
                          className="inline-flex items-center justify-center rounded-xl border border-white/10 px-4 py-2 text-sm font-medium text-white/82 transition duration-150 hover:border-white/20 hover:bg-white/[0.04]"
                        >
                          {isOpen ? "Hide details" : "Show details"}
                        </button>
                      </div>

                      {isOpen ? (
                          <div className="mt-5 grid gap-6 border-t border-white/8 pt-5 md:grid-cols-2">
                          <DetailList title="Best used for" items={variable.bestFor} />
                          <DetailList title="How to read it" items={variable.interpretation} />
                          {variable.limitations.length ? <DetailList title="Limitations" items={variable.limitations} /> : null}
                        </div>
                      ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
