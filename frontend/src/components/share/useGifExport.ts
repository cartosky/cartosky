// Client-side GIF export (share overhaul Phase 3, plan §3.2): step forecast
// hours on the live map via the frame driver, compose each frame like the
// still exporter (overlay/legend/logo), and stream frames into a gifenc Web
// Worker. Zero server involvement. Preview-as-artifact: the ready state holds
// the actual encoded GIF the user downloads/shares.

import { useCallback, useEffect, useRef, useState } from "react";

import type { LegendPayload } from "@/components/map-legend";
import type { ScreenshotExportState } from "@/lib/screenshot_export";

/** Implemented by the viewer (App.tsx) over its live frame pipeline. */
export type GifFrameDriver = {
  /** Frame hours available for the current selection (sorted); [] when GIF unsupported. */
  listFrameHours: () => number[];
  /** Stop autoplay/playback before stepping. */
  begin: () => void;
  /** Set + await the frame (per-frame dual gate: bytes ready AND presented). */
  showFrame: (hour: number) => Promise<boolean>;
  /** Repaint-then-read snapshot of the live canvas, downscaled to maxWidth. */
  captureFrame: (maxWidth?: number) => Promise<HTMLCanvasElement | null>;
  /** Valid time of the currently displayed frame (after showFrame resolves). */
  getDisplayedValidTimeISO: () => string | null;
  /** Opaque timeline snapshot taken before stepping; handed back to restore(). */
  getRestoreTarget: () => unknown;
  restore: (token: unknown) => void;
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
const GIF_FRAME_DELAY_MS = 200;
const GIF_END_HOLD_MS = 1200;
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

  const abortedRef = useRef(false);
  const runningRef = useRef(false);
  const workerRef = useRef<Worker | null>(null);

  const available = Boolean(frameDriver) && (frameDriver?.listFrameHours().length ?? 0) >= 2;

  /** Pre-generate summary shown in the idle state (§3.2: estimated size up front). */
  const buildPlan = useCallback((): GifExportPlan | null => {
    if (!frameDriver) {
      return null;
    }
    const hours = frameDriver.listFrameHours();
    if (hours.length < 2) {
      return null;
    }
    const cap = frameCapForState(buildScreenshotState?.() ?? null);
    const frameCount = Math.min(hours.length, cap);
    return {
      frameCount,
      totalHours: hours.length,
      estimatedBytes: frameCount * GIF_ESTIMATED_BYTES_PER_FRAME,
      playSeconds: ((frameCount - 1) * GIF_FRAME_DELAY_MS + GIF_END_HOLD_MS) / 1000,
    };
  }, [buildScreenshotState, frameDriver]);

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
    const hours = frameDriver.listFrameHours();
    if (hours.length < 2) {
      setStatus("error");
      setError("This selection doesn't have enough frames to animate.");
      return;
    }

    const selected = sampleHours(hours, frameCapForState(baseState));
    runningRef.current = true;
    abortedRef.current = false;
    releaseGif();
    setError(null);
    setStatus("capturing");
    setProgress({ done: 0, total: selected.length });

    const restoreTarget = frameDriver.getRestoreTarget();
    frameDriver.begin();
    const legend = getLegend?.() ?? null;
    const { buildShareOverlayLines, composeShareFrame } = await import("@/lib/screenshot_export");

    let worker: Worker | null = null;
    let composeCanvas: HTMLCanvasElement | null = null;
    let dims: { width: number; height: number } | null = null;
    let written = 0;

    try {
      for (let i = 0; i < selected.length; i += 1) {
        if (abortedRef.current) {
          break;
        }
        const hour = selected[i];
        const shown = await frameDriver.showFrame(hour);
        if (abortedRef.current) {
          break;
        }
        if (!shown) {
          // Frame never became ready (eviction, fetch failure) — skip it
          // rather than aborting the whole run.
          setProgress((current) => ({ ...current, done: current.done + 1 }));
          continue;
        }
        const mapCanvas = await frameDriver.captureFrame(GIF_OUTPUT_WIDTH);
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

        const frameState: ScreenshotExportState = {
          ...baseState,
          fh: hour,
          validTimeISO: frameDriver.getDisplayedValidTimeISO() ?? baseState.validTimeISO,
        };
        await composeShareFrame(composeCanvas!, mapCanvas, {
          width: dims.width,
          height: dims.height,
          pixelRatio: 1,
          legend,
          overlayLines: buildShareOverlayLines(frameState, legend),
          isMobile: baseState.isMobile,
        });
        const composeCtx = composeCanvas!.getContext("2d");
        if (!composeCtx) {
          continue;
        }
        const imageData = composeCtx.getImageData(0, 0, dims.width, dims.height);
        const isLast = i === selected.length - 1;
        worker!.postMessage(
          {
            type: "frame",
            buffer: imageData.data.buffer,
            delay: isLast ? GIF_END_HOLD_MS : GIF_FRAME_DELAY_MS,
            index: written,
          },
          [imageData.data.buffer],
        );
        written += 1;
        setProgress({ done: i + 1, total: selected.length });
      }

      if (abortedRef.current) {
        setStatus("cancelled");
        return;
      }
      if (written < 2 || !worker) {
        setStatus("error");
        setError("Couldn't capture enough frames. Try again.");
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
      frameDriver.restore(restoreTarget);
    }
  }, [buildScreenshotState, frameDriver, getLegend, releaseGif]);

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
  }, [open, releaseGif]);

  useEffect(() => {
    return () => {
      abortedRef.current = true;
      workerRef.current?.terminate();
    };
  }, []);

  return {
    available,
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
