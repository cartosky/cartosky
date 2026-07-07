import type { LegendPayload } from "@/components/map-legend";
import { BRAND_LOGO_SRC } from "@/lib/branding";
import type { TimeAxisMode } from "@/lib/time-axis";
import { formatObservedCompactTime, formatObservedValidTime, validAxisLabel } from "@/lib/time-axis";

export type ScreenshotExportState = {
  /** Unused by the compose-only exporter; kept optional for legacy callers. */
  style?: any;
  center: [number, number];
  zoom: number;
  bearing?: number;
  pitch?: number;
  basemapMode?: "light" | "dark";
  viewportWidth?: number;
  viewportHeight?: number;
  isMobile: boolean;
  model: string;
  run: string;
  /**
   * Optional replacement for the default `{run} {model}` prefix on overlay
   * line 1. Compare screenshots set this to a per-side run+model title
   * (e.g. "Compare: 12Z 7/7 GEFS vs 00Z 7/7 EPS") because a single
   * `run`/`model` pair cannot represent two panels with different runs.
   */
  overlayTitle?: string;
  variable: { key: string; label: string };
  fh: number;
  /** Unused by the compose-only exporter; kept optional for legacy callers. */
  gridReady?: boolean;
  timeAxisMode?: TimeAxisMode;
  runTimeISO?: string | null;
  validTimeISO?: string | null;
  cpcValidSeas?: string | null;
  cpcValidEnd?: string | null;
  sourceStatusLabel?: string | null;
  region?: { id: string; label: string };
  animationEnabled: boolean;
  capturedMapDataUrl?: string;
};

export type ScreenshotExportOptions = {
  width?: number;
  height?: number;
  pixelRatio?: number;
  legend?: LegendPayload | null;
  overlayLines?: string[];
};

export type ViewerScreenshotReadiness = {
  mapIdle: boolean;
  gridFrameReady: boolean;
  rgbFrameReady: boolean;
  selectionSupportsGrid: boolean;
  selectionSupportsRasterRgb: boolean;
};

export function isViewerScreenshotReady(readiness: ViewerScreenshotReadiness): boolean {
  if (!readiness.mapIdle) {
    return false;
  }
  if (readiness.selectionSupportsGrid) {
    return readiness.gridFrameReady;
  }
  if (readiness.selectionSupportsRasterRgb) {
    return readiness.rgbFrameReady;
  }
  return true;
}

const NORMALIZED_OUTPUT_WIDTH = 1280;
const PORTRAIT_OUTPUT_WIDTH = 720;
const DEFAULT_WIDTH = NORMALIZED_OUTPUT_WIDTH;
const DEFAULT_HEIGHT = Math.round(NORMALIZED_OUTPUT_WIDTH * 9 / 16);
const DEFAULT_PIXEL_RATIO = 2;
const IMAGE_LOAD_TIMEOUT_MS = 5_000;
const SCREENSHOT_LOGO_SRC = BRAND_LOGO_SRC;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    let done = false;
    const timeoutId = window.setTimeout(() => {
      if (done) return;
      done = true;
      image.onload = null;
      image.onerror = null;
      reject(new Error(`Timed out while loading image: ${src}`));
    }, IMAGE_LOAD_TIMEOUT_MS);

    image.decoding = "async";
    image.onload = () => {
      if (done) return;
      done = true;
      window.clearTimeout(timeoutId);
      resolve(image);
    };
    image.onerror = () => {
      if (done) return;
      done = true;
      window.clearTimeout(timeoutId);
      reject(new Error(`Failed to load image: ${src}`));
    };
    image.src = src;
  });
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("Failed to encode screenshot PNG."));
          return;
        }
        resolve(blob);
      },
      "image/png",
      1
    );
  });
}

