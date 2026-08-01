/// <reference types="node" />
/**
 * Verification config for flake-checking e2e suites — safe to delete. Runs
 * against a static production build on a private port, so runs are isolated
 * from the shared port-4173 dev server that concurrent Claude/dev sessions
 * start, reuse, and TEAR DOWN mid-run (reuseExistingServer cuts both ways),
 * and from HMR reloads triggered by live source edits.
 *
 * Usage:
 *   npx vite build --base / --outDir dist-sounding-verify
 *   npx playwright test tests/e2e/sounding-panel.spec.ts \
 *     --config playwright.verify.config.ts --workers=4
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  timeout: 60_000,
  retries: 0,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:4573',
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
    command: 'npx vite preview --host 127.0.0.1 --port 4573 --strictPort --outDir dist-sounding-verify',
    url: 'http://127.0.0.1:4573',
    reuseExistingServer: false,
  },
});
