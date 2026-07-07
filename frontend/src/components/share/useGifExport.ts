// Client-side GIF export (share overhaul Phase 3, plan §3.2): step forecast
// hours on the live map via the frame driver, compose each frame like the
// still exporter (overlay/legend/logo), and stream frames into a gifenc Web
// Worker. Zero server involvement. Preview-as-artifact: the ready state holds
// the actual encoded GIF the user downloads/shares.

import { useCallback, useEffect, useRef, useState } from "react";

import type { LegendPayload } from "@/components/map-legend";
import type { ScreenshotExportState } from "@/lib/screenshot_export";

export type GifTrendRun = {
  runId: string;
  runTimeISO: string;
  /** Display label burned into each trend frame's overlay. */
  label: string;
};

/** Implemented by the viewer (App.tsx) over its live frame pipeline. */
export type GifFrameDriver = {
  /** Frame hours available for the current selection (sorted); [] when GIF unsupported. */
  listFrameHours: () => number[];
  /** Stop autoplay/playback before stepping. */
  begin: () => void;
  /** Set + await the frame (per-frame dual gate: bytes ready AND presented). */
  showFrame: (hour: number) => Promise<boolean>;
  /** Repaint-then-read snapshot of the live canvas, downscaled to maxWidth. */
  captureFrame: (maxWidth?: number, expectGridHour?: number | null) => Promise<HTMLCanvasElement | null>;
  /** Valid time of the currently displayed frame (after showFrame resolves). */
  getDisplayedValidTimeISO: () => string | null;
  /** Opaque timeline snapshot taken before stepping; handed back to restore(). */
  getRestoreTarget: () => unknown;
  restore: (token: unknown) => void;
  // ── Run-trend support (plan §3.2 trends mode) ────────────────────────────
  /** Whether run-trend GIFs make sense here (forecast-axis grid, ≥2 runs). */
  supportsRunTrend: () => boolean;
  /** Last-N runs for the current selection, newest first. */
  listRecentRuns: (count: number) => GifTrendRun[];
  /** Valid time for `hour` on the currently resolved run. */
  validTimeForHour: (hour: number) => string | null;
  /** Switch the viewer to `runId` and present the frame whose valid time is
   * nearest `validTimeISO` (per-run cadence snap); resolves shown:false when
   * the run has no frame within tolerance (missing/evicted run → skip). */
  showRunFrame: (
    runId: string,
    validTimeISO: string,
  ) => Promise<{ shown: boolean; fh: number | null; validTimeISO: string | null }>;
};

export type GifExportStatus = "idle" | "capturing" | "encoding" | "ready" | "error" | "cancelled";

export type GifExportPlan = {
  /** Frames that would be captured (after the device cap). */
  frameCount: number;
  totalHours: number;
  estimatedBytes: number;
  playSeconds: number;
};

type WorkerOutMessage =
  | { type: "frame-encoded"; index: number }
  | { type: "done"; buffer: ArrayBuffer }
  | { type: "error"; message: string };

// §7 defaults: 720px-wide output; hard frame cap 60 desktop / 30 mobile;
// 200ms per frame (~5 fps) with a 1.2s hold on the final frame.
const GIF_OUTPUT_WIDTH = 720;
const GIF_FRAME_CAP_DESKTOP = 60;
const GIF_FRAME_CAP_MOBILE = 30;
const GIF_DEFAULT_FRAME_DELAY_MS = 200;
const GIF_END_HOLD_MS = 1200;
// GIF frames render at 720px/pixelRatio 1; the still exporter's width-derived
// chrome scale (720/1280 ≈ 0.56) leaves overlay/logo/legend small and blurry.
// 0.65 balances legibility (with flat shadows + integer-aligned draws doing
// the crispness work) against how much map the chrome covers — 0.8 read as
// oversized in gate feedback.
// Final chrome scale for GIF frames (composeShareFrame uses explicit values
// as-is). ~9px overlay text at 720px wide — the smallest that stays legible
// through 256-color quantization; verified at 0.55 (2026-07-07).
const GIF_CHROME_SCALE = 0.55;
// In-modal range-preview thumbnail width (the modal blurs/covers the live map,
// so slider scrubbing renders its own small snapshot inside the GIF tab).
// Matches GIF_OUTPUT_WIDTH so the (now full-modal-width) idle preview stays
// sharp on desktop.
const GIF_RANGE_PREVIEW_WIDTH = 720;

