/**
 * Legend presentation model — map viewer redesign Phase 7 (§7.1 / §11.3).
 *
 * One pure derivation drives every legend rendering: the full rail/popover
 * legend, the compact map-corner chip, and `/compare`. The backend still owns
 * exactly four raw kinds (`continuous | discrete | categorical | indexed`);
 * the three richer presentation modes — `ptype-intensity`, `radar-ptype` and
 * `composite-group` — are derived here from the raw kind plus the existing
 * `id` / `ptype_order` / `ptype_breaks` metadata and the composite layers the
 * App seam already has. No new backend kinds (§7.1).
 *
 * Numeric truth is normative (§7.1):
 *  - continuous ticks and ptype break values come only from metadata numbers;
 *  - a shared numeric scale is claimed *iff* every applicable ramp's numeric
 *    ladder is defined and identical — otherwise each ramp speaks its own
 *    min/max endpoints, because a single ladder would be a lie;
 *  - purely nominal categories invent nothing, and bare 1..n index codes are
 *    index codes, not measurements.
 *
 * This module is React-free and side-effect-free; the Phase 7 contract suite
 * (`tests/e2e/viewer-legend.spec.ts`) exercises it through the rendered DOM.
 */

export type LegendEntry = {
  value: number;
  color: string;
  label?: string;
};

export type LegendPayload = {
  title: string;
  units?: string;
  kind?: string;
  id?: string;
  note?: string;
  ptype_breaks?: Record<string, { offset: number; count: number }>;
  ptype_order?: string[];
  bins_per_ptype?: number;
  entries: LegendEntry[];
  opacity: number;
};

/** One component of a composite product, as the App seam already resolves it. */
export type CompositeLegendLayer = {
  id: string;
  label: string;
  legend: LegendPayload | null;
};

export type LegendTick = {
  value: number;
  label: string;
  offsetPercent: number;
};

export type LegendSwatchRow = {
  color: string;
  label: string;
  /** Present only when the metadata defines a meaningful numeric threshold. */
  value?: number;
};

export type LegendRamp = {
  key: string;
  label: string;
  colors: string[];
  values: number[];
};

export type LegendSharedScale = {
  values: number[];
  units: string;
};

export type SwatchMode = "discrete" | "categorical" | "indexed";
export type PtypeMode = "ptype-intensity" | "radar-ptype";

export type LegendPresentation =
  | {
      mode: "continuous";
      entries: LegendEntry[];
      gradientStops: string;
      ticks: LegendTick[];
      units?: string;
    }
  | { mode: SwatchMode; rows: LegendSwatchRow[]; units?: string }
  | { mode: PtypeMode; ramps: LegendRamp[]; sharedScale?: LegendSharedScale; units?: string }
  | {
      mode: "composite-group";
      groups: Array<{ layerLabel: string; presentation: LegendPresentation }>;
    };

const RADAR_GROUP_LABELS = ["Rain", "Snow", "Sleet", "Freezing Rain"];
const DEFAULT_PTYPE_ORDER = ["rain", "snow", "sleet", "frzr"];

export function formatValue(value: number): string {
  if (Number.isInteger(value)) return value.toString();
  if (Math.abs(value) < 0.1) return value.toFixed(2);
  return value.toFixed(1);
}

export function radarGroupLabelForCode(code: string, index: number): string {
  const normalized = code.toLowerCase();
  if (normalized === "rain") return "Rain";
  if (normalized === "snow") return "Snow";
  if (normalized === "sleet") return "Sleet";
  if (normalized === "ice") return "Ice";
  if (normalized === "frzr") return "Freezing Rain";
  return RADAR_GROUP_LABELS[index] ?? `Type ${index + 1}`;
}

export function isRadarPtypeLegend(legend: LegendPayload): boolean {
  const kind = legend.kind?.toLowerCase() ?? "";
  const id = legend.id?.toLowerCase() ?? "";
  return (
    kind.includes("radar_ptype") ||
    kind.includes("radar_ptype_combo") ||
    id.includes("radar") ||
    id === "radar_ptype"
  );
}

export function isPtypeIntensityLegend(legend: LegendPayload): boolean {
  return (legend.id?.toLowerCase() ?? "") === "ptype_intensity";
}

/**
 * Today's swatch-list branch, moved not changed: an explicit `categorical`
 * kind, or a fully labeled entry list (which no gradient can render).
 */
export function isSwatchLegend(legend: LegendPayload): boolean {
  const kind = legend.kind?.toLowerCase() ?? "";
  if (kind === "categorical") {
    return true;
  }
  return (
    legend.entries.length > 0 &&
    legend.entries.every((entry) => typeof entry.label === "string" && entry.label.trim().length > 0)
  );
}

export function sortLegendEntriesAscending(entries: LegendEntry[]): LegendEntry[] {
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      const byValue = left.entry.value - right.entry.value;
      return byValue !== 0 ? byValue : left.index - right.index;
    })
    .map(({ entry }) => entry);
}

