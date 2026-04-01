const API_ORIGIN_ENV = String(import.meta.env.VITE_API_BASE ?? "").trim();
export const API_ORIGIN = (API_ORIGIN_ENV || "https://api.cartosky.com").replace(/\/$/, "");
export const API_V4_BASE = `${API_ORIGIN}/api/v4`;

const TILES_BASE_ENV = String(import.meta.env.VITE_TILES_BASE ?? "").trim();
export const TILES_BASE = (TILES_BASE_ENV || API_ORIGIN).replace(/\/$/, "");
const POSTHOG_API_KEY_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_API_KEY ?? "").trim();
const POSTHOG_HOST_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_HOST ?? "").trim();
const POSTHOG_UI_HOST_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_UI_HOST ?? "").trim();
const POSTHOG_DASHBOARD_URL_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_DASHBOARD_URL ?? "").trim();
const POSTHOG_DASHBOARD_EMBED_URL_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_DASHBOARD_EMBED_URL ?? "").trim();
const POSTHOG_REPLAY_URL_ENV = String(import.meta.env.VITE_CARTOSKY_POSTHOG_REPLAY_URL ?? "").trim();
const GRAFANA_URL_ENV = String(import.meta.env.VITE_CARTOSKY_GRAFANA_URL ?? "").trim();
const GRAFANA_DASHBOARD_URL_ENV = String(import.meta.env.VITE_CARTOSKY_GRAFANA_DASHBOARD_URL ?? "").trim();
const GRAFANA_EMBED_URL_ENV = String(import.meta.env.VITE_CARTOSKY_GRAFANA_EMBED_URL ?? "").trim();
const GRAFANA_TRACES_URL_ENV = String(import.meta.env.VITE_CARTOSKY_GRAFANA_TRACES_URL ?? "").trim();
const RELEASE_SHA_ENV = String(import.meta.env.VITE_RELEASE_SHA ?? "").trim();

export const WEBP_RENDER_MODE_THRESHOLDS = {
  tier0Max: 5.8,
  hysteresis: 0.2,
  dwellMs: 200,
};

export type WeatherSubstrate = "grid_webgl_v1";

export type CanonicalSingleWebpTierMode = "webp_tier0";

export function getCanonicalSingleWebpTierMode(): CanonicalSingleWebpTierMode {
  return "webp_tier0";
}

export function normalizeWeatherSubstrate(value: unknown): WeatherSubstrate | null {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  if (normalized === "grid" || normalized === "grid_webgl_v1") {
    return "grid_webgl_v1";
  }
  return null;
}

export const MAP_VIEW_DEFAULTS = {
  region: "conus",
  center: [39.83, -98.58] as [number, number],
  zoom: 4,
};

export const OVERLAY_DEFAULT_OPACITY = 0.9;

export function isWebpDefaultRenderEnabled(): boolean {
  return readBooleanEnv(
    import.meta.env.VITE_CARTOSKY_WEBP_DEFAULT_ENABLED ?? import.meta.env.VITE_TWF_V3_WEBP_DEFAULT_ENABLED,
    true,
  );
}

export function isGridV1Enabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_GRID_V1_ENABLED, false);
}

export function isGridV1DefaultEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_GRID_V1_DEFAULT_ENABLED, false);
}

function readBooleanEnv(value: unknown, fallback: boolean): boolean {
  const envValue = String(value ?? "").trim().toLowerCase();
  if (envValue === "1" || envValue === "true" || envValue === "yes" || envValue === "on") {
    return true;
  }
  if (envValue === "0" || envValue === "false" || envValue === "no" || envValue === "off") {
    return false;
  }
  return fallback;
}

export function isTileFirstInitialPaintEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_TILE_FIRST_INITIAL_PAINT, true);
}

export function isDeferredNonCriticalBootstrapEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_DEFER_NON_CRITICAL_BOOTSTRAP, true);
}

export function isDeferredPrefetchUntilFirstPaintEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_DEFER_PREFETCH_UNTIL_FIRST_PAINT, true);
}

export function isViewportAwareTileReadinessEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_VIEWPORT_AWARE_TILE_READINESS, false);
}

export function isAdminEmbedsEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_ADMIN_EMBEDS_ENABLED, false);
}

export function isWebVitalsEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_WEB_VITALS_ENABLED, false);
}

export function isRumEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_RUM_ENABLED, false);
}

export function isLegacyPerfTelemetryEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_LEGACY_PERF_TELEMETRY_ENABLED, true);
}

export function isLegacyUsageTelemetryEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_LEGACY_USAGE_TELEMETRY_ENABLED, true);
}

export function isPostHogEnabled(): boolean {
  return (
    readBooleanEnv(import.meta.env.VITE_CARTOSKY_POSTHOG_ENABLED, false)
    && POSTHOG_API_KEY_ENV.length > 0
    && POSTHOG_HOST_ENV.length > 0
  );
}

export function isPostHogReplayEnabled(): boolean {
  return isPostHogEnabled() && readBooleanEnv(import.meta.env.VITE_CARTOSKY_POSTHOG_REPLAY_ENABLED, false);
}

export function getPostHogApiKey(): string {
  return POSTHOG_API_KEY_ENV;
}

export function getPostHogHost(): string {
  return POSTHOG_HOST_ENV.replace(/\/$/, "");
}

export function getPostHogUiHost(): string | null {
  const value = POSTHOG_UI_HOST_ENV.replace(/\/$/, "");
  return value.length > 0 ? value : null;
}

export function getPostHogDashboardUrl(): string | null {
  const value = POSTHOG_DASHBOARD_URL_ENV.trim();
  return value.length > 0 ? value : null;
}

export function getPostHogDashboardEmbedUrl(): string | null {
  const value = POSTHOG_DASHBOARD_EMBED_URL_ENV.trim();
  return value.length > 0 ? value : null;
}

export function getPostHogReplayUrl(): string | null {
  const value = POSTHOG_REPLAY_URL_ENV.trim();
  return value.length > 0 ? value : null;
}

export function getReleaseSha(): string | null {
  return RELEASE_SHA_ENV.length > 0 ? RELEASE_SHA_ENV : null;
}

export function getGrafanaUrl(): string | null {
  const value = GRAFANA_URL_ENV.replace(/\/$/, "");
  return value.length > 0 ? value : null;
}

export function getGrafanaDashboardUrl(): string | null {
  const value = GRAFANA_DASHBOARD_URL_ENV.trim();
  return value.length > 0 ? value : null;
}

export function getGrafanaEmbedUrl(): string | null {
  const value = GRAFANA_EMBED_URL_ENV.trim();
  return value.length > 0 ? value : null;
}

export function getGrafanaTracesUrl(): string | null {
  const value = GRAFANA_TRACES_URL_ENV.trim();
  return value.length > 0 ? value : null;
}
