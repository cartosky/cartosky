import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const DEFAULT_OUTPUT_DIR = "/private/tmp/cartosky-phase2-first-paint-20260728";
const RUNS_PER_CASE = 5;
const VIEWPORT = { width: 1440, height: 900 };
const NETWORK_PROFILE = {
  name: "Fast 4G deterministic baseline",
  latencyMs: 40,
  downloadMbps: 10,
  uploadMbps: 5,
};
const CPU_THROTTLE_RATE = 4;
const SCREENSHOT_TARGETS_MS = [100, 250, 500, 1000];
const FRAME_READY_TIMEOUT_MS = 30_000;
const CAPTURE_TIMELINE = process.env.CARTOSKY_CAPTURE_TIMELINE === "1";

const cases = [
  {
    id: "gfs",
    model: "GFS",
    run: "20260728_12z",
    url: "https://cartosky.com/viewer?m=gfs&r=20260728_12z&v=tmp2m&fh=12&reg=conus&lat=39.83&lon=-98.58&z=4&screenshot=1",
  },
  {
    id: "hrrr",
    model: "HRRR",
    run: "20260728_18z",
    url: "https://cartosky.com/viewer?m=hrrr&r=20260728_18z&v=tmp2m&fh=12&reg=conus&lat=39.83&lon=-98.58&z=4&screenshot=1",
  },
];

const outputDir = path.resolve(process.argv[2] || DEFAULT_OUTPUT_DIR);
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  args: [
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-gpu",
  ],
});

const browserVersion = browser.version();
const results = [];

function percentile(values, fraction) {
  const sorted = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (sorted.length === 0) {
    return null;
  }
  const index = Math.max(0, Math.ceil(sorted.length * fraction) - 1);
  return Math.round(sorted[index]);
}

function summarize(caseResults) {
  const metricKeys = [
    "ttfbMs",
    "domContentLoadedMs",
    "loadMs",
    "fcpMs",
    "lcpMs",
    "shellDomMs",
    "shellAfterLoadMs",
    "shellInteractiveMs",
    "gridFrameReadyMs",
    "frameToInteractiveMs",
    "viewerReadyMs",
  ];
  return Object.fromEntries(metricKeys.map((key) => {
    const values = caseResults.map((entry) => entry[key]).filter(Number.isFinite);
    return [
      key,
      {
        median: percentile(values, 0.5),
        p95: percentile(values, 0.95),
        samples: values.length,
      },
    ];
  }));
}