// GIF encoding quantizes the soft card shadows into visible dark banding (a
// smudgy halo around the overlay/logo/legend), so the GIF compose flattens
// them. Module-level because the card helpers are called deep inside the
// legend/overlay draw stack; composeShareFrame sets/restores it per frame.
let chromeShadowsEnabled = true;

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
): void {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function buildCpcValidLine(state: ScreenshotExportState): string | null {
  // Prefer valid_seas (monthly/seasonal products)
  const seas = (state.cpcValidSeas ?? "").trim();
  if (seas) {
    const CPC_SEASON_CODES: Record<string, string> = {
      DJF: "Dec-Jan-Feb", JFM: "Jan-Feb-Mar", FMA: "Feb-Mar-Apr",
      MAM: "Mar-Apr-May", AMJ: "Apr-May-Jun", MJJ: "May-Jun-Jul",
      JJA: "Jun-Jul-Aug", JAS: "Jul-Aug-Sep", ASO: "Aug-Sep-Oct",
      SON: "Sep-Oct-Nov", OND: "Oct-Nov-Dec", NDJ: "Nov-Dec-Jan",
    };
    const expanded = seas.replace(/^([A-Z]{3,})(\s+\d{4})$/i, (_, codes, year) => {
      const upper = codes.trim().toUpperCase();
      return (CPC_SEASON_CODES[upper] ?? upper) + year;
    });
    return `Valid: ${expanded}`;
  }

  // Fall back to valid_start / valid_end date range (6-10, 8-14, W3-4)
  const start = state.validTimeISO ? new Date(state.validTimeISO) : null;
  const end = state.cpcValidEnd ? new Date(state.cpcValidEnd) : null;
  if (!start || !end || Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return null;
  }
  if (start.getUTCFullYear() === end.getUTCFullYear() && start.getUTCMonth() === end.getUTCMonth()) {
    const month = new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" }).format(start);
    return `Valid: ${month} ${start.getUTCDate()}–${end.getUTCDate()}, ${start.getUTCFullYear()}`;
  }
  const fmt = (d: Date) =>
    new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(d);
  const startCompact = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" }).format(start);
  return start.getUTCFullYear() === end.getUTCFullYear()
    ? `Valid: ${startCompact} – ${fmt(end)}`
    : `Valid: ${fmt(start)} – ${fmt(end)}`;
}

/** Valid time as `h:MM AM/PM M/DD/YY` in the viewer's local timezone
 * (share overlay line 1; local Date getters pick up the user's tz). */
function formatShareOverlayTime(validTimeISO: string | null | undefined): string | null {
  if (!validTimeISO) {
    return null;
  }
  const parsed = new Date(validTimeISO);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  const hour12 = parsed.getHours() % 12 === 0 ? 12 : parsed.getHours() % 12;
  const meridiem = parsed.getHours() < 12 ? "AM" : "PM";
  const date = `${parsed.getMonth() + 1}/${pad(parsed.getDate())}/${String(parsed.getFullYear()).slice(-2)}`;
  return `${hour12}:${pad(parsed.getMinutes())} ${meridiem} ${date}`;
}

function defaultOverlayLines(state: ScreenshotExportState, legend?: LegendPayload | null): string[] {
  const model = state.model.trim() || "Model";
  // The viewer's run selector labels the newest run "Latest (00Z 7/07)" —
  // shares show just the run id.
  const run = (state.run.trim() || "Run").replace(/^Latest\s*\((.+)\)$/i, "$1");
  const baseVariableLabel = state.variable.label.trim() || state.variable.key.trim() || "Variable";
  const units = legend?.units?.trim();
  const unitsNormalized = units?.toLowerCase().replace(/[()]/g, "").trim() ?? "";
  const labelNormalized = baseVariableLabel.toLowerCase();
  const variableLabel = unitsNormalized && labelNormalized.includes(unitsNormalized)
    ? baseVariableLabel
    : units
      ? `${baseVariableLabel} (${units})`
      : baseVariableLabel;

  if (legend?.kind === "categorical") {
    const modelNameNormalized = model.toLowerCase();
    const variableLabelNormalized = baseVariableLabel.toLowerCase();
    const modelPrefix = modelNameNormalized.split(" ")[0] ?? "";
    const labelStartsWithModel = modelPrefix.length > 2 && variableLabelNormalized.startsWith(modelPrefix);
    const line1 = labelStartsWithModel
      ? `${baseVariableLabel} • ${run}`
      : `${model} • ${baseVariableLabel} • ${run}`;

    // Only SPC-style day-indexed products get a line 2 with day number + date
    if (isSpcCategoricalLegend(legend) && state.validTimeISO) {
      const parsed = new Date(state.validTimeISO);
      if (!Number.isNaN(parsed.getTime())) {
        const compactDate = new Intl.DateTimeFormat("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
        }).format(parsed);
        const dayNumber = Number.isFinite(state.fh) && state.fh >= 0 && state.fh <= 6
          ? state.fh + 1
          : null;
        const line2 = dayNumber !== null ? `Day ${dayNumber} • ${compactDate}` : compactDate;
        return [line1, line2];
      }
    }
    if (isCpcProbabilityLegend(legend)) {
      const validLine = buildCpcValidLine(state);
      return validLine ? [line1, validLine] : [line1];
    }
    return [line1];
  }

  if (state.timeAxisMode === "observed") {
    const observedLabel = formatObservedValidTime(state.validTimeISO) ?? formatObservedCompactTime(state.validTimeISO) ?? "Observed time n/a";
    const statusSuffix = state.sourceStatusLabel ? ` • ${state.sourceStatusLabel}` : "";
    return [`${model} • ${run} • ${observedLabel}${statusSuffix}`, variableLabel];
  }
  // Line 1: {run} {model} • {frame label} • {local valid time}, line 2: variable.
  // Compare screenshots override the {run} {model} prefix with a per-side
  // title ("Compare: … vs …" / "Difference: … − …") via overlayTitle.
  const titlePrefix = state.overlayTitle?.trim() || `${run} ${model}`;
  const localValidTime = formatShareOverlayTime(state.validTimeISO);
  if (state.timeAxisMode === "valid") {
    const frameLabel = validAxisLabel(state.fh, state.variable.key, state.runTimeISO, state.validTimeISO);
    return [
      [titlePrefix, frameLabel, localValidTime].filter(Boolean).join(" • "),
      variableLabel,
    ];
  }
  return [
    [titlePrefix, `FH ${state.fh}`, localValidTime].filter(Boolean).join(" • "),
    variableLabel,
  ];
}

function drawGlassCard(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
): void {
  ctx.save();
  if (chromeShadowsEnabled) {
    ctx.shadowColor = "rgba(0,0,0,0.35)";
    ctx.shadowBlur = 32;
    ctx.shadowOffsetY = 8;
  }
  ctx.fillStyle = "rgba(0,0,0,0.38)";
  drawRoundedRect(ctx, x, y, width, height, radius);
  ctx.fill();
  ctx.restore();

  const gradient = ctx.createLinearGradient(0, y, 0, y + height);
  gradient.addColorStop(0, "rgba(255,255,255,0.08)");
  gradient.addColorStop(0.22, "rgba(255,255,255,0.03)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  ctx.save();
  ctx.fillStyle = gradient;
  drawRoundedRect(ctx, x, y, width, height, radius);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.10)";
  ctx.lineWidth = 1;
  drawRoundedRect(ctx, x + 0.5, y + 0.5, width - 1, height - 1, Math.max(0, radius - 0.5));
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  drawRoundedRect(ctx, x + 1.5, y + 1.5, width - 3, height - 3, Math.max(0, radius - 1.5));
  ctx.stroke();
  ctx.restore();
}

