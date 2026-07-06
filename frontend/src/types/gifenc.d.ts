// Minimal typings for gifenc (no bundled types). Covers the subset used by
// gif_encode_worker.ts; see https://github.com/mattdesl/gifenc for the full API.
declare module "gifenc" {
  export type GifPalette = number[][];

  export type GifWriteFrameOptions = {
    palette?: GifPalette;
    /** Frame delay in milliseconds. */
    delay?: number;
    /** Loop count; 0 = infinite. Only honored on the first frame. */
    repeat?: number;
    transparent?: boolean;
    transparentIndex?: number;
    first?: boolean;
    dispose?: number;
  };

  export type GifEncoderInstance = {
    writeFrame(
      index: Uint8Array,
      width: number,
      height: number,
      options?: GifWriteFrameOptions,
    ): void;
    finish(): void;
    bytes(): Uint8Array;
    bytesView(): Uint8Array;
    reset(): void;
  };

  export function GIFEncoder(options?: { auto?: boolean; initialCapacity?: number }): GifEncoderInstance;
  export function quantize(
    rgba: Uint8Array | Uint8ClampedArray,
    maxColors: number,
    options?: { format?: string; oneBitAlpha?: boolean; clearAlpha?: boolean },
  ): GifPalette;
  export function applyPalette(
    rgba: Uint8Array | Uint8ClampedArray,
    palette: GifPalette,
    format?: string,
  ): Uint8Array;
}