export type GifExportMode = "hours" | "trend";

/** User-tunable generation settings (GIF tab controls). */
export type GifExportSettings = {
  /** Forecast-hour loop vs run-over-run trend at one valid time (§3.2). */
  mode: GifExportMode;
  /** First forecast hour to include; null = first available. (hours mode) */
  startHour: number | null;
  /** Last forecast hour to include; null = last available. (hours mode) */
  endHour: number | null;
  /** Hour (on the current run) whose valid time the trend aligns to; null =
   * the currently displayed hour. (trend mode) */
  trendHour: number | null;
  /** How many recent runs to compare (trend mode). A count, never run ids —
   * ids resolve fresh at generate time (runs churn as new ones publish). */
  trendRunCount: number;
  /** Per-frame delay in ms (speed control). */
  delayMs: number;
};

export const GIF_SPEED_PRESETS: Array<{ id: string; label: string; delayMs: number }> = [
  { id: "slow", label: "Slow", delayMs: 350 },
  { id: "normal", label: "Normal", delayMs: GIF_DEFAULT_FRAME_DELAY_MS },
  { id: "fast", label: "Fast", delayMs: 120 },
];

// Trend loops hold each run long enough to compare; ~1s feels right at 3 frames.
export const GIF_TREND_SPEED_PRESETS: Array<{ id: string; label: string; delayMs: number }> = [
  { id: "slow", label: "Slow", delayMs: 1400 },
  { id: "normal", label: "Normal", delayMs: 1000 },
  { id: "fast", label: "Fast", delayMs: 600 },
];

// §7 (revised post-gate): user-selectable run count, 2..6. Only the COUNT is
// stored in settings — the actual run ids resolve from the live runs list at
// render/generate time, so runs publishing or aging out while the modal is
// open can't leave a stale selection.
export const GIF_TREND_RUN_DEFAULT = 3;
export const GIF_TREND_RUN_MAX = 6;

const DEFAULT_GIF_SETTINGS: GifExportSettings = {
  mode: "hours",
  startHour: null,
  endHour: null,
  trendHour: null,
  trendRunCount: GIF_TREND_RUN_DEFAULT,
  delayMs: GIF_DEFAULT_FRAME_DELAY_MS,
};

function clampTrendRunCount(count: number): number {
  return Math.min(Math.max(Math.round(count), 2), GIF_TREND_RUN_MAX);
}

function applyHourRange(hours: number[], settings: GifExportSettings): number[] {
  return hours.filter(
    (hour) =>
      (settings.startHour === null || hour >= settings.startHour) &&
      (settings.endHour === null || hour <= settings.endHour),
  );
}
// Rough per-frame size heuristic for the pre-generate estimate (720px map
// frames typically land 30–60KB after palette encoding).
const GIF_ESTIMATED_BYTES_PER_FRAME = 45_000;

function frameCapForState(state: ScreenshotExportState | null): number {
  return state?.isMobile ? GIF_FRAME_CAP_MOBILE : GIF_FRAME_CAP_DESKTOP;
}

/** Evenly stride `hours` down to at most `cap` entries, keeping first + last. */
function sampleHours(hours: number[], cap: number): number[] {
  if (hours.length <= cap) {
    return [...hours];
  }
  const sampled: number[] = [];
  const step = (hours.length - 1) / (cap - 1);
  for (let i = 0; i < cap; i += 1) {
    sampled.push(hours[Math.round(i * step)]);
  }
  return [...new Set(sampled)];
}

export type UseGifExportParams = {
  open: boolean;
  frameDriver?: GifFrameDriver;
  buildScreenshotState?: () => ScreenshotExportState | null;
  getLegend?: () => LegendPayload | null;
};