try {
  for (const scenario of cases) {
    for (let runIndex = 1; runIndex <= RUNS_PER_CASE; runIndex += 1) {
      const runId = `${scenario.id}-run-${runIndex}`;
      const runDir = path.join(outputDir, runId);
      await mkdir(runDir, { recursive: true });

      const context = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: 1,
        locale: "en-US",
        timezoneId: "America/Chicago",
        serviceWorkers: "allow",
      });
      await context.tracing.start({
        screenshots: true,
        snapshots: true,
        sources: true,
        title: `CartoSky Phase 2 ${runId}`,
      });
      const page = await context.newPage();
      const cdp = await context.newCDPSession(page);
      await cdp.send("Network.enable");
      await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
      await cdp.send("Network.emulateNetworkConditions", {
        offline: false,
        latency: NETWORK_PROFILE.latencyMs,
        downloadThroughput: NETWORK_PROFILE.downloadMbps * 1024 * 1024 / 8,
        uploadThroughput: NETWORK_PROFILE.uploadMbps * 1024 * 1024 / 8,
        connectionType: "cellular4g",
      });
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU_THROTTLE_RATE });

      await page.addInitScript(() => {
        localStorage.setItem("csky_viewer_tour_v1", "completed");

        const measurement = {
          fcpMs: null,
          lcpMs: null,
          shellDomMs: null,
          shellInteractiveMs: null,
          shellInteractiveCandidateMs: null,
          loadingOverlayFirstMs: null,
          loadingOverlayLastMs: null,
        };
        window.__cartoskyPhase2Measurement = measurement;

        try {
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              if (entry.name === "first-contentful-paint") {
                measurement.fcpMs = Math.round(entry.startTime);
              }
            }
          }).observe({ type: "paint", buffered: true });
        } catch {}

        try {
          new PerformanceObserver((list) => {
            const entries = list.getEntries();
            const latest = entries[entries.length - 1];
            if (latest) {
              measurement.lcpMs = Math.round(latest.startTime);
            }
          }).observe({ type: "largest-contentful-paint", buffered: true });
        } catch {}

        const poll = () => {
          const now = Math.round(performance.now());
          const header = document.querySelector("header");
          const map = document.querySelector('[role="img"][aria-label="Weather map"]');
          const timeline = document.querySelector('[role="slider"]');
          const overlay = Array.from(document.querySelectorAll('[role="status"]'))
            .find((candidate) => {
              if (!(candidate instanceof HTMLElement)) {
                return false;
              }
              const rect = candidate.getBoundingClientRect();
              return getComputedStyle(candidate).position === "fixed"
                && rect.width >= window.innerWidth * 0.9
                && rect.height >= window.innerHeight * 0.9;
            });
          const overlayVisible = overlay instanceof HTMLElement;

          if (overlayVisible) {
            measurement.loadingOverlayFirstMs ??= now;
            measurement.loadingOverlayLastMs = now;
            measurement.shellInteractiveMs = null;
            measurement.shellInteractiveCandidateMs = null;
          }
          if (measurement.shellDomMs === null && header && map && timeline) {
            measurement.shellDomMs = now;
          }
          if (
            measurement.shellInteractiveMs === null
            && header instanceof HTMLElement
            && map
            && timeline
            && measurement.loadingOverlayFirstMs !== null
            && !overlayVisible
          ) {
            const button = Array.from(document.querySelectorAll("button"))
              .find((candidate) => !candidate.disabled && candidate.getBoundingClientRect().width > 32);
            if (button) {
              const rect = button.getBoundingClientRect();
              const hit = document.elementFromPoint(
                rect.left + rect.width / 2,
                rect.top + rect.height / 2,
              );
              if (hit && button.contains(hit)) {
                measurement.shellInteractiveCandidateMs ??= now;
                if (now - measurement.shellInteractiveCandidateMs >= 250) {
                  measurement.shellInteractiveMs = now;
                }
              } else {
                measurement.shellInteractiveCandidateMs = null;
              }
            }
          } else if (measurement.shellInteractiveMs === null) {
            measurement.shellInteractiveCandidateMs = null;
          }
          requestAnimationFrame(poll);
        };
        requestAnimationFrame(poll);
      });

      const consoleErrors = [];
      page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") {
          consoleErrors.push(`console: ${message.text()}`);
        }
      });

      let navigationError = null;
      try {
        await page.goto(scenario.url, { waitUntil: "commit", timeout: 30_000 });
      } catch (error) {
        navigationError = error instanceof Error ? error.message : String(error);
      }

      const screenshotTimings = [];
      if (CAPTURE_TIMELINE) {
        for (const targetMs of SCREENSHOT_TARGETS_MS) {
          const nowMs = await page.evaluate(() => performance.now()).catch(() => targetMs);
          if (nowMs < targetMs) {
            await page.waitForTimeout(targetMs - nowMs);
          }
          const actualMs = await page.evaluate(() => Math.round(performance.now())).catch(() => null);
          const filename = `${runId}-${targetMs}ms.png`;
          await page.screenshot({
            path: path.join(runDir, filename),
            fullPage: false,
          }).catch(() => {});
          screenshotTimings.push({ targetMs, actualMs, filename });
        }
      }

      await page.waitForLoadState("load", { timeout: 20_000 }).catch(() => {});
      let gridFrameTimedOut = false;
      try {
        await page.waitForFunction(
          () => window.__cartoskyGateLog?.some((entry) => entry.event === "grid_frame_ready"),
          null,
          { timeout: FRAME_READY_TIMEOUT_MS },
        );
      } catch {
        gridFrameTimedOut = true;
      }
      await page.waitForTimeout(500);
      await page.screenshot({
        path: path.join(runDir, `${runId}-settled.png`),
        fullPage: false,
      }).catch(() => {});

      const browserMetrics = await page.evaluate(() => {
        const navigation = performance.getEntriesByType("navigation")[0];
        const gateLog = window.__cartoskyGateLog ?? [];
        const gateTime = (eventName) =>
          gateLog.find((entry) => entry.event === eventName)?.tMs ?? null;
        const measurement = window.__cartoskyPhase2Measurement ?? {};
        return {
          ttfbMs: navigation ? Math.round(navigation.responseStart) : null,
          domContentLoadedMs: navigation ? Math.round(navigation.domContentLoadedEventEnd) : null,
          loadMs: navigation ? Math.round(navigation.loadEventEnd) : null,
          fcpMs: measurement.fcpMs ?? null,
          lcpMs: measurement.lcpMs ?? null,
          shellDomMs: measurement.shellDomMs ?? null,
          shellInteractiveMs: measurement.shellInteractiveMs ?? null,
          loadingOverlayFirstMs: measurement.loadingOverlayFirstMs ?? null,
          loadingOverlayLastMs: measurement.loadingOverlayLastMs ?? null,
          gridFrameReadyMs: gateTime("grid_frame_ready"),
          viewerReadyMs: gateTime("viewer_ready_set"),
          gateLog,
          finalUrl: location.href,
          finalTitle: document.title,
        };
      }).catch(() => ({
        ttfbMs: null,
        domContentLoadedMs: null,
        loadMs: null,
        fcpMs: null,
        lcpMs: null,
        shellDomMs: null,
        shellInteractiveMs: null,
        loadingOverlayFirstMs: null,
        loadingOverlayLastMs: null,
        gridFrameReadyMs: null,
        viewerReadyMs: null,
        gateLog: [],
        finalUrl: "",
        finalTitle: "",
      }));

      const result = {
        id: runId,
        scenario: scenario.id,
        model: scenario.model,
        pinnedRun: scenario.run,
        requestedUrl: scenario.url,
        browserVersion,
        viewport: VIEWPORT,
        networkProfile: NETWORK_PROFILE,
        cpuThrottleRate: CPU_THROTTLE_RATE,
        cacheDisabled: true,
        freshBrowserContext: true,
        navigationError,
        gridFrameTimedOut,
        screenshotTimings,
        consoleErrors,
        ...browserMetrics,
      };
      result.shellAfterLoadMs = Number.isFinite(result.shellDomMs) && Number.isFinite(result.loadMs)
        ? Math.round(result.shellDomMs - result.loadMs)
        : null;
      result.frameToInteractiveMs = Number.isFinite(result.shellInteractiveMs)
        && Number.isFinite(result.gridFrameReadyMs)
        ? Math.round(result.shellInteractiveMs - result.gridFrameReadyMs)
        : null;
      results.push(result);
      await writeFile(
        path.join(runDir, "metrics.json"),
        `${JSON.stringify(result, null, 2)}\n`,
        "utf8",
      );
      await context.tracing.stop({ path: path.join(runDir, `${runId}.trace.zip`) });
      await context.close();

      console.log(JSON.stringify({
        id: runId,
        fcpMs: result.fcpMs,
        shellInteractiveMs: result.shellInteractiveMs,
        gridFrameReadyMs: result.gridFrameReadyMs,
        timedOut: result.gridFrameTimedOut,
      }));
    }
  }
} finally {
  await browser.close();
}

const summaries = Object.fromEntries(cases.map((scenario) => [
  scenario.id,
  summarize(results.filter((entry) => entry.scenario === scenario.id)),
]));
const report = {
  generatedAt: new Date().toISOString(),
  browserVersion,
  runsPerCase: RUNS_PER_CASE,
  viewport: VIEWPORT,
  networkProfile: NETWORK_PROFILE,
  cpuThrottleRate: CPU_THROTTLE_RATE,
  cacheDisabled: true,
  freshBrowserContextPerRun: true,
  captureTimeline: CAPTURE_TIMELINE,
  firstRunTourSeededComplete: true,
  cases,
  summaries,
  results,
};
await writeFile(path.join(outputDir, "summary.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(`summary=${path.join(outputDir, "summary.json")}`);
