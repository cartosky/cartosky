import { defineConfig } from "vitest/config";
import { fileURLToPath, URL } from "node:url";

/**
 * Unit tests only — pure-logic modules under `src/lib`. Browser-driven suites
 * stay in Playwright (`tests/e2e`, `npm test`), which this config excludes.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    include: ["tests/unit/**/*.test.ts"],
    environment: "node",
  },
});