function drawDarkCard(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
): void {
  ctx.save();
  if (chromeShadowsEnabled) {
    ctx.shadowColor = "rgba(0,0,0,0.38)";
    ctx.shadowBlur = 28;
    ctx.shadowOffsetY = 8;
  }
  const bgGradient = ctx.createLinearGradient(x, y, x + width, y + height);
  bgGradient.addColorStop(0, "rgba(28,32,42,0.90)");
  bgGradient.addColorStop(1, "rgba(52,58,72,0.84)");
  ctx.fillStyle = bgGradient;
  drawRoundedRect(ctx, x, y, width, height, radius);
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.lineWidth = 1;
  drawRoundedRect(ctx, x + 0.5, y + 0.5, width - 1, height - 1, Math.max(0, radius - 0.5));
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,0.04)";
  drawRoundedRect(ctx, x + 1.5, y + 1.5, width - 3, height - 3, Math.max(0, radius - 1.5));
  ctx.fill();
  ctx.restore();
}

function imageSourceDimensions(source: CanvasImageSource): { width: number; height: number } | null {
  if (source instanceof HTMLImageElement) {
    const width = source.naturalWidth || source.width;
    const height = source.naturalHeight || source.height;
    return width > 0 && height > 0 ? { width, height } : null;
  }
  if (source instanceof HTMLCanvasElement || (typeof OffscreenCanvas !== "undefined" && source instanceof OffscreenCanvas)) {
    return source.width > 0 && source.height > 0 ? { width: source.width, height: source.height } : null;
  }
  if (typeof ImageBitmap !== "undefined" && source instanceof ImageBitmap) {
    return source.width > 0 && source.height > 0 ? { width: source.width, height: source.height } : null;
  }
  if (typeof SVGImageElement !== "undefined" && source instanceof SVGImageElement) {
    const width = source.width.baseVal.value;
    const height = source.height.baseVal.value;
    return width > 0 && height > 0 ? { width, height } : null;
  }
  if (source instanceof HTMLVideoElement) {
    const width = source.videoWidth || source.width;
    const height = source.videoHeight || source.height;
    return width > 0 && height > 0 ? { width, height } : null;
  }
  return null;
}

function drawMapImageCover(
  ctx: CanvasRenderingContext2D,
  image: CanvasImageSource,
  width: number,
  height: number
): void {
  const dimensions = imageSourceDimensions(image);
  if (!dimensions) {
    ctx.drawImage(image, 0, 0, width, height);
    return;
  }

  const sourceAspect = dimensions.width / dimensions.height;
  const targetAspect = width / height;
  let sourceX = 0;
  let sourceY = 0;
  let sourceWidth = dimensions.width;
  let sourceHeight = dimensions.height;

  if (sourceAspect > targetAspect) {
    sourceWidth = dimensions.height * targetAspect;
    sourceX = (dimensions.width - sourceWidth) / 2;
  } else if (sourceAspect < targetAspect) {
    sourceHeight = dimensions.width / targetAspect;
    sourceY = (dimensions.height - sourceHeight) / 2;
  }

  ctx.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, width, height);
}

