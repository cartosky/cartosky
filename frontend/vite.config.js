import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
// Overridable so a second dev session can proxy to its own backend instance
// (kept in sync with vite.config.ts — this compiled artifact shadows it).
var DEV_PROXY_TARGET = process.env.CARTOSKY_DEV_PROXY_TARGET || "http://127.0.0.1:8200";
var DEV_PROXY_PATHS = ["/api", "/auth", "/twf", "/tiles", "/static"];
var DEV_SERVER_PROXY = Object.fromEntries(DEV_PROXY_PATHS.map(function (path) { return [path, { target: DEV_PROXY_TARGET, changeOrigin: true }]; }));
export default defineConfig({
    base: "/",
    plugins: [react()],
    resolve: {
        alias: {
            "@": fileURLToPath(new URL("./src", import.meta.url)),
        },
    },
    server: {
        host: true,
        proxy: DEV_SERVER_PROXY,
    },
    build: {
        rollupOptions: {
            output: {
                manualChunks: function (id) {
                    if (id.includes("/src/lib/utils.")) {
                        return "utils";
                    }
                    if (!id.includes("node_modules")) {
                        return undefined;
                    }
                    if (id.includes("clsx") || id.includes("tailwind-merge")) {
                        return "utils";
                    }
                    if (id.includes("maplibre-gl")) {
                        return "maplibre";
                    }
                    if (id.includes("@clerk/")) {
                        return "clerk";
                    }
                    if (id.includes("@radix-ui/")) {
                        return "radix";
                    }
                    if (id.includes("recharts")) {
                        return "recharts";
                    }
                    if (id.includes("@posthog/") || id.includes("posthog-js")) {
                        return "posthog";
                    }
                    if (id.includes("lucide-react")) {
                        return "icons";
                    }
                    if (id.includes("react-router") || id.includes("@remix-run/router")) {
                        return "router";
                    }
                    return undefined;
                },
            },
        },
    },
});
