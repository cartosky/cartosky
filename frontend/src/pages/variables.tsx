import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { PrefetchLink } from "@/components/PrefetchLink";
import { ArrowRight, Droplets, Flame, Layers3, Snowflake, Wind } from "lucide-react";

import { fetchCapabilities, type CapabilityVariable, type CapabilitiesResponse } from "@/lib/api";
import { variableCatalogOrder, viewerVariableGroup } from "@/lib/app-utils";

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
  rh700: {
    definition: "Relative humidity at 700 mb, useful for mid-level moisture, dry-slot structure, and cloud-layer context.",
    bestFor: [
      "Mid-level dry intrusion and saturation checks",
      "Cloud-layer and precipitation-growth context",
      "Pairing moisture structure with 500 mb energy and 850 mb flow",
    ],
    interpretation: [
      "Low values can highlight dry air aloft that limits precipitation coverage or enhances evaporative cooling.",
      "Use it with vertical motion and temperature fields rather than treating humidity alone as lift.",
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
  mint: {
    definition: "Forecast daily minimum temperature from the official NDFD blend, focused on the coolest part of the diurnal cycle.",
    bestFor: [
      "Overnight freeze and frost risk context",
      "Comparing coldest-night placement across days",
      "Broad minimum-temperature planning",
    ],
    interpretation: [
      "Treat it as a daily minimum field rather than an hourly temperature trace.",
      "Use it with cloud cover, wind, and surface temperature guidance in marginal freeze setups.",
    ],
  },
  maxt: {
    definition: "Forecast daily maximum temperature from the official NDFD blend, centered on daytime heating potential.",
    bestFor: [
      "Warmest-day placement and intensity",
      "Heat and cooling-demand context",
      "Comparing day-to-day thermal trends",
    ],
    interpretation: [
      "This is a daily maximum field, not an hourly surface-temperature evolution.",
      "Pair it with wind and humidity for a fuller read on daytime impacts.",
    ],
  },
  qpf_6h: {
    definition: "Official NDFD six-hour liquid-equivalent precipitation accumulation.",
    bestFor: [
      "Near-term accumulation windows",
      "Event timing within broader storm totals",
      "Comparing shorter precipitation bursts",
    ],
    interpretation: [
      "Short-window QPF is useful for timing, but convective placement can still shift materially.",
    ],
  },
  qpf_24h: {
    definition: "Official NDFD rolling 24-hour liquid-equivalent precipitation total derived from six-hour fields.",
    bestFor: [
      "Daily precipitation planning",
      "Flood-sensitive day-scale accumulation context",
      "Comparing the heaviest 24-hour axes",
    ],
    interpretation: [
      "Use it for day-scale totals, then inspect six-hour QPF for timing detail.",
    ],
  },
  qpf_48h: {
    definition: "Official NDFD rolling 48-hour liquid-equivalent precipitation total derived from six-hour fields.",
    bestFor: [
      "Two-day storm-total context",
      "Broader event accumulation screening",
      "Comparing longer-duration wet corridors",
    ],
    interpretation: [
      "Longer windows are useful for total impact framing but hide shorter bursts and timing shifts.",
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
  snow_6h: {
    definition: "Official NDFD six-hour snowfall accumulation.",
    bestFor: [
      "Short-window winter accumulation timing",
      "Pinpointing heavier snow periods within a storm",
      "Comparing six-hour snowfall bursts",
    ],
    interpretation: [
      "Short windows help with timing; use daily totals for broader impact framing.",
    ],
  },
  snow_24h: {
    definition: "Official NDFD rolling 24-hour snowfall accumulation derived from six-hour fields.",
    bestFor: [
      "Day-scale snowfall planning",
      "Comparing core daily snow corridors",
      "Broad winter-storm impact context",
    ],
    interpretation: [
      "Useful for daily totals, but verify timing with the six-hour snowfall field.",
    ],
  },
  snow_48h: {
    definition: "Official NDFD rolling 48-hour snowfall accumulation derived from six-hour fields.",
    bestFor: [
      "Multi-day storm-total snowfall context",
      "Longer-duration winter-event screening",
      "Comparing broader heavy-snow swaths",
    ],
    interpretation: [
      "This is best for event framing; shorter windows still matter for operational timing.",
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
  ice_6h: {
    definition: "Official NDFD six-hour freezing-rain ice accumulation.",
    bestFor: [
      "Short-window icing risk timing",
      "Detecting when glaze accumulation ramps up",
      "Comparing marginal freezing-rain periods",
    ],
    interpretation: [
      "Use with surface temperature and precipitation type in marginal setups where small thermal changes matter.",
    ],
  },
  ice_24h: {
    definition: "Official NDFD rolling 24-hour freezing-rain ice accumulation derived from six-hour fields.",
    bestFor: [
      "Day-scale icing impact awareness",
      "Comparing highest ice-risk corridors",
      "Utility and travel impact screening",
    ],
    interpretation: [
      "The daily total is useful for impacts, but the six-hour field gives the timing detail.",
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
  wgust_6h_max: {
    definition: "Official NDFD maximum wind gust expected within each six-hour window.",
    bestFor: [
      "Near-term peak gust awareness",
      "Short-window wind impact timing",
      "Comparing the strongest gust periods in a forecast cycle",
    ],
    interpretation: [
      "Use this as a peak-gust envelope for each window rather than a sustained wind forecast.",
    ],
  },
  wgust_24h_max: {
    definition: "Official NDFD rolling 24-hour maximum wind gust derived from six-hour peak windows.",
    bestFor: [
      "Day-scale wind impact screening",
      "Comparing the strongest gust day in a forecast sequence",
      "Broad planning for wind-sensitive operations",
    ],
    interpretation: [
      "The daily max is useful for impact framing, but six-hour maxima show timing and persistence better.",
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
  tmp2m_anom: {
    definition: "Surface temperature departure from the model climatological baseline, used to spot anomalous warmth or cold relative to normal.",
    bestFor: [
      "Pattern-level warm and cold anomaly corridors",
      "Comparing thermal departures across model runs",
      "Medium-range temperature signal checks",
    ],
    interpretation: [
      "Anomaly fields show departure from normal, not absolute temperature.",
      "Pair with absolute temperature and wind fields for impact-level reads.",
    ],
  },
  tmp850_anom: {
    definition: "850 mb temperature departure from climatology for low-level thermal anomaly and advection context.",
    bestFor: [
      "Low-level warm and cold advection anomaly patterns",
      "Winter-weather thermal profile anomaly checks",
      "Synoptic-scale thermal departure screening",
    ],
    interpretation: [
      "Use with absolute 850mb temperature and surface fields for a fuller winter or severe setup read.",
    ],
  },
  hgt500_anom: {
    definition: "500 mb height departure from climatology for mid-level pattern anomaly and trough/ridge strength context.",
    bestFor: [
      "Mid-level pattern anomaly screening",
      "Trough and ridge strength relative to normal",
      "Medium-range pattern comparison across guidance",
    ],
    interpretation: [
      "Height anomalies help compare pattern strength, but still need moisture and instability for practical impacts.",
    ],
  },
  precip_5d_anom: {
    definition: "Five-day accumulated precipitation anomaly relative to climatology.",
    bestFor: [
      "Medium-range wet versus dry anomaly corridors",
      "Pattern-level precipitation signal checks",
      "Comparing anomaly placement across model runs",
    ],
    interpretation: [
      "Anomaly totals show departure from normal, not absolute rainfall amounts.",
    ],
  },
  precip_7d_anom: {
    definition: "Seven-day accumulated precipitation anomaly relative to climatology.",
    bestFor: [
      "Week-scale wet versus dry anomaly screening",
      "Extended precipitation pattern comparison",
      "Medium-range event potential context",
    ],
    interpretation: [
      "Use alongside absolute QPF and forcing fields before committing to an impact forecast.",
    ],
  },
  precip_10d_anom: {
    definition: "Ten-day accumulated precipitation anomaly relative to climatology.",
    bestFor: [
      "Extended wet versus dry anomaly corridors",
      "Longer-lead precipitation pattern checks",
      "Cross-model anomaly comparison",
    ],
    interpretation: [
      "Longer windows smooth timing detail; pair with shorter-lead QPF for event timing.",
    ],
  },
  precip_15d_anom: {
    definition: "Fifteen-day accumulated precipitation anomaly relative to climatology.",
    bestFor: [
      "Extended-range precipitation anomaly screening",
      "Pattern persistence checks in ensemble guidance",
      "Broad medium-range wet/dry signal comparison",
    ],
    interpretation: [
      "Best for pattern framing rather than day-specific rainfall timing.",
    ],
  },
  precip_16d_anom: {
    definition: "Sixteen-day accumulated precipitation anomaly relative to climatology.",
    bestFor: [
      "Extended GFS/GEFS/AIGFS anomaly comparison",
      "Longer-lead wet versus dry corridor screening",
      "Medium-range pattern trend checks",
    ],
    interpretation: [
      "Use as a broad anomaly signal, then tighten the read with shorter-lead deterministic guidance.",
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
  tornado_prob: {
    definition: "Official SPC tornado probability outlook layer.",
    bestFor: ["Official tornado-risk context in severe-weather setups"],
    interpretation: ["Use as official situational context alongside deterministic and mesoscale model guidance."],
  },
  wind_prob: {
    definition: "Official SPC wind probability outlook layer.",
    bestFor: ["Official damaging-wind risk context in severe-weather setups"],
    interpretation: ["Best used as official context before drilling into model kinematics and instability."],
  },
  hail_prob: {
    definition: "Official SPC hail probability outlook layer.",
    bestFor: ["Official hail-risk context in severe-weather setups"],
    interpretation: ["Best used as official context before drilling into instability, lapse rates, and storm mode."],
  },
  cpc_610_temp: {
    definition: "Official CPC 6-10 day temperature outlook for extended-range thermal planning.",
    bestFor: [
      "Extended temperature pattern screening",
      "Week-two warm and cold signal awareness",
      "Medium-range planning beyond deterministic model windows",
    ],
    interpretation: ["Official outlook product, not deterministic model output."],
  },
  cpc_610_precip: {
    definition: "Official CPC 6-10 day precipitation outlook for extended-range wet/dry planning.",
    bestFor: [
      "Extended precipitation pattern screening",
      "Week-two wet versus dry corridor awareness",
      "Medium-range event potential framing",
    ],
    interpretation: ["Official outlook product, not deterministic model output."],
  },
  cpc_814_temp: {
    definition: "Official CPC 8-14 day temperature outlook for longer-lead thermal planning.",
    bestFor: [
      "Longer-lead temperature pattern screening",
      "Extended warm and cold signal awareness",
      "Pattern persistence checks beyond week one",
    ],
    interpretation: ["Official outlook product with lower timing precision than short-range guidance."],
  },
  cpc_814_precip: {
    definition: "Official CPC 8-14 day precipitation outlook for longer-lead wet/dry planning.",
    bestFor: [
      "Longer-lead precipitation pattern screening",
      "Extended wet versus dry corridor awareness",
      "Broad event-potential framing",
    ],
    interpretation: ["Official outlook product with lower timing precision than short-range guidance."],
  },
  mrms_recent_precip_6h: {
    definition: "Observed MRMS six-hour recent precipitation accumulation.",
    bestFor: [
      "Very short-term observed rainfall totals",
      "Comparing recent observed totals against near-term guidance",
      "Fast situational awareness in active systems",
    ],
    interpretation: ["Observed recent precipitation, not a forecast field."],
  },
  mrms_recent_precip_24h: {
    definition: "Observed MRMS 24-hour recent precipitation accumulation.",
    bestFor: [
      "Day-scale observed rainfall totals",
      "Flood-sensitive recent-total screening",
      "Observed-versus-forecast comparison",
    ],
    interpretation: ["Observed recent precipitation, not a forecast field."],
  },
  mrms_recent_precip_72h: {
    definition: "Observed MRMS 72-hour recent precipitation accumulation.",
    bestFor: [
      "Multi-day observed rainfall totals",
      "Broader recent wet-corridor screening",
      "Event-total context before reading forecast guidance",
    ],
    interpretation: ["Observed recent precipitation, not a forecast field."],
  },
  ir13: {
    definition: "GOES-East Band 13 clean infrared imagery for cloud-top temperature and system organization.",
    bestFor: [
      "Cloud-top structure and convective cluster organization",
      "Large-scale system evolution context",
      "Satellite comparison against model cloud and precip fields",
    ],
    interpretation: ["Satellite observation rather than model-derived output."],
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

const GROUP_ORDER = [
  "SURFACE",
  "PRECIPITATION",
  "SEVERE",
  "UPPER AIR",
  "OUTLOOKS",
  "FORECASTS",
  "RADAR",
  "SATELLITE",
  "OBSERVATIONS",
] as const;

function groupSortKey(group: string): number {
  const index = GROUP_ORDER.indexOf(group as (typeof GROUP_ORDER)[number]);
  return index === -1 ? GROUP_ORDER.length : index;
}

function groupIcon(group: string) {
  if (group === "SURFACE" || group === "SEVERE") return <Flame className="h-5 w-5" />;
  if (group === "UPPER AIR") return <Wind className="h-5 w-5" />;
  if (group === "PRECIPITATION") return <Droplets className="h-5 w-5" />;
  if (group === "OUTLOOKS" || group === "FORECASTS") return <Layers3 className="h-5 w-5" />;
  if (group === "RADAR" || group === "SATELLITE" || group === "OBSERVATIONS") return <Snowflake className="h-5 w-5" />;
  return <Layers3 className="h-5 w-5" />;
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
        const group = viewerVariableGroup(varKey, variable.group);

        return {
          id: varKey,
          name: displayName,
          units: variable.units?.trim() || "Contextual units",
          group,
          models: Array.from(modelsByVariable.get(varKey) ?? []),
          order: variableCatalogOrder(varKey, variable.order),
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
                <PrefetchLink
                  to="/viewer"
                  className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.18)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
                >
                  Open Viewer
                  <ArrowRight className="h-4 w-4" />
                </PrefetchLink>
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
