/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_TILES_BASE?: string;
  readonly VITE_CARTOSKY_WEBP_DEFAULT_ENABLED?: string;
  readonly VITE_TWF_V3_WEBP_DEFAULT_ENABLED?: string;
  readonly VITE_CARTOSKY_ADMIN_EMBEDS_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
