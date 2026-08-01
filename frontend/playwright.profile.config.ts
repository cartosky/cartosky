/// <reference types="node" />
/**
 * Profiling config for the globe performance pass — safe to delete.
 *
 * Runs a PRIVATE dev server on port 4773 so the run is isolated from the shared
 * port-4173 dev server that concurrent sessions start, reuse, and tear down
 * mid-run. Dev (not preview) on purpose: the CPU profile is attributed by
 * function name, and a production build minifies them away; `__cartoskyGifDriver`
 * (the timing probe) is also dev-only.
 *
 * Usage:
 *   npx playwright test tests/e2e/globe-perf-profile.spec.ts \
 *     --config playwright.profile.config.ts --workers=1
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 600_000,
  retries: 0,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:4773',
    trace: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-gpu'],
        },
      },
    },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4773 --strictPort',
    url: 'http://127.0.0.1:4773',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
