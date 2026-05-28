import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

const DEV_PROXY_TARGET = "https://api.cartosky.com";
const DEV_PROXY_PATHS = ["/api", "/auth", "/twf", "/tiles"] as const;
const DEV_SERVER_PROXY: Record<string, { target: string; changeOrigin: boolean }> = Object.fromEntries(
  DEV_PROXY_PATHS.map((path) => [path, { target: DEV_PROXY_TARGET, changeOrigin: true }]),
);

export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: DEV_SERVER_PROXY,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl"],
        },
      },
    },
  },
});