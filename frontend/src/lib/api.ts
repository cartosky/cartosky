import { API_ORIGIN, API_V4_BASE, type WeatherSubstrate } from "@/lib/config";
import {
  startNetworkTimer,
  trackNetworkFetchDuration,
  type NetworkDiagnosticMetricName,
} from "@/lib/network-diagnostics";
import {
  type AnchorBatchPoint,
  type AnchorBatchResponse,
  type AnchorFeatureCollection,
  isAnchorFeatureCollection,
} from "@/lib/anchor-labels";
import { getClerkAuthToken } from "@/lib/admin-api";
import { shouldAuthorizeProductRequest } from "@/lib/entitlements";

export type ModelOption = {
  id: string;
  name: string;
};

export type ModelTimeAxisMode = "forecast" | "observed" | "valid";
export type ModelDefaultFrameSelection = "first" | "latest";
export type AvailabilityFreshnessState = "live" | "delayed" | "stale" | "unavailable";

export type PressureCenter = {
  type: "H" | "L" | string;
  lat: number;
  lon: number;
  value?: number | string | null;
  units?: string | null;
  source?: string | null;
  prominence?: number | string | null;
};

export type CapabilityModelDefaults = Record<string, unknown> & {
  default_var_key?: string;
  default_run?: string;
  default_ensemble_view?: string | null;
  default_render_substrate?: WeatherSubstrate | null;
  default_frame_selection?: ModelDefaultFrameSelection | null;
};

export type CapabilityModelConstraints = Record<string, unknown> & {
  canonical_region?: string | null;
  time_axis_mode?: ModelTimeAxisMode | null;
  latest_only?: boolean | null;
  supports_sampling?: boolean | null;
};

export type CapabilityVariable = {
  var_key: string;
  display_name?: string;
  kind?: string | null;
  display_resampling_override?: string | null;
  units?: string | null;
  order?: number | null;
  group?: string | null;
  render_substrates?: WeatherSubstrate[] | null;
  supported_build_regions?: string[] | null;
  default_fh?: number | null;
  buildable?: boolean;
  color_map_id?: string | null;
  constraints?: Record<string, unknown>;
  derived?: boolean;
  derive_strategy_id?: string | null;
  ensemble?: Record<string, unknown>;
};

/**
 * One entry of a variable's ensemble stats product selector (stats design
 * §7 / D-D). key "mean" carries var_id null (today's behavior); stats
 * products carry the published runtime var id the viewer requests directly.
 */
export type EnsembleProductOption = {
  key: string;
  var_id?: string | null;
  label?: string;
  long_label?: string;
};

export type CapabilityModel = {
  model_id: string;
  name: string;
  product?: string | null;
  canonical_region?: string | null;
  defaults?: CapabilityModelDefaults;
  constraints?: CapabilityModelConstraints;
  run_discovery?: Record<string, unknown>;
  ensemble?: Record<string, unknown>;
  variables: Record<string, CapabilityVariable>;
};

export type CapabilitiesResponse = {
  contract_version: string;
  supported_models: string[];
  model_catalog: Record<string, CapabilityModel>;
  availability: Record<
    string,
    {
      latest_run: string | null;
      published_runs: string[];
      latest_run_ready?: boolean;
      latest_run_ready_vars?: string[];
      latest_run_ready_frame_count?: number;
      latest_run_target_max_fh?: number | null;
      source?: string | null;
      time_axis_mode?: ModelTimeAxisMode | null;
      latest_scan_valid_time?: string | null;
      latest_scan_age_minutes?: number | null;
      bundle_published_at?: string | null;
      bundle_age_seconds?: number | null;
      observation_to_publish_latency_seconds?: number | null;
      target_frame_count?: number | null;
      available_frame_count?: number | null;
      stale?: boolean | null;
      usable?: boolean | null;
      degraded_reason?: string | null;
      freshness_state?: AvailabilityFreshnessState | null;
    }
  >;
};

export type BootstrapSelection = {
  model: string;
  run: string;
  variable: string;
  ensemble_view?: string;
  region: string;
};

