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

export type WeatherSubstrate = "grid" | "vector";

export const MAP_VIEW_DEFAULTS = {
  region: "conus",
  center: [39.83, -98.58] as [number, number],
  zoom: 4,
};

export const OVERLAY_DEFAULT_OPACITY = 0.9;

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

function readNumberEnv(value: unknown, fallback: number, min: number, max: number): number {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, parsed));
}

export function isDeferredNonCriticalBootstrapEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_DEFER_NON_CRITICAL_BOOTSTRAP, true);
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

export function getRumDiagnosticsSampleRate(): number {
  return readNumberEnv(import.meta.env.VITE_CARTOSKY_RUM_DIAGNOSTICS_SAMPLE_RATE, 1, 0, 1);
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
