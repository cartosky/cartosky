import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

function GlassCard({
  title,
  desc,
  children,
  right,
}: {
  title: string;
  desc?: string;
  children?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/25 backdrop-blur-xl shadow-[0_10px_30px_rgba(0,0,0,0.35)]">
      <div className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-white">{title}</div>
            {desc ? <div className="mt-1 text-sm text-white/65">{desc}</div> : null}
          </div>
          {right ? <div className="shrink-0">{right}</div> : null}
        </div>
        {children ? <div className="mt-4">{children}</div> : null}
      </div>
    </div>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/70">
      {children}
    </div>
  );
}

function Section({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="space-y-2">
      <div className="text-[11px] uppercase tracking-wider text-white/55">{label}</div>
      <ul className="space-y-1.5 text-sm text-white/80">
        {items.map((t) => (
          <li key={t} className="flex gap-2">
            <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-white/35" />
            <span>{t}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type VariableDef = {
  id: string;
  name: string;
  oneLiner: string;
  pills: string[];
  definition: string;
  bestFor: string[];
  interpretation: string[];
  limitations?: string[];
  notes?: string[];
};

export default function Variables() {
  const [openId, setOpenId] = useState<string>("tmp2m");

  const variables: VariableDef[] = useMemo(
    () => [
      {
        id: "tmp2m",
        name: "Surface Temp",
        oneLiner: "Air temperature at ~2 meters above ground level.",
        pills: ["2m AGL", "°F/°C", "Continuous"],
        definition:
          "2m temperature represents near-surface air temperature, commonly used for impacts, thermal gradients, and boundary placement.",
        bestFor: [
          "Thermal gradients and boundary location",
          "Air mass identification",
          "Surface impact timing (freeze line, melting line context)",
        ],
        interpretation: [
          "2m temperature is not pavement temperature and can lag/lead in shallow cold pools.",
          "Compare with dew point and wind to understand mixing and temperature recovery.",
        ],
        limitations: ["Local microclimates and terrain effects may not be captured at coarser resolution."],
      },
      {
        id: "td2m",
        name: "Surface Dew Point",
        oneLiner: "Near-surface moisture proxy; strongly tied to instability and fog/stratus potential.",
        pills: ["2m AGL", "°F/°C", "Continuous"],
        definition:
          "2m dew point represents the moisture content of the near-surface air mass and is a key driver of instability, cloud bases, and low-level saturation.",
        bestFor: [
          "Moisture advection and dryline placement",
          "Assessing low-level saturation (fog/stratus potential)",
          "Context for instability (in combination with temperature)",
        ],
        interpretation: [
          "Sharp dew point gradients often mark boundaries even when temperature gradients are weak.",
          "A rising dew point with increasing wind often signals effective moisture transport.",
        ],
        limitations: ["Shallow mixing and surface flux biases can shift dew points by a few degrees."],
      },
      {
        id: "wspd850",
        name: "850mb Heights + Winds",
        oneLiner: "850 mb wind speed shaded beneath 850 mb height contours for low-level jet and warm-advection pattern diagnosis.",
        pills: ["850 mb", "mph + m", "Continuous + contours"],
        definition:
          "This product shades 850 mb wind speed while overlaying 850 mb geopotential height contours. Together they help diagnose low-level jet structure, warm-advection corridors, moisture transport, and frontal-zone evolution.",
        bestFor: [
          "Finding low-level jet cores and stronger warm-advection corridors",
          "Comparing frontal-zone placement with stronger low-level flow",
          "Tracking moisture-transport pathways into severe or heavy-rain setups",
        ],
        interpretation: [
          "Use the wind-speed shading to find stronger low-level flow maxima, then use the height contours to place that flow within the broader synoptic pattern.",
          "A stronger 850 mb wind maximum matters most where it overlaps moisture and instability, so pair it with dew point, CAPE, and precipitation fields rather than using it alone.",
        ],
        limitations: [
          "This is an above-ground pressure-level product, so local terrain effects and shallow surface decoupling are not represented directly.",
          "Wind speed alone does not show directional shear or exact moisture quality, so additional fields are still needed for full low-level kinematic analysis.",
        ],
        notes: ["Current implementation uses 30 m height contours over the 850 mb wind-speed fill."],
      },
      {
        id: "wspd10m",
        name: "10m Wind Speed",
        oneLiner: "Sustained wind speed at 10 meters above ground.",
        pills: ["10m AGL", "mph/kt", "Continuous"],
        definition:
          "10m wind speed represents sustained near-surface wind magnitude. Useful for gradient winds, mixing regimes, and impact planning.",
        bestFor: [
          "Gradient wind events and wind advisories context",
          "Identifying wind maxima in tight pressure gradients",
          "Blowing snow potential (with snow cover + temps)",
        ],
        interpretation: [
          "Model 10m winds depend on boundary layer scheme and surface roughness assumptions.",
          "Compare with gusts to gauge mixing and turbulence potential.",
        ],
        limitations: ["Local terrain/channeling and urban roughness may be under-resolved."],
      },
      {
        id: "wgust10m",
        name: "10m Wind Gusts",
        oneLiner: "Peak gust potential at 10 meters above ground.",
        pills: ["10m AGL", "mph/kt", "Continuous"],
        definition:
          "10m wind gust is a modeled estimate of peak gusts driven by turbulence/mixing and momentum transfer. It often highlights impact potential better than sustained wind alone.",
        bestFor: [
          "Impact-level wind potential (trees, power, travel)",
          "Identifying corridor/gradient maxima",
          "Assessing mixing behind fronts or within dry slots",
        ],
        interpretation: [
          "Gust algorithms vary; treat as guidance, not a guarantee.",
          "High gusts often correlate with steep low-level lapse rates and strong flow aloft.",
        ],
        limitations: ["Convective gusts and downbursts are not reliably captured outside convection-permitting scenarios."],
      },
      {
        id: "precip_ptype",
        name: "Precip Type & Intensity",
        oneLiner: "Precipitation type categories combined with intensity bins for quick winter-weather diagnosis.",
        pills: ["Derived", "ptype + intensity", "Categorical"],
        definition:
          "This product blends modeled precipitation type with intensity classes so you can quickly see not just what is falling, but how strongly the model is producing it.",
        bestFor: [
          "Quick winter-weather overview without interpreting multiple separate fields",
          "Finding transitions between rain, snow, sleet, and freezing rain",
          "Spotting where modeled precip rates intensify within the precip shield",
        ],
        interpretation: [
          "Treat type boundaries as approximate because shallow thermal-profile changes can shift category edges quickly.",
          "Use this as a fast situational-awareness field, then sanity-check with temperature profiles and snowfall/QPF products.",
        ],
        limitations: [
          "Categorical p-type output can look noisy around marginal thermal zones.",
          "Intensity buckets are model-derived guidance, not direct observed precipitation rates.",
        ],
        notes: ["Categorical precipitation products should stay nearest-neighbor when resampled so class edges remain crisp."],
      },
      {
        id: "refl_ptype",
        name: "Composite Reflectivity + Ptype",
        oneLiner: "Simulated composite reflectivity with a precipitation-type overlay.",
        pills: ["Derived", "dBZ + classes", "Categorical overlay"],
        definition:
          "Composite reflectivity is the maximum modeled reflectivity through the vertical column. P-type is a categorical classification (rain/snow/mix/ice-type depending on model) rendered as discrete classes.",
        bestFor: [
          "Quick convective mode/coverage overview",
          "Identifying frontal structure and precip shields",
          "Contextualizing where precip is likely falling as snow vs rain",
        ],
        interpretation: [
          "This is not NEXRAD radar; it is model-simulated reflectivity.",
          "Treat P-type boundaries as approximate—small changes in thermal profile can shift class edges.",
        ],
        limitations: [
          "Reflectivity often over/under-does fine storm structure depending on microphysics/parameterization.",
          "P-type is especially sensitive near 31–34°F and in warm-nose setups.",
        ],
        notes: ["Categorical overlays should remain crisp; resampling should be nearest-neighbor."],
      },
      {
        id: "qpf",
        name: "Total Precip (QPF)",
        oneLiner: "Accumulated liquid-equivalent precipitation over the forecast period.",
        pills: ["Accumulation", "in/mm", "Continuous"],
        definition:
          "QPF is the model’s accumulated precipitation total over a given time window. It represents liquid-equivalent amount, not snow depth.",
        bestFor: [
          "Precipitation axis placement and totals",
          "Comparing storm-to-storm consistency across runs",
          "Downstream derived products (snow via SLR assumptions)",
        ],
        interpretation: [
          "Accumulations depend heavily on convective/microphysics schemes—expect volatility in convective regimes.",
          "Always evaluate in context of forcing and thermal structure.",
        ],
        limitations: ["Convective feedback and parameterization can create local QPF maxima that shift run-to-run."],
      },
      {
        id: "snow10to1",
        name: "Total Snowfall (10:1)",
        oneLiner: "Snow accumulation derived from QPF using a fixed 10:1 snow-liquid ratio.",
        pills: ["Derived", "10:1 SLR", "Accumulation"],
        definition:
          "A simple derived snow product: total QPF multiplied by a fixed 10:1 snow-to-liquid ratio. Useful for baseline comparison, not a final call.",
        bestFor: [
          "Quick first-pass snow potential",
          "Comparing storm track shifts via accumulation gradients",
          "Baseline mapping when dynamic SLR isn’t available",
        ],
        interpretation: [
          "10:1 is a convenience, not physics—real SLR varies widely by dendritic growth zone, lift, and surface temps.",
          "Use 850 mb temperature + surface temperature to sanity-check rain/snow cutoff zones.",
        ],
        limitations: [
          "Overestimates in wet/warm profiles; underestimates in cold/fluffy profiles.",
          "Does not account for compaction, melting, or sleet/freezing rain.",
        ],
      },
      {
        id: "snowkuchera",
        name: "Total Snowfall (Kuchera)",
        oneLiner: "Snow accumulation derived from QPF using a temperature-dependent Kuchera snow-liquid ratio.",
        pills: ["Derived", "Kuchera SLR", "Accumulation"],
        definition:
          "A derived snow product that applies a variable snow-liquid ratio based on the modeled thermal profile rather than a fixed 10:1 assumption. It is meant to better approximate wetter vs fluffier snow setups.",
        bestFor: [
          "Comparing realistic snowfall potential across warmer and colder profiles",
          "Highlighting where fixed 10:1 may be too low or too high",
          "Storm-total snow mapping when profile-dependent SLR matters",
        ],
        interpretation: [
          "Kuchera generally increases totals in colder/fluffier setups and lowers them in wetter/heavier snow regimes.",
          "It is still a derived field from modeled QPF and temperature structure, not a direct forecast of observed snow depth.",
        ],
        limitations: [
          "Still depends on underlying QPF accuracy, so precip placement errors carry directly into the snowfall map.",
          "Does not fully capture compaction, melting on contact, sleet contamination, or marginal boundary-layer issues.",
        ],
        notes: [
          "Usually more realistic than 10:1 for broad-brush snowfall, but it should still be sanity-checked against surface temperatures and p-type.",
        ],
      },
      {
        id: "pwat",
        name: "Precipitable Water",
        oneLiner: "Total column moisture, expressed as the liquid water depth that would result if all vapor condensed.",
        pills: ["Entire column", "in/mm", "Continuous"],
        definition:
          "Precipitable water measures the integrated moisture content through the atmospheric column. Higher values generally support heavier rain rates and more efficient warm-rain processes when lift and instability are present.",
        bestFor: [
          "Finding deep moisture plumes feeding heavy rain or tropical air masses",
          "Identifying moisture gradients along drylines, fronts, and atmospheric-river style corridors",
          "Comparing whether a setup is moisture-limited or primed for efficient rainfall production",
        ],
        interpretation: [
          "PWAT is a moisture field, not a rainfall forecast, so it needs forcing and storm coverage context before implying totals or flash-flood risk.",
          "Anomalously high PWAT usually matters more than the raw value alone because climatologically impressive moisture varies by region and season.",
        ],
        limitations: [
          "High PWAT can coexist with weak lift or strong capping, producing little precipitation despite a moisture-rich column.",
          "It does not tell you where within the column the moisture is concentrated, so pair it with sounding-level fields when p-type or cloud-depth structure matters.",
        ],
        notes: [
          "Rendered with an inches-based palette similar to the classic PWAT ramp so low-end dry air stays muted and richer tropical moisture stands out quickly.",
        ],
      },
      {
        id: "mucape",
        name: "Most-Unstable CAPE",
        oneLiner: "Buoyant energy for the most unstable parcel in the lower troposphere, highlighting elevated or concentrated instability reservoirs.",
        pills: ["255-0 mb layer", "J/kg", "Continuous"],
        definition:
          "Most-unstable CAPE estimates the positive buoyant energy associated with the most unstable parcel found within the lower troposphere. It is useful when instability is not well represented by a simple mixed boundary-layer parcel, especially in elevated setups.",
        bestFor: [
          "Identifying elevated instability above shallow stable layers",
          "Comparing whether severe potential is rooted near the surface or in a deeper unstable layer",
          "Highlighting environments where nocturnal or elevated convection can persist",
        ],
        interpretation: [
          "MUCAPE often exceeds MLCAPE in elevated regimes, so treat it as a signal of available instability rather than guaranteed surface-based storm intensity.",
          "Compare MUCAPE with MLCAPE, surface-based fields, inhibition, and forcing to understand storm rooting depth.",
        ],
        limitations: [
          "Large MUCAPE can occur in strongly capped or elevated environments where storms never ingest that parcel source region efficiently.",
          "Different models may define the sampled unstable layer differently enough that direct run-to-run comparisons still need caution.",
        ],
        notes: [
          "Uses the same CAPE color ramp as MLCAPE so threshold interpretation stays visually consistent across instability variants.",
        ],
      },
      {
        id: "mlcape",
        name: "Mixed-Layer CAPE",
        oneLiner: "Buoyant energy available to a mixed boundary-layer parcel, used to gauge convective instability.",
        pills: ["90-0 mb mixed layer", "J/kg", "Continuous"],
        definition:
          "Mixed-layer CAPE estimates the positive buoyant energy a representative mixed parcel in the lowest part of the atmosphere would have if lifted. It is one of the cleaner broad-brush instability fields for warm-season convective setups.",
        bestFor: [
          "Locating corridors of greater convective instability",
          "Comparing overlap of moisture, heating, and forcing before severe storms",
          "Filtering environments where storm coverage could rapidly increase if inhibition weakens",
        ],
        interpretation: [
          "Higher CAPE does not guarantee storms or severity; shear, forcing, inhibition, and storm mode still matter.",
          "Use CAPE with dew point, lapse rates, hodographs, and convective initiation signals rather than in isolation.",
        ],
        limitations: [
          "CAPE can look impressive in capped regimes where storms never initiate.",
          "Coarser guidance may smooth narrow instability axes or under-resolve outflow and mesoscale boundaries.",
        ],
        notes: [
          "Displayed as a continuous field, but the legend should still emphasize standard severe-weather threshold bands.",
        ],
      },
      {
        id: "sbcape",
        name: "Surface-Based CAPE",
        oneLiner: "Buoyant energy for a surface parcel, emphasizing instability that is directly rooted at the ground.",
        pills: ["Surface parcel", "J/kg", "Continuous"],
        definition:
          "Surface-based CAPE estimates the positive buoyant energy a parcel lifted directly from the surface would have. It is the most direct CAPE flavor for environments where storms are expected to ingest near-surface air instead of elevated source layers.",
        bestFor: [
          "Comparing whether instability is actually surface-rooted instead of elevated",
          "Highlighting warm-sector environments where surface heating and moisture can support surface-based convection",
          "Cross-checking MLCAPE and MUCAPE when assessing severe-weather potential tied to the boundary layer",
        ],
        interpretation: [
          "SBCAPE is usually the most relevant CAPE flavor when storms are surface-based, but it still needs forcing, inhibition, and shear context.",
          "Large SBCAPE alone does not guarantee initiation if the cap holds or lift never materializes.",
        ],
        limitations: [
          "Surface parcel fields can be noisy or overly sensitive to shallow temperature and dew point biases near the ground.",
          "Elevated convection can still thrive when SBCAPE is modest but MUCAPE remains large above a stable surface layer.",
        ],
        notes: [
          "Uses the same CAPE color ramp as MLCAPE and MUCAPE so instability thresholds stay visually consistent across parcel choices.",
        ],
      },
      {
        id: "vort500",
        name: "500mb Heights + Vorticity",
        oneLiner: "500 mb absolute vorticity shaded beneath 500 mb height contours for synoptic-scale pattern diagnosis.",
        pills: ["500 mb", "10^-5 s^-1 + m", "Continuous + contours"],
        definition:
          "This product shades 500 mb absolute vorticity while overlaying 500 mb geopotential height contours. Together they help diagnose troughs, shortwaves, vort maxima, and the broader mid-level pattern driving ascent and storm evolution.",
        bestFor: [
          "Tracking shortwaves and mid-level energy maxima",
          "Comparing trough/ridge structure and timing",
          "Finding areas where stronger mid-level forcing overlaps moisture and instability",
        ],
        interpretation: [
          "Use the vorticity shading to find compact lobes of stronger spin, then use the height contours to place them within the larger synoptic pattern.",
          "A strong vort max matters most where it is translating into downstream ascent, so pair it with moisture and instability fields rather than treating it in isolation.",
        ],
        limitations: [
          "This is a pressure-level field, so terrain-intersecting areas and very small-scale features are not the focus of the product.",
          "Absolute vorticity includes the Coriolis contribution, so values are not directly interchangeable with a relative-vorticity display from another source.",
        ],
        notes: ["Current implementation uses 60 m height contours over the vorticity fill."],
      },
    ],
    []
  );

  return (
    <div className="space-y-14">
      {/* HERO */}
      <section className="pt-6 md:pt-10">
        <div className="max-w-3xl">
          <h1 className="text-5xl md:text-6xl font-semibold tracking-tight leading-[1.02]">
            Variables,
            <br />
            <span className="text-[#577361]">Precisely Described.</span>
          </h1>

          <p className="mt-4 text-base md:text-lg text-white/70">
            Supported products with units, definitions, and interpretation notes. Designed for fast scanning,
            with optional detail when you need it.
          </p>

          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              to="/viewer"
              className="rounded-lg bg-[linear-gradient(to_top_right,#1f342f_0%,#526d5c_100%)] px-4 py-2.5 text-sm font-medium text-white border border-white/20 shadow-[0_8px_18px_rgba(0,0,0,0.28)] transition-all duration-150 hover:brightness-110"
            >
              Launch Viewer
            </Link>
            <Link
              to="/models"
              className="rounded-lg bg-black/20 px-4 py-2.5 text-sm font-medium text-white hover:bg-black/30 border border-white/15"
            >
              Explore Models
            </Link>
          </div>
        </div>
      </section>

      {/* VARIABLE LIST */}
      <section className="space-y-4">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-xs uppercase tracking-wider text-white/60">Current library</div>
            <h2 className="mt-2 text-2xl md:text-3xl font-semibold tracking-tight text-white">
              Variables
            </h2>
          </div>

          <div className="text-xs text-white/55">
            Adding more soon — focus is correctness + performance.
          </div>
        </div>

        <div className="space-y-4">
          {variables.map((v) => {
            const isOpen = openId === v.id;

            return (
              <GlassCard
                key={v.id}
                title={v.name}
                desc={v.oneLiner}
                right={
                  <button
                    type="button"
                    onClick={() => setOpenId((prev) => (prev === v.id ? "" : v.id))}
                    className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs font-medium text-white/80 hover:bg-white/10 transition-colors"
                    aria-expanded={isOpen}
                    aria-controls={`var-${v.id}-details`}
                  >
                    {isOpen ? "Hide details" : "Show details"}
                  </button>
                }
              >
                {/* pills */}
                <div className="flex flex-wrap gap-2">
                  {v.pills.map((p) => (
                    <Pill key={p}>{p}</Pill>
                  ))}
                </div>

                {/* details */}
                {isOpen ? (
                  <div
                    id={`var-${v.id}-details`}
                    className="mt-4 rounded-xl border border-white/10 bg-white/5 p-4 space-y-5"
                  >
                    <div className="space-y-2">
                      <div className="text-[11px] uppercase tracking-wider text-white/55">Definition</div>
                      <div className="text-sm text-white/80 leading-relaxed">{v.definition}</div>
                    </div>

                    <div className="grid gap-6 md:grid-cols-2">
                      <Section label="Best for" items={v.bestFor} />
                      <Section label="Interpretation" items={v.interpretation} />
                    </div>

                    {v.limitations?.length ? (
                      <>
                        <div className="h-px bg-white/10" />
                        <Section label="Limitations" items={v.limitations} />
                      </>
                    ) : null}

                    {v.notes?.length ? (
                      <>
                        <div className="h-px bg-white/10" />
                        <Section label="Notes" items={v.notes} />
                      </>
                    ) : null}
                  </div>
                ) : null}
              </GlassCard>
            );
          })}
        </div>
      </section>

      {/* ROADMAP */}
      <section className="space-y-4">
        <GlassCard title="Roadmap" desc="Likely additions as the catalog grows.">
          <div className="space-y-3 text-sm text-white/75">
            <div className="text-[11px] uppercase tracking-wider text-white/55">Candidates</div>
            <div className="text-white/80">
              MSLP, 500 mb heights, CIN and additional CAPE variants, 700 mb RH, visibility, freezing rain accretion (where supported),
              and additional precip-type variants.
            </div>
          </div>
        </GlassCard>
      </section>

      {/* FOOTER TRUST ROW */}
      <section className="pt-2">
        <div className="flex flex-wrap items-center gap-6 text-xs text-white/55">
          <span>Clean legends</span>
          <span>•</span>
          <span>Correct resampling</span>
          <span>•</span>
          <span>Fast animation</span>
        </div>
      </section>
    </div>
  );
}