export type BootstrapResponse = {
  contract_version: string;
  capabilities: CapabilitiesResponse;
  regions: {
    regions: Record<string, RegionPreset>;
  };
  selection?: BootstrapSelection;
  manifest?: RunManifestResponse | null;
  frames?: FrameRow[];
};

export type RegionPreset = {
  label?: string;
  bbox: [number, number, number, number];
  defaultCenter: [number, number];
  defaultZoom: number;
  minZoom?: number;
  maxZoom?: number;
};

export type LegendStops = [number | string, string][];

export type LegendMeta = {
  kind?: string;
  display_name?: string;
  legend_title?: string;
  legend_note?: string;
  units?: string;
  valid_time?: string;
  valid_start?: string;
  valid_end?: string;
  valid_seas?: string;
  issue_time?: string;
  generated_at?: string;
  legend_stops?: LegendStops;
  legend?: { type?: string; stops?: LegendStops };
  legend_entries?: Array<{ value: number; color: string; label?: string }>;
  colors?: string[];
  levels?: number[];
  ptype_order?: string[];
  ptype_breaks?: Record<string, { offset: number; count: number }>;
  ptype_levels?: Record<string, number[]>;
  range?: [number, number];
  bins_per_ptype?: number;
  contours?: Record<
    string,
    {
      format?: string;
      path?: string;
      srs?: string;
      level?: number;
    }
  >;
  vector_layers?: Record<
    string,
    {
      format?: string;
      path?: string;
      style_key?: string;
    }
  >;
  pressure_centers?: PressureCenter[];
  day_label?: string;
};

export type FrameRow = {
  fh: number;
  has_cog: boolean;
  run?: string;
  valid_time?: string;
  tile_url_template?: string;
  meta?: {
    meta?: LegendMeta | null;
  } | null;
};

export type GridManifestFrame = {
  fh: number;
  file: string;
  valid_time?: string;
  url?: string;
};

export type GridManifestLod = {
  level: number;
  width: number;
  height: number;
  min_zoom?: number | null;
  max_zoom?: number | null;
  frames: GridManifestFrame[];
};

export type GridManifestGrid = {
  width: number;
  height: number;
  dtype: string;
  endianness: string;
  scale: number;
  offset: number;
  nodata: number;
  units?: string;
};

export type GridManifestContour = {
  format?: string;
  path?: string;
  srs?: string;
  level?: number;
  interval?: number;
  levels?: number[];
  label?: string;
  grid?: GridManifestGrid;
  lods?: GridManifestLod[];
};

export type GridManifestPalette = {
  color_map_id?: string | null;
  kind?: string | null;
  power_norm_gamma?: number | null;
  transparent_below_min?: number | null;
  transparent_zero?: boolean | null;
  ptype_order?: string[] | null;
  ptype_breaks?: Record<string, { offset: number; count: number }> | null;
};

export type GridManifestDisplayPrep = {
  id?: string;
  upscale_factor?: number | null;
  smooth_sigma?: number | null;
  preserve_zero_support?: boolean | null;
  support_min_value?: number | null;
  support_coverage_threshold?: number | null;
  categorical_nearest?: boolean | null;
};

export type GridManifestCompositeLayer = {
  id: string;
  var: string;
};

export type GridManifestResponse = {
  manifest_version: number;
  subtype: WeatherSubstrate | string;
  model: string;
  run: string;
  var: string;
  projection?: string;
  bbox?: [number, number, number, number];
  grid: GridManifestGrid;
  palette?: GridManifestPalette;
  display_prep?: GridManifestDisplayPrep | null;
  display_name?: string;
  legend?: { type?: string; stops?: LegendStops };
  contours?: Record<string, GridManifestContour>;
  composite_mode?: string | null;
  composite_layers?: GridManifestCompositeLayer[];
  lods: GridManifestLod[];
};

export type RunManifestFrame = {
  fh: number;
  valid_time?: string;
  generated_at?: string;
};