function drawOverlay(
  ctx: CanvasRenderingContext2D,
  lines: string[],
  width: number,
  scaleFactor = 1
): void {
  const cleaned = lines.map((line) => line.trim()).filter(Boolean);
  if (cleaned.length === 0) {
    return;
  }

  const paddingX = 14 * scaleFactor;
  const paddingY = 12 * scaleFactor;
  const lineHeight = 21 * scaleFactor;
  const boxX = 18 * scaleFactor;
  const boxY = 18 * scaleFactor;
  const maxWidth = Math.max(280 * scaleFactor, width * 0.6);
  const font = `700 ${16 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;

  ctx.save();
  ctx.font = font;
  let textWidth = 0;
  for (const line of cleaned) {
    textWidth = Math.max(textWidth, ctx.measureText(line).width);
  }
  const boxWidth = Math.min(maxWidth, Math.ceil(textWidth) + paddingX * 2);
  const boxHeight = cleaned.length * lineHeight + paddingY * 2 - 2;

  drawDarkCard(ctx, boxX, boxY, boxWidth, boxHeight, 11 * scaleFactor);

  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.textBaseline = "top";
  ctx.font = font;
  cleaned.forEach((line, index) => {
    ctx.fillText(line, boxX + paddingX, boxY + paddingY + index * lineHeight, boxWidth - paddingX * 2);
  });
  ctx.restore();
}

type LegendEntry = LegendPayload["entries"][number];
type RadarLegendGroup = {
  label: string;
  entries: LegendEntry[];
};
type PtypeIntensityLegendRow = {
  label: string;
  min: number;
  max: number;
  colors: string[];
};

const RADAR_GROUP_LABELS = ["Rain", "Snow", "Sleet", "Freezing Rain"];
const DEFAULT_PTYPE_ORDER = ["rain", "snow", "sleet", "frzr"];

function formatLegendValue(value: number): string {
  if (Number.isInteger(value)) return value.toString();
  if (Math.abs(value) < 0.1) return value.toFixed(2);
  return value.toFixed(1);
}

function compactLegendTitle(legend: LegendPayload): string {
  const title = legend.title.trim();
  const units = legend.units?.trim();
  if (!units) {
    return title;
  }
  if (title.toLowerCase().includes(units.toLowerCase())) {
    return title;
  }
  return `${title} (${units})`;
}

function sortLegendEntriesAscending(entries: LegendPayload["entries"]): LegendPayload["entries"] {
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      const byValue = left.entry.value - right.entry.value;
      return byValue !== 0 ? byValue : left.index - right.index;
    })
    .map(({ entry }) => entry);
}

function radarGroupLabelForCode(code: string, index: number): string {
  const normalized = code.toLowerCase();
  if (normalized === "rain") return "Rain";
  if (normalized === "snow") return "Snow";
  if (normalized === "sleet") return "Sleet";
  if (normalized === "ice") return "Ice";
  if (normalized === "frzr") return "Freezing Rain";
  return RADAR_GROUP_LABELS[index] ?? `Type ${index + 1}`;
}

function isRadarPtypeLegend(legend: LegendPayload): boolean {
  const kind = legend.kind?.toLowerCase() ?? "";
  const id = legend.id?.toLowerCase() ?? "";
  return (
    kind.includes("radar_ptype") ||
    kind.includes("radar_ptype_combo") ||
    id.includes("radar") ||
    id === "radar_ptype"
  );
}

function isPtypeIntensityLegend(legend: LegendPayload): boolean {
  const id = legend.id?.toLowerCase() ?? "";
  return id === "ptype_intensity";
}

function groupRadarEntries(legend: LegendPayload): RadarLegendGroup[] {
  const isZero = (value: number) => Math.abs(value) < 1e-9;

  if (legend.ptype_breaks) {
    const orderedTypes = (
      Array.isArray(legend.ptype_order) && legend.ptype_order.length > 0 ? legend.ptype_order : DEFAULT_PTYPE_ORDER
    ).filter((ptype) => legend.ptype_breaks?.[ptype]);
    const groupedByMeta: RadarLegendGroup[] = [];

    for (let index = 0; index < orderedTypes.length; index += 1) {
      const ptype = orderedTypes[index];
      const boundary = legend.ptype_breaks?.[ptype];
      if (!boundary) continue;
      const offset = Number(boundary.offset);
      const count = Number(boundary.count);
      if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) continue;
      const slice = legend.entries.slice(offset, offset + count);
      if (slice.length === 0) continue;
      groupedByMeta.push({ label: radarGroupLabelForCode(ptype, index), entries: slice });
    }

    if (groupedByMeta.length > 0) {
      return groupedByMeta;
    }
  }

  const fallbackGroups: RadarLegendGroup[] = [];
  let current: LegendEntry[] = [];
  for (const entry of legend.entries) {
    if (isZero(entry.value)) {
      if (current.length > 0) {
        fallbackGroups.push({
          label: RADAR_GROUP_LABELS[fallbackGroups.length] ?? `Type ${fallbackGroups.length + 1}`,
          entries: current,
        });
        current = [];
      }
      continue;
    }
    current.push(entry);
  }

  if (current.length > 0) {
    fallbackGroups.push({
      label: RADAR_GROUP_LABELS[fallbackGroups.length] ?? `Type ${fallbackGroups.length + 1}`,
      entries: current,
    });
  }

  return fallbackGroups;
}

function groupPtypeIntensityRows(legend: LegendPayload): PtypeIntensityLegendRow[] {
  if (!legend.ptype_breaks) return [];
  const orderedTypes = (Array.isArray(legend.ptype_order) && legend.ptype_order.length > 0 ? legend.ptype_order : [])
    .filter((ptype) => legend.ptype_breaks?.[ptype]);
  if (orderedTypes.length === 0) return [];

  const rows: PtypeIntensityLegendRow[] = [];
  for (let index = 0; index < orderedTypes.length; index += 1) {
    const ptype = orderedTypes[index];
    const boundary = legend.ptype_breaks[ptype];
    const offset = Number(boundary.offset);
    const count = Number(boundary.count);
    if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) continue;
    const segment = legend.entries.slice(offset, offset + count);
    if (segment.length === 0) continue;
    const colors = segment.map((entry) => entry.color).filter(Boolean);
    const min = Number(segment[0]?.value);
    const max = Number(segment[segment.length - 1]?.value);
    if (colors.length === 0 || !Number.isFinite(min) || !Number.isFinite(max)) continue;
    rows.push({ label: radarGroupLabelForCode(ptype, index), min, max, colors });
  }

  return rows;
}

function fillHorizontalGradient(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  colors: string[],
  radius: number
): void {
  const gradient = ctx.createLinearGradient(x, 0, x + width, 0);
  const steps = Math.max(1, colors.length - 1);
  colors.forEach((color, index) => {
    gradient.addColorStop(index / steps, color);
  });
  ctx.fillStyle = gradient;
  drawRoundedRect(ctx, x, y, width, height, radius);
  ctx.fill();
}

function drawLegendLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  align: CanvasTextAlign = "left"
): void {
  ctx.textAlign = align;
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "rgba(255,255,255,0.95)";
  ctx.fillText(text, x, y);
}

function strokeRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
): void {
  drawRoundedRect(ctx, x, y, width, height, radius);
  ctx.stroke();
}

function drawSectionGradient(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  colors: string[],
  radius: number
): void {
  fillHorizontalGradient(ctx, x, y, width, height, colors, radius);
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  strokeRoundedRect(ctx, x + 0.5, y + 0.5, width - 1, height - 1, radius);
}

function isCpcProbabilityLegend(legend: LegendPayload): boolean {
  if (legend.kind?.toLowerCase() !== "categorical") return false;
  return legend.entries.some(
    (e) => typeof e.label === "string" && /normal/i.test(e.label)
  );
}

function drawCpcProbabilityLegend(
  ctx: CanvasRenderingContext2D,
  legend: LegendPayload,
  width: number,
  height: number,
  bottomPadding: number,
  isMobileLayout: boolean,
  scaleFactor: number
): void {
  const belowEntries = legend.entries.filter(
    (e) => typeof e.label === "string" && /below/i.test(e.label)
  );
  const aboveEntries = legend.entries.filter(
    (e) => typeof e.label === "string" && /above/i.test(e.label)
  );
  const nearEntry = legend.entries.find(
    (e) => typeof e.label === "string" && /near/i.test(e.label)
  );

  if (belowEntries.length === 0 && aboveEntries.length === 0) return;

  const outerPadding = 18 * scaleFactor;
  const bandWidth = width - outerPadding * 2;
  const bandX = outerPadding;
  const PAD_X = 14 * scaleFactor;
  const PAD_TOP = 9 * scaleFactor;
  const PAD_BOT = 10 * scaleFactor;
  const WING_LABEL_H = 13 * scaleFactor;
  const BAR_H = 13 * scaleFactor;
  const TICK_H = 12 * scaleFactor;
  const NOTE_H = legend.note ? 12 * scaleFactor : 0;
  const NOTE_GAP = legend.note ? 5 * scaleFactor : 0;
  const bandHeight = PAD_TOP + WING_LABEL_H + 4 * scaleFactor + BAR_H + TICK_H + NOTE_GAP + NOTE_H + PAD_BOT;
  const bandY = height - bottomPadding - bandHeight;
  const contentX = bandX + PAD_X;
  const contentWidth = bandWidth - PAD_X * 2;

  const NEAR_W = nearEntry ? (isMobileLayout ? 28 : 56) * scaleFactor : 0;
  const WING_GAP = 10 * scaleFactor;
  const wingsWidth = contentWidth - NEAR_W - (nearEntry ? WING_GAP * 2 : 0);
  const wingW = wingsWidth / 2;

  const belowX = contentX;
  const nearX = contentX + wingW + (nearEntry ? WING_GAP : 0);
  const aboveX = nearX + NEAR_W + (nearEntry ? WING_GAP : 0);

  const barY = bandY + PAD_TOP + WING_LABEL_H + 4 * scaleFactor;
  const tickY = barY + BAR_H + 3 * scaleFactor;

  ctx.save();
  drawDarkCard(ctx, bandX, bandY, bandWidth, bandHeight, 12 * scaleFactor);

  const wingLabelFontSize = isMobileLayout ? 8 : 9;
  const wingLabelFont = `700 ${wingLabelFontSize * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
  const tickFont = `600 ${8 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
  const noteFont = `500 ${8 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
  const sectionRadius = 4 * scaleFactor;
  const blockGap = 2 * scaleFactor;

  function drawBlockRow(
    x: number,
    y: number,
    w: number,
    h: number,
    colors: string[]
  ): void {
    if (colors.length === 0) return;
    const totalGap = blockGap * (colors.length - 1);
    const blockW = (w - totalGap) / colors.length;
    colors.forEach((color, i) => {
      const bx = x + i * (blockW + blockGap);
      const isFirst = i === 0;
      const isLast = i === colors.length - 1;
      ctx.fillStyle = color;
      ctx.beginPath();
      const r = sectionRadius;
      const rx = isFirst ? r : 0;
      const ry = isLast ? r : 0;
      ctx.moveTo(bx + rx, y);
      ctx.lineTo(bx + blockW - ry, y);
      ctx.quadraticCurveTo(bx + blockW, y, bx + blockW, y + (ry ? r : 0));
      ctx.lineTo(bx + blockW, y + h - (ry ? r : 0));
      ctx.quadraticCurveTo(bx + blockW, y + h, bx + blockW - ry, y + h);
      ctx.lineTo(bx + rx, y + h);
      ctx.quadraticCurveTo(bx, y + h, bx, y + h - (rx ? r : 0));
      ctx.lineTo(bx, y + (rx ? r : 0));
      ctx.quadraticCurveTo(bx, y, bx + rx, y);
      ctx.closePath();
      ctx.fill();
    });
    ctx.strokeStyle = "rgba(255,255,255,0.18)";
    ctx.lineWidth = 1;
    drawRoundedRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, sectionRadius);
    ctx.stroke();
  }

  const belowColors = belowEntries.map((e) => e.color);
  ctx.font = wingLabelFont;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.fillText("Below Normal", belowX, bandY + PAD_TOP + WING_LABEL_H - 2 * scaleFactor);
  drawBlockRow(belowX, barY, wingW, BAR_H, belowColors);
  ctx.font = tickFont;
  ctx.fillStyle = "rgba(255,255,255,0.52)";
  ctx.textAlign = "left";
  ctx.fillText("33%", belowX, tickY + TICK_H - 3 * scaleFactor);
  ctx.textAlign = "right";
  ctx.fillText("90–100%", belowX + wingW, tickY + TICK_H - 3 * scaleFactor);

  if (nearEntry) {
    ctx.font = wingLabelFont;
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,0.72)";
    ctx.font = `700 ${7.5 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
    ctx.fillText("Near Normal", nearX + NEAR_W / 2, bandY + PAD_TOP + WING_LABEL_H - 2 * scaleFactor);
    ctx.fillStyle = nearEntry.color;
    drawRoundedRect(ctx, nearX, barY, NEAR_W, BAR_H, sectionRadius);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.18)";
    ctx.lineWidth = 1;
    drawRoundedRect(ctx, nearX + 0.5, barY + 0.5, NEAR_W - 1, BAR_H - 1, sectionRadius);
    ctx.stroke();
  }

  const aboveColors = aboveEntries.map((e) => e.color);
  ctx.font = wingLabelFont;
  ctx.textAlign = "right";
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.fillText("Above Normal", aboveX + wingW, bandY + PAD_TOP + WING_LABEL_H - 2 * scaleFactor);
  drawBlockRow(aboveX, barY, wingW, BAR_H, aboveColors);
  ctx.font = tickFont;
  ctx.fillStyle = "rgba(255,255,255,0.52)";
  ctx.textAlign = "left";
  ctx.fillText("33%", aboveX, tickY + TICK_H - 3 * scaleFactor);
  ctx.textAlign = "right";
  ctx.fillText("90–100%", aboveX + wingW, tickY + TICK_H - 3 * scaleFactor);

  if (legend.note) {
    const noteY = tickY + TICK_H + NOTE_GAP + 8 * scaleFactor;
    ctx.font = noteFont;
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(255,255,255,0.38)";
    ctx.fillText(legend.note, contentX, noteY, contentWidth);
  }

  ctx.restore();
}

function isSpcCategoricalLegend(legend: LegendPayload): boolean {
  if (legend.kind?.toLowerCase() !== "categorical") return false;
  if (isCpcProbabilityLegend(legend)) return false;
  return legend.entries.some(
    (e) => typeof e.label === "string" && e.label.trim().length > 0
  );
}

function drawSpcCategoricalLegend(
  ctx: CanvasRenderingContext2D,
  legend: LegendPayload,
  width: number,
  height: number,
  bottomPadding: number,
  scaleFactor: number
): void {
  const entries = legend.entries.filter(
    (e) => e.color && typeof e.label === "string" && e.label.trim().length > 0
  );
  if (entries.length === 0) return;

  const outerPadding = 18 * scaleFactor;
  const PAD_X = 14 * scaleFactor;
  const PAD_Y = 10 * scaleFactor;
  const SWATCH = 13 * scaleFactor;
  const GAP_INNER = 6 * scaleFactor;
  const GAP_ENTRY = 18 * scaleFactor;
  const ROW_H = SWATCH;

  ctx.save();
  ctx.font = `600 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
  const totalContentWidth = entries.reduce((sum, entry, i) => {
    const labelW = ctx.measureText(entry.label!.trim()).width;
    return sum + SWATCH + GAP_INNER + labelW + (i < entries.length - 1 ? GAP_ENTRY : 0);
  }, 0);

  const bandWidth = totalContentWidth + PAD_X * 2;
  const bandHeight = PAD_Y + ROW_H + PAD_Y;
  const bandX = (width - bandWidth) / 2;
  const bandY = height - bottomPadding - bandHeight;
  const rowY = bandY + PAD_Y;

  drawDarkCard(ctx, bandX, bandY, bandWidth, bandHeight, 12 * scaleFactor);

  let x = bandX + PAD_X;

  for (const entry of entries) {
    const label = entry.label!.trim();

    ctx.fillStyle = entry.color;
    drawRoundedRect(ctx, x, rowY, SWATCH, SWATCH, 3 * scaleFactor);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.22)";
    ctx.lineWidth = 1;
    drawRoundedRect(ctx, x + 0.5, rowY + 0.5, SWATCH - 1, SWATCH - 1, 3 * scaleFactor);
    ctx.stroke();

    ctx.font = `600 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
    ctx.fillStyle = "rgba(255,255,255,0.90)";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + SWATCH + GAP_INNER, rowY + SWATCH / 2);

    x += SWATCH + GAP_INNER + ctx.measureText(label).width + GAP_ENTRY;
  }

  ctx.restore();
}

function drawBottomLegend(
  ctx: CanvasRenderingContext2D,
  legend: LegendPayload,
  width: number,
  height: number,
  bottomPadding: number,
  isMobileLayout: boolean,
  scaleFactor = 1
): void {
  // Delegate to specialised renderers before any shared layout or card drawing
  if (isCpcProbabilityLegend(legend)) {
    drawCpcProbabilityLegend(ctx, legend, width, height, bottomPadding, isMobileLayout, scaleFactor);
    return;
  }

  if (isSpcCategoricalLegend(legend)) {
    drawSpcCategoricalLegend(ctx, legend, width, height, bottomPadding, scaleFactor);
    return;
  }

  const outerPadding = 18 * scaleFactor;
  const isPrecip = isPtypeIntensityLegend(legend);
  const isRadar = isRadarPtypeLegend(legend);
  const bandHeight = isPrecip ? (isMobileLayout ? 112 : 60) : isRadar ? (isMobileLayout ? 112 : 66) : 54;
  const bandX = outerPadding;
  const scaledBandHeight = bandHeight * scaleFactor;
  const bandY = height - bottomPadding - scaledBandHeight;
  const bandWidth = width - outerPadding * 2;
  const contentX = bandX + 14 * scaleFactor;
  const contentWidth = bandWidth - 28 * scaleFactor;
  const barHeight = (isPrecip || isRadar ? 12 : 14) * scaleFactor;
  const barY = isPrecip || isRadar ? bandY + 36 * scaleFactor : bandY + 24 * scaleFactor;
  const sectionRadius = 8 * scaleFactor;

  ctx.save();
  drawDarkCard(ctx, bandX, bandY, bandWidth, scaledBandHeight, 12 * scaleFactor);

  if (isPrecip) {
    const rows = groupPtypeIntensityRows(legend);
    if (rows.length > 0) {
      if (isMobileLayout) {
        const columns = 2;
        const gapX = 12 * scaleFactor;
        const gapY = 10 * scaleFactor;
        const sectionWidth = (contentWidth - gapX * (columns - 1)) / columns;
        const sectionHeight = 30 * scaleFactor;
        rows.forEach((row, index) => {
          const column = index % columns;
          const rowIndex = Math.floor(index / columns);
          const x = contentX + column * (sectionWidth + gapX);
          const y = bandY + 16 * scaleFactor + rowIndex * (sectionHeight + gapY);
          ctx.font = `700 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, row.label.toUpperCase(), x, y);
          ctx.font = `600 ${11 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, `${formatLegendValue(row.min)}-${formatLegendValue(row.max)}`, x + sectionWidth, y, "right");
          drawSectionGradient(ctx, x, y + 8 * scaleFactor, sectionWidth, barHeight, row.colors, sectionRadius);
        });
      } else {
        const gap = 10 * scaleFactor;
        const sectionWidth = (contentWidth - gap * (rows.length - 1)) / rows.length;
        rows.forEach((row, index) => {
          const x = contentX + index * (sectionWidth + gap);
          ctx.font = `700 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, row.label.toUpperCase(), x, bandY + 18 * scaleFactor);
          ctx.font = `600 ${11 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, `${formatLegendValue(row.min)}-${formatLegendValue(row.max)}`, x, bandY + 30 * scaleFactor);
          drawSectionGradient(ctx, x, barY, sectionWidth, barHeight, row.colors, sectionRadius);
        });
      }
      ctx.restore();
      return;
    }
  }

  if (isRadar) {
    const groups = groupRadarEntries(legend);
    if (groups.length > 0) {
      if (isMobileLayout) {
        const columns = 2;
        const gapX = 12 * scaleFactor;
        const gapY = 10 * scaleFactor;
        const sectionWidth = (contentWidth - gapX * (columns - 1)) / columns;
        const sectionHeight = 38 * scaleFactor;
        groups.forEach((group, groupIndex) => {
          const column = groupIndex % columns;
          const rowIndex = Math.floor(groupIndex / columns);
          const x = contentX + column * (sectionWidth + gapX);
          const y = bandY + 16 * scaleFactor + rowIndex * (sectionHeight + gapY);
          const colors = group.entries.map((entry) => entry.color).filter(Boolean);

          ctx.font = `700 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, group.label.toUpperCase(), x, y);
          drawSectionGradient(ctx, x, y + 8 * scaleFactor, sectionWidth, barHeight, colors, sectionRadius);
          ctx.font = `700 ${8 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, "LIGHT", x, y + 29 * scaleFactor);
          drawLegendLabel(ctx, "HEAVY", x + sectionWidth, y + 29 * scaleFactor, "right");
        });
      } else {
        const gap = 10 * scaleFactor;
        const sectionWidth = (contentWidth - gap * (groups.length - 1)) / groups.length;
        groups.forEach((group, groupIndex) => {
          const x = contentX + groupIndex * (sectionWidth + gap);
          const colors = group.entries.map((entry) => entry.color).filter(Boolean);
          ctx.font = `700 ${10 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, group.label.toUpperCase(), x, bandY + 18 * scaleFactor);
          drawSectionGradient(ctx, x, barY, sectionWidth, barHeight, colors, sectionRadius);
          ctx.font = `700 ${8 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
          drawLegendLabel(ctx, "LIGHT", x, bandY + 56 * scaleFactor);
          drawLegendLabel(ctx, "HEAVY", x + sectionWidth, bandY + 56 * scaleFactor, "right");
        });
      }
      ctx.restore();
      return;
    }
  }

  if (legend.entries.length === 0) {
    ctx.restore();
    return;
  }

  const displayedEntries = sortLegendEntriesAscending(legend.entries);

  drawSectionGradient(ctx, contentX, barY, contentWidth, barHeight, displayedEntries.map((entry) => entry.color), sectionRadius);

  const labelIndices = [0, 0.25, 0.5, 0.75, 1].map((ratio) =>
    Math.min(displayedEntries.length - 1, Math.max(0, Math.round((displayedEntries.length - 1) * ratio)))
  );
  const dedupedIndices = labelIndices.filter((value, index) => index === 0 || value !== labelIndices[index - 1]);
  ctx.font = `600 ${11 * scaleFactor}px system-ui, -apple-system, Segoe UI, sans-serif`;
  dedupedIndices.forEach((entryIndex, index) => {
    const entry = displayedEntries[entryIndex];
    const ratio = dedupedIndices.length === 1 ? 0 : index / (dedupedIndices.length - 1);
    const labelX = contentX + ratio * contentWidth;
    const align: CanvasTextAlign = index === 0 ? "left" : index === dedupedIndices.length - 1 ? "right" : "center";
    drawLegendLabel(ctx, formatLegendValue(entry.value), labelX, bandY + 18 * scaleFactor, align);
  });
  ctx.restore();
}

// Cached across calls: GIF export composes up to 60 frames back to back and
// each frame draws the same logo bitmap.
let cachedLogoPromise: Promise<HTMLImageElement> | null = null;

function loadLogoImage(): Promise<HTMLImageElement> {
  if (!cachedLogoPromise) {
    cachedLogoPromise = loadImage(SCREENSHOT_LOGO_SRC).catch((error) => {
      cachedLogoPromise = null;
      throw error;
    });
  }
  return cachedLogoPromise;
}

async function drawLogo(ctx: CanvasRenderingContext2D, width: number, scaleFactor = 1): Promise<void> {
  const logo = await loadLogoImage();
  const padding = 18 * scaleFactor;
  const maxWidth = 162 * scaleFactor;
  const maxHeight = 46 * scaleFactor;
  const scale = Math.min(maxWidth / logo.width, maxHeight / logo.height);
  const drawWidth = Math.max(1, Math.round(logo.width * scale));
  const drawHeight = Math.max(1, Math.round(logo.height * scale));
  const cardPaddingX = 12 * scaleFactor;
  const cardPaddingY = 8 * scaleFactor;
  const cardWidth = drawWidth + cardPaddingX * 2;
  const cardHeight = drawHeight + cardPaddingY * 2;
  // Integer positions: fractional coordinates resample the bitmap, which reads
  // as pixelation at GIF sizes (pixelRatio 1); harmless at the stills' 2×.
  const cardX = Math.round(width - padding - cardWidth);
  const cardY = Math.round(padding);

  drawDarkCard(ctx, cardX, cardY, cardWidth, cardHeight, 11 * scaleFactor);

  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  if (chromeShadowsEnabled) {
    ctx.shadowColor = "rgba(0,0,0,0.42)";
    ctx.shadowBlur = 18;
    ctx.shadowOffsetY = 4;
  }
  ctx.drawImage(
    logo,
    Math.round(cardX + cardPaddingX),
    Math.round(cardY + cardPaddingY),
    drawWidth,
    drawHeight,
  );
  ctx.restore();
}

/** Overlay title lines for a share frame (still or GIF); exported so the GIF
 * export can rebuild them per frame with that frame's fh/valid time. */
export function buildShareOverlayLines(
  state: ScreenshotExportState,
  legend?: LegendPayload | null,
): string[] {
  return defaultOverlayLines(state, legend).filter(Boolean);
}

export type ShareFrameComposeOptions = {
  width: number;
  height: number;
  pixelRatio: number;
  legend?: LegendPayload | null;
  overlayLines: string[];
  isMobile: boolean;
  /** Overrides the width-derived scale for overlay/logo/legend chrome. GIF
   * frames render at 720px with pixelRatio 1, where the default 720/1280
   * scale makes the chrome small and blurry — a higher floor keeps it crisp. */
  chromeScale?: number;
  /** Soft card shadows quantize into dark banding in GIF output — pass false
   * to render flat chrome (default true, matching the stills). */
  chromeShadows?: boolean;
};

/**
 * Draw one composed share frame (map cover + overlay card + logo + legend)
 * onto the provided canvas. Shared by the still exporter and the per-frame
 * GIF compose (share overhaul Phase 3).
 *
 * No compare divider (split captures carry the gutter baked in; diff is a
 * single map) and no anchor chips (the capture is WYSIWYG and already shows
 * the in-map city labels) — see the Phase 1 notes in the plan doc.
 */
export async function composeShareFrame(
  outputCanvas: HTMLCanvasElement,
  mapImage: CanvasImageSource,
  opts: ShareFrameComposeOptions,
): Promise<void> {
  const { width, height, pixelRatio } = opts;
  // Chrome sizing (gate feedback 2026-07-07: cards must read as small helper
  // badges, never dominant). Two rules:
  // 1. Scale by the LIMITING dimension — wide-short composites (compare
  //    split ≈ 2000×760) previously scaled by width alone and drew ~1.6×
  //    cards the short frame couldn't accommodate.
  // 2. Apply a compact factor to the derived scale. An explicit
  //    opts.chromeScale (the GIF path's floor) is used as-is — its value
  //    already encodes the smallest-legible size.
  const CHROME_COMPACT_FACTOR = 0.72;
  const scaleFactor = opts.chromeScale
    ?? Math.min(width / NORMALIZED_OUTPUT_WIDTH, height / DEFAULT_HEIGHT) * CHROME_COMPACT_FACTOR;
  outputCanvas.width = Math.max(1, Math.round(width * pixelRatio));
  outputCanvas.height = Math.max(1, Math.round(height * pixelRatio));
  const outputCtx = outputCanvas.getContext("2d");
  if (!outputCtx) {
    throw new Error("Failed to create screenshot canvas context.");
  }

  outputCtx.imageSmoothingEnabled = true;
  outputCtx.imageSmoothingQuality = "high";
  outputCtx.save();
  outputCtx.scale(pixelRatio, pixelRatio);
  chromeShadowsEnabled = opts.chromeShadows ?? true;
  try {
    drawMapImageCover(outputCtx, mapImage, width, height);
    drawOverlay(outputCtx, opts.overlayLines, width, scaleFactor);

    try {
      await drawLogo(outputCtx, width, scaleFactor);
    } catch (error) {
      console.warn("[screenshot] Logo load failed; continuing without logo.", error);
    }

    const bottomPadding = 18 * scaleFactor;
    if (opts.legend) {
      drawBottomLegend(outputCtx, opts.legend, width, height, bottomPadding, opts.isMobile, scaleFactor);
    }
  } finally {
    chromeShadowsEnabled = true;
    outputCtx.restore();
  }
}

export async function exportViewerScreenshotPng(
  state: ScreenshotExportState,
  opts: ScreenshotExportOptions = {}
): Promise<Blob> {
  if (typeof document === "undefined" || typeof window === "undefined") {
    throw new Error("Screenshot export is only available in browser environments.");
  }

  const stateViewportWidth = Number.isFinite(state.viewportWidth) ? Number(state.viewportWidth) : null;
  const stateViewportHeight = Number.isFinite(state.viewportHeight) ? Number(state.viewportHeight) : null;

  const isPortraitViewport = stateViewportWidth !== null
    && stateViewportHeight !== null
    && stateViewportHeight > stateViewportWidth;

  const outputBaseWidth = isPortraitViewport ? PORTRAIT_OUTPUT_WIDTH : NORMALIZED_OUTPUT_WIDTH;

  const normalizedViewportSize = stateViewportWidth !== null
    && stateViewportHeight !== null
    && stateViewportWidth > 0
    && stateViewportHeight > 0
    ? {
        width: outputBaseWidth,
        height: Math.max(1, Math.round(outputBaseWidth / (stateViewportWidth / stateViewportHeight))),
      }
    : null;

  const width = Number.isFinite(opts.width)
    ? Math.max(1, Math.round(Number(opts.width)))
    : normalizedViewportSize
      ? normalizedViewportSize.width
      : DEFAULT_WIDTH;
  const height = Number.isFinite(opts.height)
    ? Math.max(1, Math.round(Number(opts.height)))
    : normalizedViewportSize
      ? normalizedViewportSize.height
      : DEFAULT_HEIGHT;
  const pixelRatio = Number.isFinite(opts.pixelRatio)
    ? Math.max(1, Number(opts.pixelRatio))
    : DEFAULT_PIXEL_RATIO;
  const overlayLines = (opts.overlayLines ?? defaultOverlayLines(state, opts.legend)).filter(Boolean);

  // Compose-only: the offscreen style-rebuild path was deleted (share overhaul
  // Phase 1). It rendered basemap/vectors but never the WebGL weather grid
  // (§2.1 of the plan), so its output was data-less and misleading. All callers
  // must supply a captured map image; failing loudly here beats silently
  // shipping a basemap-only share image.
  if (!state.capturedMapDataUrl) {
    throw new Error("Map capture unavailable. Retry the screenshot.");
  }
  const liveMapImage = await loadImage(state.capturedMapDataUrl);
  const outputCanvas = document.createElement("canvas");
  await composeShareFrame(outputCanvas, liveMapImage, {
    width,
    height,
    pixelRatio,
    legend: opts.legend,
    overlayLines,
    isMobile: state.isMobile,
  });
  return canvasToPngBlob(outputCanvas);
}
