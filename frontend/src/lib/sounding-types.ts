/**
 * `POST /api/v4/forecast/sounding` response shape (Skew-T design §5, Phase 2).
 *
 * Nulls appear wherever the stack held nodata; the renderer must break lines at
 * them rather than bridging or emitting NaN.
 */

/**
 * Fallback model list, used ONLY when the capabilities payload predates the
 * per-model `soundings` flag (Phase 6). It exists so a capabilities blob cached
 * by an older client cannot hide the sounding toggle for HRRR, which has been
 * live since Phase 3.
 */
export const SOUNDING_MODELS_FALLBACK = ["hrrr"] as const;

/** Shape this module needs from a capabilities model-catalog entry. */
export type SoundingCapability = { soundings?: boolean } | null | undefined;

/**
 * Whether the sounding panel should be offered for `model`.
 *
 * The server computes `soundings` from the same gate the endpoint enforces
 * (`CARTOSKY_SOUNDING_MODELS` ∩ the pipeline's supported models), so the toggle
 * and the 404 cannot disagree. An entry that simply lacks the key is an old
 * payload, not a "no" — hence the fallback.
 */
export function modelSupportsSounding(
  model: string | null | undefined,
  capability?: SoundingCapability,
): boolean {
  const normalized = String(model ?? "").trim().toLowerCase();
  if (!normalized) return false;
  const flag = capability?.soundings;
  if (typeof flag === "boolean") return flag;
  return (SOUNDING_MODELS_FALLBACK as readonly string[]).includes(normalized);
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
  /**
   * The model's own CAPE diagnostic, J/kg. Only on format_version ≥ 2 stacks.
   * NOT the same quantity across models — HRRR/GFS ship surface-based CAPE,
   * ECMWF ships most-unstable — which is why `model_cape_label` exists.
   */
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
  /**
   * The model's native CAPE — a side-by-side diagnostic, never our parcel.
   * Stable field name across models; `model_cape_label` on the response says
   * which quantity it actually is.
   */
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
  /**
   * The model's own ladder, descending: 37 levels for HRRR, 21 for GFS, 12 for
   * ECMWF (design §10). Never assume a length — every overlay indexes this
   * array in parallel with the per-level series.
   */
  levels_hPa: number[];
  units: Record<string, string>;
  /**
   * What the SB parcel IS, in the server's own words (design decision #5).
   * Rendered verbatim — the client must never restate the parcel definition,
   * or the two can drift apart.
   */
  parcel_definition?: string;
  /**
   * Label for the `indices.model_sbcape` row, from the stack sidecar
   * ("HRRR SBCAPE" / "GFS SBCAPE" / "MUCAPE"). The field name is a stable
   * contract but the quantity is NOT the same across models — ECMWF ships
   * most-unstable CAPE — so the label must come from the server, never the
   * client. Absent on pre-Phase-6 responses.
   */
  model_cape_label?: string | null;
  frames: SoundingFrame[];
  generated_at: string;
};
