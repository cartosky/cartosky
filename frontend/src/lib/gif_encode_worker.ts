/// <reference lib="webworker" />
// GIF encoder worker (share overhaul Phase 3, plan §3.2). Frames stream in as
// RGBA buffers and are encoded incrementally so at most one frame's pixels are
// held here at a time. The palette is quantized once from the first frame and
// reused for every frame (fixed palette: stable colors across the loop, no
// per-frame quantization cost).

import { GIFEncoder, applyPalette, quantize } from "gifenc";

type StartMessage = { type: "start"; width: number; height: number };
type FrameMessage = { type: "frame"; buffer: ArrayBuffer; delay: number; index: number };
type FinishMessage = { type: "finish" };
type AbortMessage = { type: "abort" };
export type GifWorkerInMessage = StartMessage | FrameMessage | FinishMessage | AbortMessage;

export type GifWorkerOutMessage =
  | { type: "frame-encoded"; index: number }
  | { type: "done"; buffer: ArrayBuffer }
  | { type: "error"; message: string };

const scope = self as unknown as DedicatedWorkerGlobalScope;

let encoder: ReturnType<typeof GIFEncoder> | null = null;
let palette: number[][] | null = null;
let width = 0;
let height = 0;
let framesWritten = 0;

scope.onmessage = (event: MessageEvent<GifWorkerInMessage>) => {
  const message = event.data;
  try {
    if (message.type === "start") {
      encoder = GIFEncoder();
      palette = null;
      width = message.width;
      height = message.height;
      framesWritten = 0;
      return;
    }
    if (message.type === "frame") {
      if (!encoder) {
        return;
      }
      const rgba = new Uint8ClampedArray(message.buffer);
      if (!palette) {
        palette = quantize(rgba, 256);
      }
      const indexed = applyPalette(rgba, palette);
      encoder.writeFrame(indexed, width, height, {
        palette,
        delay: message.delay,
        // Infinite loop; the option only takes effect on the first frame.
        ...(framesWritten === 0 ? { repeat: 0 } : {}),
      });
      framesWritten += 1;
      scope.postMessage({ type: "frame-encoded", index: message.index } satisfies GifWorkerOutMessage);
      return;
    }
    if (message.type === "finish") {
      if (!encoder) {
        scope.postMessage({ type: "error", message: "Encoder not started." } satisfies GifWorkerOutMessage);
        return;
      }
      encoder.finish();
      const bytes = encoder.bytes();
      encoder = null;
      palette = null;
      // Copy into a plain ArrayBuffer: bytes() may be a view over a larger
      // internal buffer, and TS types the transfer list as ArrayBuffer only.
      const outBuffer = new ArrayBuffer(bytes.byteLength);
      new Uint8Array(outBuffer).set(bytes);
      scope.postMessage(
        { type: "done", buffer: outBuffer } satisfies GifWorkerOutMessage,
        [outBuffer],
      );
      return;
    }
    if (message.type === "abort") {
      encoder = null;
      palette = null;
    }
  } catch (error) {
    scope.postMessage({
      type: "error",
      message: error instanceof Error ? error.message : "GIF encoding failed.",
    } satisfies GifWorkerOutMessage);
  }
};
