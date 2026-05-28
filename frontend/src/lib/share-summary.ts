import { formatObservedCompactTime, formatObservedRunLabel, formatRunLabel, formatValidTime, validAxisLabel } from "@/lib/time-axis";

type BuildShareSummaryInput = {
  modelId: string;
  runId: string;
  variableId: string;
  variableDisplayName?: string | null;
  regionId: string;
  regionLabel?: string | null;
  forecastHour: number | null;
  timeAxisMode?: "forecast" | "observed" | "valid";
  validTimeISO?: string | null;
  centerLat: number | null;
  centerLon: number | null;
  zoom: number | null;
  animationEnabled: boolean;
};

type ShareSummary = {
  shortSummary: string;
  detailsSummary: string;
};

const MODEL_LABELS: Record<string, string> = {
  hrrr: "HRRR",
  gfs: "GFS",
  nam: "NAM",
  nbm: "NBM",
  rap: "RAP",
  gefs: "GEFS",
  eps: "EPS",
  ecmwf: "ECMWF",
  aifs: "AIFS",
  aigfs: "AIGFS",
};

const VARIABLE_SPECIAL_CASES: Record<string, string> = {
  radar_ptype: "Radar & precip type",
  mrms_radar_ptype: "Radar & precip type",
  qpf: "QPF",
  pwat: "Precipitable water",
  tmp2m: "2m temperature",
};

function titleCaseWords(value: string): string {
  return value
    .split(" ")
    .filter(Boolean)
    .map((part) => {
      if (part.length <= 3 && part === part.toUpperCase()) {
        return part;
      }
      return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase();
    })
    .join(" ");
}

function modelLabel(modelId: string): string {
  const key = modelId.trim().toLowerCase();
  if (!key) {
    return "Model";
  }
  return MODEL_LABELS[key] ?? key.toUpperCase();
}

function runLabel(runId: string, timeAxisMode: "forecast" | "observed" | "valid" = "forecast"): string {
  const trimmed = runId.trim();
  if (!trimmed) {
    return "Latest";
  }
  return timeAxisMode === "observed" ? formatObservedRunLabel(trimmed) : formatRunLabel(trimmed);
}

function variableLabel(variableId: string, preferred?: string | null): string {
  const preferredLabel = typeof preferred === "string" ? preferred.trim() : "";
  if (preferredLabel) {
    return preferredLabel;
  }
  const normalized = variableId.trim().toLowerCase();
  if (!normalized) {
    return "Variable";
  }
  if (VARIABLE_SPECIAL_CASES[normalized]) {
    return VARIABLE_SPECIAL_CASES[normalized];
  }
  const words = normalized.replace(/[_-]+/g, " ");
  return titleCaseWords(words);
}

function formatCenter(lat: number | null, lon: number | null): string {
  const latValue = Number.isFinite(lat) ? (lat as number).toFixed(2) : "n/a";
  const lonValue = Number.isFinite(lon) ? (lon as number).toFixed(2) : "n/a";
  return `Center ${latValue}, ${lonValue}`;
}

function formatZoom(zoom: number | null): string {
  if (!Number.isFinite(zoom)) {
    return "Zoom n/a";
  }
  return `Zoom ${(zoom as number).toFixed(2)}`;
}

function formatForecastHour(forecastHour: number | null): string {
  if (!Number.isFinite(forecastHour)) {
    return "Forecast hour n/a";
  }
  return `Forecast hour ${Math.round(forecastHour as number)}`;
}

function formatTimeSummary(input: BuildShareSummaryInput): string {
  if (input.timeAxisMode === "observed") {
    const observed = formatObservedCompactTime(input.validTimeISO);
    return observed ? `Observed ${observed}` : "Observed time n/a";
  }
  if (input.timeAxisMode === "valid") {
    const valid = formatValidTime(input.validTimeISO);
    return valid
      ? `${validAxisLabel(input.forecastHour, input.variableId)} • ${valid}`
      : validAxisLabel(input.forecastHour, input.variableId);
  }
  return formatForecastHour(input.forecastHour);
}

export function buildShareSummary(input: BuildShareSummaryInput): ShareSummary {
  const shortSummary = [
    modelLabel(input.modelId),
    runLabel(input.runId, input.timeAxisMode ?? "forecast"),
    formatTimeSummary(input),
    variableLabel(input.variableId, input.variableDisplayName),
  ].join(" • ");

  const detailsSummary = [
    formatCenter(input.centerLat, input.centerLon),
    formatZoom(input.zoom),
    `Animation ${input.animationEnabled ? "on" : "off"}`,
  ].join(" • ");

  return { shortSummary, detailsSummary };
}