function buildDenseLegendTicks(entries: LegendEntry[], targetCount = 6): number[] {
  if (entries.length === 0) return [];
  const lastIndex = entries.length - 1;
  return Array.from({ length: targetCount }, (_, index) => {
    const ratio = targetCount === 1 ? 0 : index / (targetCount - 1);
    return Math.round(ratio * lastIndex);
  }).filter((value, index, arr) => index === 0 || value !== arr[index - 1]);
}

function formatDenseTickValue(value: number): string {
  const absValue = Math.abs(value);
  if (absValue < 1) return formatValue(value);
  if (absValue >= 20) return formatValue(Math.round(value / 10) * 10);
  if (absValue >= 5) return formatValue(Math.round(value / 5) * 5);
  return formatValue(Math.round(value));
}

function buildContinuousPresentation(legend: LegendPayload): LegendPresentation {
  const entries = sortLegendEntriesAscending(legend.entries);
  const stopCount = Math.max(entries.length - 1, 1);
  const gradientStops = entries
    .map((entry, index) => `${entry.color} ${(index / stopCount) * 100}%`)
    .join(", ");
  const tickIndexes = buildDenseLegendTicks(entries, 6);
  const ticks = tickIndexes.map((index, position) => ({
    value: entries[index]!.value,
    // Endpoints anchor the range and must not round past the metadata
    // (a 0–75 ladder must not advertise "80"); interior ticks may round.
    label:
      position === 0 || position === tickIndexes.length - 1
        ? formatValue(entries[index]!.value)
        : formatDenseTickValue(entries[index]!.value),
    offsetPercent: stopCount === 0 ? 0 : (index / stopCount) * 100,
  }));
  return { mode: "continuous", entries, gradientStops, ticks, units: legend.units };
}

/** Bare 0..n or 1..n integer codes carry no measurement (§7.1). */
function isBareIndexSequence(values: number[]): boolean {
  if (values.length < 2) return false;
  if (!values.every((value) => Number.isInteger(value))) return false;
  const start = values[0];
  if (start !== 0 && start !== 1) return false;
  return values.every((value, index) => value === start + index);
}

function buildSwatchPresentation(legend: LegendPayload, mode: SwatchMode): LegendPresentation {
  const values = legend.entries.map((entry) => Number(entry.value));
  const nominal = mode === "categorical" || isBareIndexSequence(values);
  const rows: LegendSwatchRow[] = legend.entries
    .slice()
    // Highest class first — the reading order the legend has always used.
    .reverse()
    .map((entry) => {
      const label = entry.label?.trim();
      const numeric = !nominal && Number.isFinite(entry.value);
      const labelCarriesNumbers = Boolean(label && /\d/.test(label));
      return {
        color: entry.color,
        label: label || (nominal ? "" : formatValue(entry.value)),
        value: numeric && label && !labelCarriesNumbers ? entry.value : undefined,
      };
    })
    .filter((row) => row.label.length > 0 || row.value !== undefined);
  return { mode, rows, units: legend.units };
}

/** Ramps from `ptype_breaks` offsets, in `ptype_order`; every ramp is kept. */
function rampsFromBreaks(legend: LegendPayload): LegendRamp[] {
  const breaks = legend.ptype_breaks;
  if (!breaks) return [];
  const order = (Array.isArray(legend.ptype_order) && legend.ptype_order.length > 0
    ? legend.ptype_order
    : DEFAULT_PTYPE_ORDER
  ).filter((ptype) => breaks[ptype]);

  const ramps: LegendRamp[] = [];
  order.forEach((ptype, index) => {
    const boundary = breaks[ptype];
    if (!boundary) return;
    const offset = Number(boundary.offset);
    const count = Number(boundary.count);
    if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) return;
    const segment = legend.entries.slice(offset, offset + count);
    const colors = segment.map((entry) => entry.color).filter(Boolean);
    if (colors.length === 0) return;
    ramps.push({
      key: ptype,
      label: radarGroupLabelForCode(ptype, index),
      colors,
      values: segment.map((entry) => Number(entry.value)).filter(Number.isFinite),
    });
  });
  return ramps;
}

/**
 * Fallback for radar sidecars that predate `ptype_breaks`: split the flat
 * sequence on zero-value delimiters, in native order.
 */
function rampsFromZeroDelimiters(legend: LegendPayload): LegendRamp[] {
  const ramps: LegendRamp[] = [];
  let current: LegendEntry[] = [];
  const flush = () => {
    if (current.length === 0) return;
    const index = ramps.length;
    ramps.push({
      key: DEFAULT_PTYPE_ORDER[index] ?? `type-${index + 1}`,
      label: RADAR_GROUP_LABELS[index] ?? `Type ${index + 1}`,
      colors: current.map((entry) => entry.color).filter(Boolean),
      values: current.map((entry) => Number(entry.value)).filter(Number.isFinite),
    });
    current = [];
  };
  for (const entry of legend.entries) {
    if (Math.abs(entry.value) < 1e-9) {
      flush();
      continue;
    }
    current.push(entry);
  }
  flush();
  return ramps.filter((ramp) => ramp.colors.length > 0);
}

