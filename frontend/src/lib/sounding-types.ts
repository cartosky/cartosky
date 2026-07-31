/**
 * `POST /api/v4/forecast/sounding` response shape (Skew-T design §5, Phase 2).
 *
 * Nulls appear wherever the stack held nodata; the renderer must break lines at
 * them rather than bridging or emitting NaN.
 */

/** Models that publish sounding stacks. v1 ships HRRR only (§7 Phase 3). */
export const SOUNDING_MODELS = ["hrrr"] as const;

export function modelSupportsSounding(model: string | null | undefined): boolean {
  const normalized = String(model ?? "").trim().toLowerCase();
  return (SOUNDING_MODELS as readonly string[]).includes(normalized);
}

export type SoundingSurface = {
  /** Surface pressure, hPa. */
  pres_sfc: number | null;
  /** 2 m temperature / dewpoint, °C. */
  t2m: number | null;
  td2m: number | null;
  /** 10 m wind components, m/s. */
  u10m: number | null;
  v10m: number | null;
};

export type SoundingFrame = {
  fh: number;
  valid_time: string | null;
  surface: SoundingSurface;
  /** Per-level arrays, index-aligned with `levels_hPa`. */
  t: (number | null)[];
  td: (number | null)[];
  u: (number | null)[];
  v: (number | null)[];
  w: (number | null)[];
};

export type SoundingGridPoint = {
  lat: number;
  lon: number;
  row: number;
  col: number;
  distance_km: number;
};

export type SoundingResponse = {
  model: string;
  run: string;
  /** Present only when a run pin was requested and degraded. */
  requested_run?: string;
  location: { lat: number; lon: number };
  grid_point: SoundingGridPoint;
  /** 37 levels, 1000 → 100 hPa descending. */
  levels_hPa: number[];
  units: Record<string, string>;
  frames: SoundingFrame[];
  generated_at: string;
};
