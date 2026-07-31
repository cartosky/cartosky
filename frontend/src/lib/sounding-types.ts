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
  /** HRRR's own surface CAPE, J/kg. Only on format_version ≥ 2 stacks. */
  cape_sfc?: number | null;
};

/**
 * Server-computed thermodynamics (Phase 4). Every field is independently
 * nullable: a capped sounding genuinely has no LFC/EL, and a nodata column has
 * no indices at all. The client does zero thermodynamics — it only formats.
 */
export type SoundingIndices = {
  /** Surface-based CAPE/CIN, J/kg, virtual-temperature corrected. */
  sbcape: number | null;
  sbcin: number | null;
  /** 100 hPa mixed-layer CAPE/CIN, J/kg. */
  mlcape: number | null;
  mlcin: number | null;
  lcl_hPa: number | null;
  lcl_C: number | null;
  lfc_hPa: number | null;
  el_hPa: number | null;
  pwat_mm: number | null;
  /** HRRR's native SBCAPE — a side-by-side diagnostic, not our parcel. */
  model_sbcape: number | null;
};

/**
 * Server-computed per-level overlay inputs (Phase 5).
 *
 * The three arrays are index-aligned with `levels_hPa` — including `null` at
 * every below-ground level — even though the server computes them on the
 * surface-anchored column. That keeps the client's existing parallel-array
 * indexing (`anchorProfile`) working unchanged.
 *
 * Absent on responses served before Phase 5: every overlay that reads this must
 * render nothing rather than assume the block exists.
 */
export type SoundingProfiles = {
  /** Wet-bulb temperature, °C. */
  tw: (number | null)[];
  /** Equivalent potential temperature, K. */
  theta_e: (number | null)[];
  /** Height above ground, m — hypsometrically reconstructed, surface = 0. */
  height_m_agl: (number | null)[];
  /** Surface-block values; the surface has no slot on the isobaric ladder. */
  surface_tw?: number | null;
  surface_theta_e?: number | null;
};

/** SB parcel ascent polyline, index-aligned (hPa / °C). */
export type SoundingParcel = {
  p: number[];
  t: number[];
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
  /** Phase 4; absent on responses served before it shipped. */
  indices?: SoundingIndices | null;
  parcel?: SoundingParcel | null;
  /** Phase 5; absent on responses served before it shipped. */
  profiles?: SoundingProfiles | null;
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
  /**
   * What the SB parcel IS, in the server's own words (design decision #5).
   * Rendered verbatim — the client must never restate the parcel definition,
   * or the two can drift apart.
   */
  parcel_definition?: string;
  frames: SoundingFrame[];
  generated_at: string;
};