function sameLadder(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

/**
 * §7.2: the shared numeric break scale is rendered once — but only when the
 * ramps genuinely share one. Real MRMS radar-ptype ladders diverge (rain
 * 5–70 dBZ, the other types 0–60), so those get per-ramp endpoints instead.
 */
export function resolveSharedScale(ramps: LegendRamp[], units?: string): LegendSharedScale | undefined {
  if (ramps.length < 2) return undefined;
  if (ramps.some((ramp) => ramp.values.length === 0)) return undefined;
  const first = ramps[0].values;
  if (!ramps.every((ramp) => sameLadder(ramp.values, first))) return undefined;
  return { values: [...first], units: (units ?? "").trim() };
}

function buildPtypePresentation(legend: LegendPayload, mode: PtypeMode): LegendPresentation | null {
  const ramps = rampsFromBreaks(legend);
  // The zero-delimiter fallback invents ramp identities (labels from
  // DEFAULT_PTYPE_ORDER, first entry eaten as a delimiter), which is only
  // defensible when the split actually reveals multiple packed ramps. A
  // single-ramp result means this is really one continuous ladder — e.g. the
  // HRRR/NAM per-type `radar_ptype_snow` variables, whose `radar` id lands
  // here but whose metadata defines no grouping — and it must fall through to
  // the continuous rendering instead of showing a fabricated "Rain 1.2–75".
  const fallback = mode === "radar-ptype" ? rampsFromZeroDelimiters(legend) : [];
  const resolved = ramps.length > 0 ? ramps : fallback.length >= 2 ? fallback : [];
  if (resolved.length === 0) return null;
  return {
    mode,
    ramps: resolved,
    sharedScale: resolveSharedScale(resolved, legend.units),
    units: legend.units,
  };
}

function resolveSingleLegend(legend: LegendPayload): LegendPresentation | null {
  if (!Array.isArray(legend.entries) || legend.entries.length === 0) {
    return null;
  }

  if (isPtypeIntensityLegend(legend)) {
    const presentation = buildPtypePresentation(legend, "ptype-intensity");
    if (presentation) return presentation;
  }
  if (isRadarPtypeLegend(legend)) {
    const presentation = buildPtypePresentation(legend, "radar-ptype");
    if (presentation) return presentation;
  }

  if (isSwatchLegend(legend)) {
    const kind = legend.kind?.toLowerCase() ?? "";
    const mode: SwatchMode = kind === "discrete" || kind === "indexed" ? kind : "categorical";
    return buildSwatchPresentation(legend, mode);
  }

  return buildContinuousPresentation(legend);
}

/**
 * Resolve the presentation for a legend, optionally grouped by the component
 * layers of a composite product. Composite grouping uses only legend data the
 * App seam already holds (§7.1 decision 5) — it never triggers a fetch.
 */
export function resolveLegendPresentation(
  legend: LegendPayload | null | undefined,
  compositeLayers?: CompositeLegendLayer[] | null,
): LegendPresentation | null {
  const groups = (compositeLayers ?? [])
    .map((layer) => ({
      layerLabel: layer.label,
      presentation: layer.legend ? resolveSingleLegend(layer.legend) : null,
    }))
    .filter(
      (group): group is { layerLabel: string; presentation: LegendPresentation } =>
        group.presentation !== null,
    );

  // A single component is just that component's legend — grouping one row
  // would be chrome without information.
  if (groups.length >= 2) {
    return { mode: "composite-group", groups };
  }

  if (!legend) return null;
  return resolveSingleLegend(legend);
}

/** Truthful compact summary for the swatch kinds (§7.2). */
export function swatchSummaryLabel(mode: SwatchMode, count: number): string {
  const noun = mode === "categorical" ? "categories" : "levels";
  return `${count} ${noun}`;
}

/** Endpoint string for one ramp, e.g. `5–70 dBZ`. */
export function rampEndpointsLabel(ramp: LegendRamp, units?: string): string | null {
  if (ramp.values.length === 0) return null;
  const min = ramp.values[0];
  const max = ramp.values[ramp.values.length - 1];
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  const suffix = (units ?? "").trim();
  return `${formatValue(min)}–${formatValue(max)}${suffix ? ` ${suffix}` : ""}`;
}

/** Ladder endpoints/mid-stops for the shared scale row (rendered once). */
export function sharedScaleTicks(scale: LegendSharedScale, targetCount = 5): number[] {
  const values = scale.values;
  if (values.length <= targetCount) return [...values];
  const lastIndex = values.length - 1;
  const indices = Array.from({ length: targetCount }, (_, index) =>
    Math.round((index / (targetCount - 1)) * lastIndex),
  ).filter((value, index, arr) => index === 0 || value !== arr[index - 1]);
  return indices.map((index) => values[index]);
}
