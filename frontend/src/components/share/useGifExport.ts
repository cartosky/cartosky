// GIF export state for the share modal — Phase 2 stub.
// Phase 3 (share overhaul plan §5) replaces this with the real client-side
// frame-stepping capture + gifenc worker encode. The shape exists now so the
// GIF tab and its wiring don't move when the feature lands.

export type GifExportState = {
  available: false;
};

export function useGifExport(): GifExportState {
  return { available: false };
}
