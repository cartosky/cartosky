import type { StyleSpecification } from "maplibre-gl";

import type { GridManifestFrame, GridManifestResponse } from "@/lib/api";
import { API_ORIGIN } from "@/lib/config";
import { selectGridManifestLod } from "@/lib/grid-lod";
import { buildPermalinkSearch } from "@/lib/permalink";

export const RADAR_PREVIEW_MODEL = "mrms";
export const RADAR_PREVIEW_VARIABLE = "reflectivity";
export const RADAR_PREVIEW_REGION = "conus";
export const RADAR_PREVIEW_ZOOM = 9;
export const RADAR_PREVIEW_LOOP_MS = 875;
export const RADAR_PREVIEW_FRAME_UPDATE_TIMEOUT_MS = RADAR_PREVIEW_LOOP_MS * 2;
export const RADAR_PREVIEW_INITIAL_TIMEOUT_MS = 8000;
export const RADAR_PREVIEW_LOOP_FRAME_COUNT = { min: 3, max: 5 } as const;

const IS_HIDPI = typeof window !== "undefined" && window.devicePixelRatio > 1;
const CARTO_TILE_SUFFIX = IS_HIDPI ? "@2x" : "";
const CARTO_TILE_SIZE = 256;

const CARTO_DARK_BASE_TILES = [
  `https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

export type PreviewFrame = {
  hour: number;
  url: string;
  validTime: string | null;
};

/** MRMS CONUS manifest bbox — v1 gate; edge cases near AK/Caribbean/ocean are acceptable. */
export function isRadarPreviewAvailable(lat: number, lon: number): boolean {
  return lon >= -134 && lon <= -60 && lat >= 24 && lat <= 55;
}

export function normalizeRadarFrameUrl(url: string | null | undefined): string | null {
  const trimmed = String(url ?? "").trim();
  if (!trimmed) {
    return null;
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const root = API_ORIGIN.replace(/\/$/, "");
  return `${root}${trimmed.startsWith("/") ? "" : "/"}${trimmed}`;
}

export function selectPreviewLod(manifest: GridManifestResponse | null): {
  lodLevel: number | null;
  lod: ReturnType<typeof selectGridManifestLod>;
} {
  const lod = selectGridManifestLod(manifest, RADAR_PREVIEW_ZOOM);
  return {
    lod,
    lodLevel: lod && Number.isFinite(Number(lod.level)) ? Number(lod.level) : null,
  };
}

export function resolvePreviewFrameForHour(
  manifest: GridManifestResponse,
  hour: number,
  preferredLodLevel: number | null,
): GridManifestFrame | null {
  const lods = Array.isArray(manifest.lods) ? manifest.lods : [];
  if (preferredLodLevel !== null) {
    const preferred = lods.find((entry) => Number(entry.level) === preferredLodLevel);
    const match = preferred?.frames?.find((frame) => Number(frame.fh) === hour);
    if (match) {
      return match;
    }
  }
  for (const lod of lods) {
    const match = lod.frames?.find((frame) => Number(frame.fh) === hour);
    if (match) {
      return match;
    }
  }
  return null;
}

export function buildPreviewLoopFrames(
  manifest: GridManifestResponse,
  lodLevel: number | null,
): PreviewFrame[] {
  const lod = selectGridManifestLod(manifest, RADAR_PREVIEW_ZOOM);
  const hours = (Array.isArray(lod?.frames) ? lod.frames : [])
    .map((frame) => Number(frame.fh))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);

  const uniqueHours = Array.from(new Set(hours));
  const count = Math.min(RADAR_PREVIEW_LOOP_FRAME_COUNT.max, uniqueHours.length);
  const loopHours = uniqueHours.slice(-count);

  return loopHours
    .map((hour) => {
      const frame = resolvePreviewFrameForHour(manifest, hour, lodLevel);
      const url = normalizeRadarFrameUrl(frame?.url ?? frame?.file ?? null);
      if (!url) {
        return null;
      }
      return {
        hour,
        url,
        validTime: typeof frame?.valid_time === "string" ? frame.valid_time : null,
      };
    })
    .filter((frame): frame is PreviewFrame => frame !== null);
}

export function viewerRadarHref(lat: number, lon: number): string {
  return `/viewer${buildPermalinkSearch({
    model: RADAR_PREVIEW_MODEL,
    var: RADAR_PREVIEW_VARIABLE,
    region: RADAR_PREVIEW_REGION,
    lat,
    lon,
    z: RADAR_PREVIEW_ZOOM,
  })}`;
}

export function formatRadarFrameAge(validTime: string | null | undefined): string {
  if (!validTime) {
    return "";
  }
  const timestamp = Date.parse(validTime);
  if (!Number.isFinite(timestamp)) {
    return "";
  }
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) {
    return "just now";
  }
  return `${minutes}m ago`;
}

export function buildRadarPreviewMapStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      "radar-preview-basemap": {
        type: "raster",
        tiles: CARTO_DARK_BASE_TILES,
        tileSize: CARTO_TILE_SIZE,
      },
    },
    layers: [
      {
        id: "radar-preview-background",
        type: "background",
        paint: {
          "background-color": "#1f2a33",
        },
      },
      {
        id: "radar-preview-basemap",
        type: "raster",
        source: "radar-preview-basemap",
        paint: {
          "raster-brightness-min": 0.08,
          "raster-brightness-max": 0.94,
          "raster-contrast": -0.06,
          "raster-saturation": -0.08,
        },
      },
    ],
  };
}
