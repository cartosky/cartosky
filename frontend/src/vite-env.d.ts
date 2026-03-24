/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_TILES_BASE?: string;
  readonly VITE_CARTOSKY_WEBP_DEFAULT_ENABLED?: string;
  readonly VITE_TWF_V3_WEBP_DEFAULT_ENABLED?: string;
  readonly VITE_CARTOSKY_ADMIN_EMBEDS_ENABLED?: string;
  readonly VITE_CARTOSKY_WEB_VITALS_ENABLED?: string;
  readonly VITE_CARTOSKY_RUM_ENABLED?: string;
  readonly VITE_CARTOSKY_POSTHOG_ENABLED?: string;
  readonly VITE_CARTOSKY_POSTHOG_REPLAY_ENABLED?: string;
  readonly VITE_CARTOSKY_POSTHOG_API_KEY?: string;
  readonly VITE_CARTOSKY_POSTHOG_HOST?: string;
  readonly VITE_CARTOSKY_POSTHOG_UI_HOST?: string;
  readonly VITE_CARTOSKY_POSTHOG_DASHBOARD_URL?: string;
  readonly VITE_CARTOSKY_POSTHOG_DASHBOARD_EMBED_URL?: string;
  readonly VITE_CARTOSKY_POSTHOG_REPLAY_URL?: string;
  readonly VITE_RELEASE_SHA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
