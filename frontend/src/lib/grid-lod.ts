import type { GridManifestLod, GridManifestResponse } from "@/lib/api";

function finiteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function zoomMatchesLod(lod: GridManifestLod, zoom: number): boolean {
  const minZoom = finiteNumber(lod.min_zoom);
  const maxZoom = finiteNumber(lod.max_zoom);
  if (minZoom !== null && zoom < minZoom) {
    return false;
  }
  if (maxZoom !== null && zoom >= maxZoom) {
    return false;
  }
  return true;
}

export function selectGridManifestLod(
  manifest: GridManifestResponse | null | undefined,
  zoom: number | null | undefined,
): GridManifestLod | null {
  const lods = Array.isArray(manifest?.lods)
    ? manifest.lods.filter((entry): entry is GridManifestLod => Boolean(entry && Number.isFinite(Number(entry.level))))
    : [];
  if (lods.length === 0) {
    return null;
  }

  const sorted = [...lods].sort((left, right) => Number(left.level) - Number(right.level));
  const levelZero = sorted.find((entry) => Number(entry.level) === 0) ?? sorted[0];
  const resolvedZoom = finiteNumber(zoom);
  if (resolvedZoom === null) {
    return levelZero;
  }

  const matching = sorted.find((entry) => zoomMatchesLod(entry, resolvedZoom));
  if (matching) {
    return matching;
  }
  const levelZeroMinZoom = finiteNumber(levelZero.min_zoom);
  if (levelZeroMinZoom !== null && resolvedZoom < levelZeroMinZoom) {
    return sorted[sorted.length - 1] ?? levelZero;
  }
  return levelZero;
}