export function useGifExport({
  open,
  frameDriver,
  buildScreenshotState,
  getLegend,
}: UseGifExportParams) {
  const [status, setStatus] = useState<GifExportStatus>("idle");
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [gifBlob, setGifBlob] = useState<Blob | null>(null);
  const [gifBlobUrl, setGifBlobUrl] = useState<string | null>(null);
  const [gifFrameCount, setGifFrameCount] = useState(0);
  const [settings, setSettings] = useState<GifExportSettings>(DEFAULT_GIF_SETTINGS);
  const [rangePreview, setRangePreview] = useState<{ url: string; hour: number } | null>(null);
  const previewTokenRef = useRef(0);

  const abortedRef = useRef(false);
  const runningRef = useRef(false);
  const workerRef = useRef<Worker | null>(null);
  // Timeline snapshot from BEFORE the first driver interaction this open
  // (range preview or generate); restored on generate end and on modal close.
  const originalTimelineRef = useRef<unknown | null>(null);

  const availableHours = frameDriver?.listFrameHours() ?? [];
  const available = Boolean(frameDriver) && availableHours.length >= 2;
  const trendAvailable = Boolean(frameDriver?.supportsRunTrend());
  // Resolved fresh each render from the live runs list (never cached in
  // settings): new runs publishing or old ones aging out while the modal is
  // open update the selectable count automatically.
  const trendRunsAvailable = trendAvailable
    ? (frameDriver?.listRecentRuns(GIF_TREND_RUN_MAX).length ?? 0)
    : 0;

  const updateSettings = useCallback((update: Partial<GifExportSettings>) => {
    setSettings((current) => ({ ...current, ...update }));
  }, []);

  /** Step the live map to `hour` and refresh the in-modal thumbnail so the
   * user sees what a range handle points at (the modal covers/blurs the live
   * map). Fire-and-forget; only the latest request updates the thumbnail, and
   * the original timeline is restored on modal close. */
  const previewFrame = useCallback((hour: number) => {
    if (!frameDriver || runningRef.current) {
      return;
    }
    if (originalTimelineRef.current === null) {
      originalTimelineRef.current = frameDriver.getRestoreTarget();
    }
    frameDriver.begin();
    const token = ++previewTokenRef.current;
    void (async () => {
      const shown = await frameDriver.showFrame(hour);
      if (!shown || token !== previewTokenRef.current || runningRef.current) {
        return;
      }
      const canvas = await frameDriver.captureFrame(GIF_RANGE_PREVIEW_WIDTH, hour);
      if (!canvas || token !== previewTokenRef.current || runningRef.current) {
        return;
      }
      try {
        setRangePreview({ url: canvas.toDataURL("image/jpeg", 0.75), hour });
      } catch {
        // Canvas read failed — keep the previous thumbnail.
      }
    })();
  }, [frameDriver]);

  /** Pre-generate summary shown in the idle state (§3.2: estimated size up front). */
  const buildPlan = useCallback((): GifExportPlan | null => {
    if (!frameDriver) {
      return null;
    }
    if (settings.mode === "trend") {
      const runCount = frameDriver.listRecentRuns(clampTrendRunCount(settings.trendRunCount)).length;
      if (runCount < 2) {
        return null;
      }
      return {
        frameCount: runCount,
        totalHours: runCount,
        estimatedBytes: runCount * GIF_ESTIMATED_BYTES_PER_FRAME,
        playSeconds: ((runCount - 1) * settings.delayMs + Math.max(GIF_END_HOLD_MS, settings.delayMs)) / 1000,
      };
    }
    const hours = applyHourRange(frameDriver.listFrameHours(), settings);
    if (hours.length < 2) {
      return null;
    }
    const cap = frameCapForState(buildScreenshotState?.() ?? null);
    const frameCount = Math.min(hours.length, cap);
    return {
      frameCount,
      totalHours: hours.length,
      estimatedBytes: frameCount * GIF_ESTIMATED_BYTES_PER_FRAME,
      playSeconds: ((frameCount - 1) * settings.delayMs + GIF_END_HOLD_MS) / 1000,
    };
  }, [buildScreenshotState, frameDriver, settings]);

  const releaseGif = useCallback(() => {
    setGifBlob(null);
    setGifFrameCount(0);
    setGifBlobUrl((previous) => {
      if (previous) {
        URL.revokeObjectURL(previous);
      }
      return null;
    });
  }, []);

  const reset = useCallback(() => {
    abortedRef.current = true;
    workerRef.current?.terminate();
    workerRef.current = null;
    releaseGif();
    setStatus("idle");
    setError(null);
    setProgress({ done: 0, total: 0 });
  }, [releaseGif]);

  const cancel = useCallback(() => {
    abortedRef.current = true;
  }, []);

  const generate = useCallback(async () => {
    if (runningRef.current || !frameDriver || !buildScreenshotState) {
      return;
    }
    const baseState = buildScreenshotState();
    if (!baseState) {
      setStatus("error");
      setError("Map is still loading. Try again in a moment.");
      return;
    }
    type PlannedGifFrame =
      | { kind: "hour"; hour: number }
      | { kind: "run"; run: GifTrendRun; targetValidISO: string };

    let planned: PlannedGifFrame[];
    if (settings.mode === "trend") {
      // §3.2 trends: same valid time across the last runs, oldest first, with
      // per-run fh resolved by the driver (nearest-frame cadence snap).
      const runsNewestFirst = frameDriver.listRecentRuns(clampTrendRunCount(settings.trendRunCount));
      const anchorHour = settings.trendHour ?? baseState.fh;
      const targetValidISO = frameDriver.validTimeForHour(anchorHour);
      if (runsNewestFirst.length < 2 || !targetValidISO) {
        setStatus("error");
        setError("Run trend needs at least two runs for this selection.");
        return;
      }
      planned = [...runsNewestFirst]
        .reverse()
        .map((run) => ({ kind: "run" as const, run, targetValidISO }));
    } else {
      const hours = applyHourRange(frameDriver.listFrameHours(), settings);
      if (hours.length < 2) {
        setStatus("error");
        setError("This selection doesn't have enough frames to animate.");
        return;
      }
      planned = sampleHours(hours, frameCapForState(baseState)).map((hour) => ({
        kind: "hour" as const,
        hour,
      }));
    }

    runningRef.current = true;
    abortedRef.current = false;
    releaseGif();
    setError(null);
    setStatus("capturing");
    setProgress({ done: 0, total: planned.length });

    if (originalTimelineRef.current === null) {
      originalTimelineRef.current = frameDriver.getRestoreTarget();
    }
    frameDriver.begin();
    const legend = getLegend?.() ?? null;
    const { buildShareOverlayLines, composeShareFrame } = await import("@/lib/screenshot_export");

    let worker: Worker | null = null;
    let composeCanvas: HTMLCanvasElement | null = null;
    let dims: { width: number; height: number } | null = null;
    let written = 0;

    try {
      for (let i = 0; i < planned.length; i += 1) {
        if (abortedRef.current) {
          break;
        }
        const plannedFrame = planned[i];
        let frameState: ScreenshotExportState | null = null;
        if (plannedFrame.kind === "hour") {
          const shown = await frameDriver.showFrame(plannedFrame.hour);
          if (!abortedRef.current && shown) {
            frameState = {
              ...baseState,
              fh: plannedFrame.hour,
              validTimeISO: frameDriver.getDisplayedValidTimeISO() ?? baseState.validTimeISO,
            };
          }
        } else {
          const result = await frameDriver.showRunFrame(
            plannedFrame.run.runId,
            plannedFrame.targetValidISO,
          );
          if (!abortedRef.current && result.shown && result.fh !== null) {
            // Per-frame run label burned into the overlay (§3.2).
            frameState = {
              ...baseState,
              run: plannedFrame.run.label,
              fh: result.fh,
              validTimeISO: result.validTimeISO ?? plannedFrame.targetValidISO,
            };
          }
        }
        if (abortedRef.current) {
          break;
        }
        if (!frameState) {
          // Frame never became ready (eviction, missing run, fetch failure) —
          // skip it rather than aborting the whole run (§3.2 graceful skip).
          setProgress((current) => ({ ...current, done: current.done + 1 }));
          continue;
        }
        // Capture with an atomic in-render grid check: the capture returns
        // null if the grid layer wasn't drawing exactly this hour at read
        // time (run-switch settling can transiently clear the texture), so
        // retry briefly instead of encoding a basemap-only frame.
        let mapCanvas: HTMLCanvasElement | null = null;
        const captureDeadline = performance.now() + 4000;
        for (;;) {
          mapCanvas = await frameDriver.captureFrame(GIF_OUTPUT_WIDTH, frameState.fh);
          if (mapCanvas || abortedRef.current || performance.now() >= captureDeadline) {
            break;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 150));
        }
        if (abortedRef.current) {
          break;
        }
        if (!mapCanvas) {
          setProgress((current) => ({ ...current, done: current.done + 1 }));
          continue;
        }
        if (!dims) {
          const width = Math.min(GIF_OUTPUT_WIDTH, mapCanvas.width);
          const height = Math.max(1, Math.round(width * (mapCanvas.height / mapCanvas.width)));
          dims = { width, height };
          composeCanvas = document.createElement("canvas");
          worker = new Worker(new URL("../../lib/gif_encode_worker.ts", import.meta.url), {
            type: "module",
          });
          workerRef.current = worker;
          worker.postMessage({ type: "start", width, height });
        }

        await composeShareFrame(composeCanvas!, mapCanvas, {
          width: dims.width,
          height: dims.height,
          pixelRatio: 1,
          legend,
          overlayLines: buildShareOverlayLines(frameState, legend),
          isMobile: baseState.isMobile,
          chromeScale: GIF_CHROME_SCALE,
          // Soft shadows quantize into dark banding in the GIF palette.
          chromeShadows: false,
        });
        const composeCtx = composeCanvas!.getContext("2d");
        if (!composeCtx) {
          continue;
        }
        const imageData = composeCtx.getImageData(0, 0, dims.width, dims.height);
        const isLast = i === planned.length - 1;
        worker!.postMessage(
          {
            type: "frame",
            buffer: imageData.data.buffer,
            delay: isLast ? Math.max(GIF_END_HOLD_MS, settings.delayMs) : settings.delayMs,
            index: written,
          },
          [imageData.data.buffer],
        );
        written += 1;
        setProgress({ done: i + 1, total: planned.length });
      }

      if (abortedRef.current) {
        setStatus("cancelled");
        return;
      }
      if (written < 2 || !worker) {
        setStatus("error");
        setError(
          settings.mode === "trend"
            ? "Couldn't capture enough runs at this valid time. Try a different hour."
            : "Couldn't capture enough frames. Try again.",
        );
        return;
      }

      setStatus("encoding");
      const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
        worker!.onmessage = (event: MessageEvent<WorkerOutMessage>) => {
          const message = event.data;
          if (message.type === "done") {
            resolve(message.buffer);
          } else if (message.type === "error") {
            reject(new Error(message.message));
          }
        };
        worker!.onerror = () => reject(new Error("GIF encoding failed."));
        worker!.postMessage({ type: "finish" });
      });
      if (abortedRef.current) {
        setStatus("cancelled");
        return;
      }
      const blob = new Blob([buffer], { type: "image/gif" });
      setGifBlob(blob);
      setGifFrameCount(written);
      setGifBlobUrl((previous) => {
        if (previous) {
          URL.revokeObjectURL(previous);
        }
        return URL.createObjectURL(blob);
      });
      setStatus("ready");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error && err.message ? err.message : "GIF generation failed.");
    } finally {
      runningRef.current = false;
      worker?.terminate();
      workerRef.current = null;
      if (originalTimelineRef.current !== null) {
        frameDriver.restore(originalTimelineRef.current);
      }
    }
  }, [buildScreenshotState, frameDriver, getLegend, releaseGif, settings]);

  // Abort + release everything when the modal closes or the component unmounts.
  // The in-flight generate() loop notices abortedRef and restores the timeline
  // via its own finally block.
  useEffect(() => {
    if (open) {
      return;
    }
    abortedRef.current = true;
    workerRef.current?.terminate();
    workerRef.current = null;
    releaseGif();
    setStatus("idle");
    setError(null);
    setProgress({ done: 0, total: 0 });
    setSettings(DEFAULT_GIF_SETTINGS);
    setRangePreview(null);
    previewTokenRef.current += 1;
    // Undo any range-preview stepping that never went through generate().
    if (originalTimelineRef.current !== null) {
      frameDriver?.restore(originalTimelineRef.current);
      originalTimelineRef.current = null;
    }
  }, [frameDriver, open, releaseGif]);

  useEffect(() => {
    return () => {
      abortedRef.current = true;
      workerRef.current?.terminate();
    };
  }, []);

  return {
    available,
    availableHours,
    trendAvailable,
    trendRunsAvailable,
    settings,
    updateSettings,
    previewFrame,
    rangePreview,
    status,
    progress,
    error,
    gifBlob,
    gifBlobUrl,
    gifFrameCount,
    buildPlan,
    generate,
    cancel,
    reset,
  };
}