export type RunManifestVariable = {
  display_name?: string;
  name?: string;
  label?: string;
  expected_frames?: number;
  available_frames?: number;
  frames?: RunManifestFrame[];
};

export type RunManifestResponse = {
  contract_version?: string;
  model: string;
  run: string;
  region?: string;
  last_updated?: string;
  variables: Record<string, RunManifestVariable>;
};

export interface RgbManifestFrame {
  fh: number;
  valid_time: string;
  slot_time?: string;
  filename: string;
  url: string;
}

export interface RgbManifestResponse {
  model: string;
  run: string;
  var: string;
  kind: string;
  render_substrate: string;
  frames: RgbManifestFrame[];
  available_frames: number;
  expected_frames: number;
}

export type VarRow =
  | string
  | {
      id: string;
      display_name?: string;
      name?: string;
      label?: string;
    };

type FetchOptions = {
  signal?: AbortSignal;
  diagnosticMetricName?: NetworkDiagnosticMetricName;
  diagnosticMeta?: Record<string, unknown> | null;
  productId?: string | null;
  authorize?: boolean;
};

export function publicFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, init);
}

export async function authorizedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = await getClerkAuthToken();
  if (!token) {
    return publicFetch(input, init);
  }
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return publicFetch(input, {
    ...init,
    headers,
  });
}

export function productFetch(productId: string | null | undefined, input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return shouldAuthorizeProductRequest(productId ?? "") ? authorizedFetch(input, init) : publicFetch(input, init);
}

function normalizeGridWeatherSubstrate(value: unknown): WeatherSubstrate | null {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "grid") {
    return "grid";
  }
  if (normalized === "vector") {
    return "vector";
  }
  if (normalized === "raster_rgb" || normalized === "image") {
    return "image";
  }
  return null;
}

export function readCapabilityTimeAxisMode(model: CapabilityModel | null | undefined): ModelTimeAxisMode {
  const raw = String(model?.constraints?.time_axis_mode ?? "").trim().toLowerCase();
  if (raw === "valid") {
    return "valid";
  }
  return raw === "observed" ? "observed" : "forecast";
}

export function readCapabilityDefaultFrameSelection(
  model: CapabilityModel | null | undefined
): ModelDefaultFrameSelection {
  const raw = String(model?.defaults?.default_frame_selection ?? "").trim().toLowerCase();
  return raw === "latest" ? "latest" : "first";
}

export function readCapabilityLatestOnly(model: CapabilityModel | null | undefined): boolean {
  return model?.constraints?.latest_only === true;
}

export function readCapabilityRenderSubstrates(
  variable: CapabilityVariable | null | undefined
): WeatherSubstrate[] {
  const normalized: WeatherSubstrate[] = [];
  const raw = Array.isArray(variable?.render_substrates) ? variable.render_substrates : [];
  for (const entry of raw) {
    const substrate = normalizeGridWeatherSubstrate(entry);
    if (!substrate || normalized.includes(substrate)) {
      continue;
    }
    normalized.push(substrate);
  }
  if (normalized.length === 0) {
    return ["grid"];
  }
  return normalized;
}

export function readCapabilitySupportsSampling(model: CapabilityModel | null | undefined): boolean {
  return model?.constraints?.supports_sampling !== false;
}

async function fetchJson<T>(url: string, options?: FetchOptions): Promise<T> {
  const startedAtMs = startNetworkTimer();
  const request = options?.authorize
    ? authorizedFetch
    : shouldAuthorizeProductRequest(options?.productId ?? "")
      ? authorizedFetch
      : publicFetch;
  const response = await request(url, {
    credentials: "omit",
    signal: options?.signal,
    cache: "no-store",
  });
  if (options?.diagnosticMetricName) {
    trackNetworkFetchDuration({
      metric_name: options.diagnosticMetricName,
      started_at_ms: startedAtMs,
      response,
      meta: options.diagnosticMeta ?? null,
    });
  }
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isRunManifestResponse(value: unknown): value is RunManifestResponse {
  if (!isObject(value)) {
    return false;
  }
  if (typeof value.model !== "string" || typeof value.run !== "string") {
    return false;
  }
  if (!isObject(value.variables)) {
    return false;
  }

  for (const varEntry of Object.values(value.variables)) {
    if (!isObject(varEntry)) {
      return false;
    }
    if ("frames" in varEntry && !Array.isArray(varEntry.frames)) {
      return false;
    }
    if (Array.isArray(varEntry.frames)) {
      for (const frame of varEntry.frames) {
        if (!isObject(frame)) {
          return false;
        }
        if (!Number.isFinite(Number(frame.fh))) {
          return false;
        }
      }
    }
  }
  return true;
}

const REGIONS_CACHE_KEY = "twf_v3_regions_cache";
const REGIONS_ETAG_KEY = "twf_v3_regions_etag";

type RegionsResponse = {
  regions: Record<string, RegionPreset>;
};

export async function fetchRegionPresets(options?: FetchOptions): Promise<Record<string, RegionPreset>> {
  const cachedRaw = localStorage.getItem(REGIONS_CACHE_KEY);
  const etag = localStorage.getItem(REGIONS_ETAG_KEY);
  const headers: Record<string, string> = {};
  if (etag) {
    headers["If-None-Match"] = etag;
  }

  const startedAtMs = startNetworkTimer();
  const response = await publicFetch(`${API_ORIGIN}/api/regions`, {
    credentials: "omit",
    headers,
    signal: options?.signal,
  });
  trackNetworkFetchDuration({
    metric_name: options?.diagnosticMetricName ?? "regions_fetch_duration",
    started_at_ms: startedAtMs,
    response,
    meta: {
      ...(options?.diagnosticMeta ?? {}),
      had_cached_regions: Boolean(cachedRaw),
      had_if_none_match: Boolean(etag),
    },
  });

  if (response.status === 304 && cachedRaw) {
    try {
      const parsed = JSON.parse(cachedRaw) as RegionsResponse;
      return parsed.regions ?? {};
    } catch {
      return {};
    }
  }

  if (!response.ok) {
    if (cachedRaw) {
      try {
        const parsed = JSON.parse(cachedRaw) as RegionsResponse;
        return parsed.regions ?? {};
      } catch {
        return {};
      }
    }
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  const payload = (await response.json()) as RegionsResponse;
  const nextEtag = response.headers.get("ETag");
  localStorage.setItem(REGIONS_CACHE_KEY, JSON.stringify(payload));
  if (nextEtag) {
    localStorage.setItem(REGIONS_ETAG_KEY, nextEtag);
  }
  return payload.regions ?? {};
}

export async function fetchModels(options?: FetchOptions): Promise<ModelOption[]> {
  return fetchJson<ModelOption[]>(`${API_V4_BASE}/models`, options);
}

const CAPABILITIES_CACHE_KEY = "twf_v4_capabilities_cache";
const CAPABILITIES_ETAG_KEY = "twf_v4_capabilities_etag";

export async function fetchCapabilities(options?: FetchOptions): Promise<CapabilitiesResponse> {
  const cachedRaw = localStorage.getItem(CAPABILITIES_CACHE_KEY);
  const etag = localStorage.getItem(CAPABILITIES_ETAG_KEY);
  const headers: Record<string, string> = {};
  if (etag && cachedRaw) {
    headers["If-None-Match"] = etag;
  }

  const startedAtMs = startNetworkTimer();
  const response = await publicFetch(`${API_V4_BASE}/capabilities`, {
    credentials: "omit",
    headers,
    signal: options?.signal,
    cache: "no-store",
  });
  trackNetworkFetchDuration({
    metric_name: options?.diagnosticMetricName ?? "capabilities_fetch_duration",
    started_at_ms: startedAtMs,
    response,
    meta: {
      ...(options?.diagnosticMeta ?? {}),
      had_cached_capabilities: Boolean(cachedRaw),
      had_if_none_match: Boolean(etag && cachedRaw),
    },
  });

  if (response.status === 304 && cachedRaw) {
    try {
      return JSON.parse(cachedRaw) as CapabilitiesResponse;
    } catch {
      localStorage.removeItem(CAPABILITIES_CACHE_KEY);
      localStorage.removeItem(CAPABILITIES_ETAG_KEY);
    }
  }

  if (!response.ok) {
    if (cachedRaw) {
      try {
        return JSON.parse(cachedRaw) as CapabilitiesResponse;
      } catch {
        // fall through to the error below
      }
    }
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  const payload = (await response.json()) as CapabilitiesResponse;
  const nextEtag = response.headers.get("ETag");
  const cacheControl = (response.headers.get("Cache-Control") ?? "").toLowerCase();
  // Only cache responses the server marks shareable; when pro gating is enabled the
  // backend serves per-user availability as "private, no-store" without an ETag.
  const cacheable = Boolean(nextEtag) && !cacheControl.includes("no-store") && !cacheControl.includes("private");
  try {
    if (cacheable && nextEtag) {
      localStorage.setItem(CAPABILITIES_CACHE_KEY, JSON.stringify(payload));
      localStorage.setItem(CAPABILITIES_ETAG_KEY, nextEtag);
    } else {
      localStorage.removeItem(CAPABILITIES_CACHE_KEY);
      localStorage.removeItem(CAPABILITIES_ETAG_KEY);
    }
  } catch {
    // localStorage quota or private-mode failures must never break capabilities.
  }
  return payload;
}

export async function fetchBootstrap(params?: {
  model?: string;
  run?: string;
  variable?: string;
  ensembleView?: string;
  region?: string;
  signal?: AbortSignal;
}): Promise<BootstrapResponse> {
  const query = new URLSearchParams();
  if (params?.model) {
    query.set("model", params.model);
  }
  if (params?.run) {
    query.set("run", params.run);
  }
  if (params?.variable) {
    query.set("var", params.variable);
  }
  if (params?.ensembleView) {
    query.set("ensemble_view", params.ensembleView);
  }
  if (params?.region) {
    query.set("region", params.region);
  }
  const suffix = query.toString();
  const url = suffix ? `${API_V4_BASE}/bootstrap?${suffix}` : `${API_V4_BASE}/bootstrap`;
  return fetchJson<BootstrapResponse>(url, {
    signal: params?.signal,
    productId: params?.model ?? null,
    diagnosticMetricName: "bootstrap_fetch_duration",
    diagnosticMeta: {
      model: params?.model ?? null,
      run: params?.run ?? null,
      variable: params?.variable ?? null,
      ensemble_view: params?.ensembleView ?? null,
      region: params?.region ?? null,
    },
  });
}

export async function fetchRegions(model: string, options?: FetchOptions): Promise<string[]> {
  void model;
  const regions = await fetchRegionPresets(options);
  return Object.keys(regions);
}

export async function fetchRuns(model: string, options?: FetchOptions): Promise<string[]> {
  return fetchJson<string[]>(
    `${API_V4_BASE}/${encodeURIComponent(model)}/runs`,
    { ...options, productId: model }
  );
}

export async function fetchVars(model: string, run: string, options?: FetchOptions): Promise<VarRow[]> {
  const runKey = run || "latest";
  return fetchJson<VarRow[]>(
    `${API_V4_BASE}/${encodeURIComponent(model)}/${encodeURIComponent(runKey)}/vars`,
    { ...options, productId: model }
  );
}

export async function fetchManifest(
  model: string,
  run: string,
  region?: string | null,
  ensembleView?: string | null,
  options?: FetchOptions
): Promise<RunManifestResponse> {
  const runKey = run || "latest";
  const query = new URLSearchParams();
  if (region) {
    query.set("region", region);
  }
  if (ensembleView) {
    query.set("ensemble_view", ensembleView);
  }
  const payload = await fetchJson<unknown>(
    `${API_V4_BASE}/${encodeURIComponent(model)}/${encodeURIComponent(runKey)}/manifest${query.toString() ? `?${query.toString()}` : ""}`,
    {
      ...options,
      productId: model,
      diagnosticMetricName: options?.diagnosticMetricName ?? "manifest_fetch_duration",
      diagnosticMeta: {
        ...(options?.diagnosticMeta ?? {}),
        model_id: model,
        run_id: runKey,
        region: region ?? null,
      },
    }
  );
  if (!isRunManifestResponse(payload)) {
    throw new Error("Invalid manifest response shape");
  }
  return payload;
}

export async function fetchFrames(
  model: string,
  run: string,
  varKey: string,
  region?: string | null,
  ensembleView?: string | null,
  options?: FetchOptions
): Promise<FrameRow[]> {
  const runKey = run || "latest";
  const query = new URLSearchParams();
  if (region) {
    query.set("region", region);
  }
  if (ensembleView) {
    query.set("ensemble_view", ensembleView);
  }
  const response = await fetchJson<FrameRow[]>(
    `${API_V4_BASE}/${encodeURIComponent(model)}/${encodeURIComponent(runKey)}/${encodeURIComponent(varKey)}/frames${query.toString() ? `?${query.toString()}` : ""}`,
    {
      ...options,
      productId: model,
      diagnosticMetricName: options?.diagnosticMetricName ?? "frames_fetch_duration",
      diagnosticMeta: {
        ...(options?.diagnosticMeta ?? {}),
        model_id: model,
        run_id: runKey,
        variable_id: varKey,
        region: region ?? null,
      },
    }
  );
  if (!Array.isArray(response)) {
    return [];
  }
  return response
    .filter((row) => row && Number.isFinite(Number(row.fh)))
    .map((row) => {
      const nestedValidTime = row?.meta?.meta?.valid_time;
      return {
        ...row,
        valid_time:
          typeof row?.valid_time === "string" && row.valid_time.trim()
            ? row.valid_time.trim()
            : typeof nestedValidTime === "string" && nestedValidTime.trim()
              ? nestedValidTime.trim()
              : undefined,
      };
    })
    .sort((a, b) => Number(a.fh) - Number(b.fh));
}

// In-flight dedup: the viewer fires concurrent grid-manifest requests for the
// same URL (latest-run probes, composite layers, background refresh). Shared
// requests intentionally carry NO abort signal — an aborting caller just
// abandons its await (all call sites re-check their own signal after awaiting),
// so one caller's abort can't cancel the response for the others.
const gridManifestInflight = new Map<string, Promise<GridManifestResponse | null>>();

export function fetchGridManifest(
  model: string,
  run: string,
  varKey: string,
  region?: string | null,
  ensembleView?: string | null,
  options?: FetchOptions
): Promise<GridManifestResponse | null> {
  const runKey = run || "latest";
  const query = new URLSearchParams();
  if (region) {
    query.set("region", region);
  }
  if (ensembleView) {
    query.set("ensemble_view", ensembleView);
  }
  const url = `${API_V4_BASE}/${encodeURIComponent(model)}/${encodeURIComponent(runKey)}/${encodeURIComponent(varKey)}/grid-manifest${query.toString() ? `?${query.toString()}` : ""}`;

  const inflight = gridManifestInflight.get(url);
  if (inflight) {
    return inflight;
  }

  const request = (async (): Promise<GridManifestResponse | null> => {
    try {
      const response = await fetchJson<GridManifestResponse>(
        url,
        {
          ...options,
          signal: undefined,
          productId: model,
          diagnosticMetricName: options?.diagnosticMetricName ?? "grid_manifest_fetch_duration",
          diagnosticMeta: {
            ...(options?.diagnosticMeta ?? {}),
            model_id: model,
            run_id: runKey,
            variable_id: varKey,
            region: region ?? null,
          },
        }
      );
      if (
        !response
        || !Array.isArray(response.lods)
        || !response.grid
        || !Number.isFinite(Number(response.grid.width))
        || !Number.isFinite(Number(response.grid.height))
      ) {
        return null;
      }
      return response;
    } catch {
      return null;
    } finally {
      gridManifestInflight.delete(url);
    }
  })();

  gridManifestInflight.set(url, request);
  return request;
}

export async function fetchRgbManifest(
  model: string,
  run: string,
  variable: string,
  options?: FetchOptions,
): Promise<RgbManifestResponse> {
  return fetchJson<RgbManifestResponse>(
    `${API_V4_BASE}/${encodeURIComponent(model)}/${encodeURIComponent(run || "latest")}/${encodeURIComponent(variable)}/rgb-manifest`,
    { ...options, productId: model },
  );
}


export async function fetchAnchorFeatureCollection(options?: FetchOptions): Promise<AnchorFeatureCollection> {
  const response = await publicFetch("/data/anchors_conus.geojson", {
    credentials: "omit",
    signal: options?.signal,
  });
  if (!response.ok) {
    throw new Error(`Anchor data request failed: ${response.status} ${response.statusText}`);
  }
  const payload = (await response.json()) as unknown;
  if (!isAnchorFeatureCollection(payload)) {
    throw new Error("Invalid anchor GeoJSON shape");
  }
  return payload;
}

export async function fetchSampleBatch(params: {
  model: string;
  run: string;
  variable: string;
  ensembleView?: string | null;
  forecastHour: number;
  points: AnchorBatchPoint[];
  signal?: AbortSignal;
}): Promise<AnchorBatchResponse | null> {
  const startedAtMs = startNetworkTimer();
  const response = await productFetch(params.model, `${API_V4_BASE}/sample/batch`, {
    method: "POST",
    credentials: "omit",
    signal: params.signal,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: params.model,
      run: params.run,
      variable: params.variable,
      ensemble_view: params.ensembleView ?? null,
      forecast_hour: params.forecastHour,
      points: params.points,
    }),
  });
  trackNetworkFetchDuration({
    metric_name: "sample_batch_request_duration",
    started_at_ms: startedAtMs,
    response,
    model_id: params.model,
    variable_id: params.variable,
    run_id: params.run,
    forecast_hour: params.forecastHour,
    meta: {
      point_count: params.points.length,
    },
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Batch sample request failed: ${response.status}`);
  }

  const payload = (await response.json()) as Partial<AnchorBatchResponse>;
  return {
    units: typeof payload.units === "string" ? payload.units : "",
    values: payload.values && typeof payload.values === "object" ? payload.values : {},
  };
}

// ── Sample (hover-for-data) ──────────────────────────────────────────

export type SampleResult = {
  value: number;
  units: string;
  model: string;
  run?: string;
  var: string;
  fh: number;
  valid_time: string;
  lat: number;
  lon: number;
  noData: boolean;
  label?: string;
  desc?: string;
};

export async function fetchSample(params: {
  model: string;
  run: string;
  var: string;
  ensembleView?: string | null;
  fh: number;
  lat: number;
  lon: number;
  signal?: AbortSignal;
}): Promise<SampleResult | null> {
  const qs = new URLSearchParams({
    model: params.model,
    run: params.run,
    var: params.var,
    fh: String(params.fh),
    lat: String(params.lat),
    lon: String(params.lon),
  });
  if (params.ensembleView) {
    qs.set("ensemble_view", params.ensembleView);
  }
  const startedAtMs = startNetworkTimer();
  const response = await productFetch(params.model, `${API_V4_BASE}/sample?${qs}`, { credentials: "omit", signal: params.signal });
  trackNetworkFetchDuration({
    metric_name: "sample_request_duration",
    started_at_ms: startedAtMs,
    response,
    model_id: params.model,
    variable_id: params.var,
    run_id: params.run,
    forecast_hour: params.fh,
    meta: {
      lat: params.lat,
      lon: params.lon,
    },
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Sample request failed: ${response.status}`);
  }
  const payload = (await response.json()) as SampleResult;
  if (payload.noData || payload.value === null || Number.isNaN(Number(payload.value))) {
    return null;
  }
  return {
    ...payload,
    value: Number(payload.value),
  };
}

export function buildContourUrl(params: {
  model: string;
  run: string;
  varKey: string;
  fh: number;
  key: string;
}): string {
  const enc = encodeURIComponent;
  return `${API_V4_BASE}/${enc(params.model)}/${enc(params.run)}/${enc(params.varKey)}/${enc(params.fh)}/contours/${enc(params.key)}`;
}

// ── NWS Anchor City Weather ──────────────────────────────────────────

export type NwsObservation = {
  stationName: string | null;
  stationId: string | null;
  observedAt: string | null;
  tempF: number | null;
  dewpointF: number | null;
  relativeHumidity: number | null;
  windDirection: string | null;
  windSpeedMph: number | null;
  windGustMph: number | null;
  windChillF: number | null;
  heatIndexF: number | null;
  pressureInHg: number | null;
  visibilityMi: number | null;
  textDescription: string | null;
  precipLastHourIn: number | null;
};

export type NwsForecastPeriod = {
  number: number;
  name: string;
  isDaytime: boolean;
  tempF: number | null;
  windSpeed: string | null;
  windDirection: string | null;
  shortForecast: string | null;
  detailedForecast: string | null;
  precipProbability: number | null;
};

export type NwsForecast = {
  generatedAt: string | null;
  periods: NwsForecastPeriod[];
};

export type NwsWeatherMeta = {
  anchorId: string;
  resolvedFromCache: boolean;
  observationDegraded: boolean | null;
  observationStationFallbackUsed: boolean | null;
  stationsAttempted: number;
};

export type AnchorWeatherResponse = {
  city: string;
  state: string;
  st: string;
  observation: NwsObservation | null;
  forecast: NwsForecast | null;
  meta: NwsWeatherMeta;
};

export type AnchorAfdResponse = {
  wfo: string;
  officeName: string | null;
  issuedAt: string | null;
  productText: string | null;
  meta: {
    anchorId: string;
    productId: string | null;
  };
};

export type AnchorAfdEmptyResponse = {
  afd: null;
  reason: string;
  meta: {
    anchorId: string;
  };
};

export type NwsHazardAlertDetail = {
  id: string;
  source: "nws";
  event: string | null;
  headline: string | null;
  severity: string | null;
  urgency: string | null;
  certainty: string | null;
  sent: string | null;
  effective: string | null;
  expires: string | null;
  area_description: string | null;
  areas: string[];
  description: string | null;
  instruction: string | null;
};

export async function fetchNwsHazardAlertDetail(
  alertId: string,
  signal?: AbortSignal,
): Promise<NwsHazardAlertDetail | null> {
  const url = `${API_V4_BASE}/nws-hazards/alert?id=${encodeURIComponent(alertId)}`;
  try {
    const response = await publicFetch(url, { credentials: "omit", signal });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(`NWS hazard alert request failed: ${response.status}`);
    }
    return (await response.json()) as NwsHazardAlertDetail;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return null;
    }
    throw err;
  }
}

export async function fetchAnchorWeather(
  anchorId: string,
  signal?: AbortSignal,
): Promise<AnchorWeatherResponse | null> {
  const url = `${API_V4_BASE}/anchors/${encodeURIComponent(anchorId)}/weather`;
  try {
    const response = await publicFetch(url, { credentials: "omit", signal });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(`Anchor weather request failed: ${response.status}`);
    }
    return (await response.json()) as AnchorWeatherResponse;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return null;
    }
    throw err;
  }
}

export async function fetchAnchorAfd(
  anchorId: string,
  signal?: AbortSignal,
): Promise<AnchorAfdResponse | null> {
  const url = `${API_V4_BASE}/anchors/${encodeURIComponent(anchorId)}/afd`;
  try {
    const response = await publicFetch(url, { credentials: "omit", signal });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(`Anchor AFD request failed: ${response.status}`);
    }
    const payload = await response.json();
    // Handle the "no AFD available" shape
    if (payload && payload.afd === null) {
      return null;
    }
    return payload as AnchorAfdResponse;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return null;
    }
    throw err;
  }
}
