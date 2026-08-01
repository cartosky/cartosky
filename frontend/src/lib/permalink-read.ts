export type PermalinkState = {
  model?: string;
  run?: string;
  var?: string;
  ensembleView?: string;
  /** Ensemble stats product key (stats design §7), e.g. "p50" / "prob_gt_0p5". */
  product?: string;
  fh?: number;
  region?: string;
  /** Data domain — a published build-region ID (Phase 2B); absent = canonical. */
  domain?: string;
  /**
   * Map projection (Globe v1 / Phase G2): `"globe"` or absent. Absent = the
   * flat mercator map, which is the default and is never serialized — same
   * absent-when-default rule as `domain`.
   *
   * This is a CAMERA concern, not a data selection: it never influences which
   * artifact is fetched. See docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md.
   */
  projection?: "globe";
  lat?: number;
  lon?: number;
  z?: number;
  /** Open sounding point (Skew-T design §6): `sounding=lat,lon`. */
  sounding?: { lat: number; lon: number };
};

/**
 * Lowest zoom a permalink may carry. Mirrors `MIN_PERMALINK_ZOOM` in
 * permalink.ts — the writer and reader ranges must stay identical or a link
 * this app emits is one it cannot read back. See that file for why it is
 * negative (globe cameras are latitude-adjusted; a framed pole is z ~ -6.8 and
 * the camera constrain's own floor at +-89.9 is -9.16).
 */
const MIN_PERMALINK_ZOOM = -10;

function readStringParam(params: URLSearchParams, key: string): string | undefined {
  const raw = params.get(key);
  if (!raw) {
    return undefined;
  }
  const trimmed = raw.trim();
  return trimmed || undefined;
}

function readFiniteNumberParam(params: URLSearchParams, key: string): number | undefined {
  const raw = params.get(key);
  if (raw === null) {
    return undefined;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function readPermalink(): PermalinkState {
  if (typeof window === "undefined") {
    return {};
  }

  const params = new URLSearchParams(window.location.search);
  const state: PermalinkState = {};

  const model = readStringParam(params, "m");
  if (model) {
    state.model = model;
  }

  const run = readStringParam(params, "r");
  if (run) {
    state.run = run;
  }

  const varKey = readStringParam(params, "v");
  if (varKey) {
    state.var = varKey;
  }

  const product = readStringParam(params, "product");
  if (product) {
    state.product = product.toLowerCase();
  }
  const ensembleView = readStringParam(params, "ev");
  if (ensembleView) {
    state.ensembleView = ensembleView;
  }

  const region = readStringParam(params, "reg");
  if (region) {
    state.region = region;
  }

  const domain = readStringParam(params, "domain");
  if (domain) {
    state.domain = domain.toLowerCase();
  }

  // Unknown values degrade silently to the flat map, exactly as an unknown
  // `domain` degrades to canonical: a deep link from a future build must never
  // strand a viewer in a projection this one cannot render.
  const projection = readStringParam(params, "proj");
  if (projection && projection.toLowerCase() === "globe") {
    state.projection = "globe";
  }

  const fh = readFiniteNumberParam(params, "fh");
  if (Number.isFinite(fh) && Number(fh) >= 0) {
    state.fh = Number(fh);
  }

  const lat = readFiniteNumberParam(params, "lat");
  if (Number.isFinite(lat) && Number(lat) >= -90 && Number(lat) <= 90) {
    state.lat = Number(lat);
  }

  const lon = readFiniteNumberParam(params, "lon");
  if (Number.isFinite(lon) && Number(lon) >= -180 && Number(lon) <= 180) {
    state.lon = Number(lon);
  }

  const z = readFiniteNumberParam(params, "z");
  if (Number.isFinite(z) && Number(z) >= MIN_PERMALINK_ZOOM && Number(z) <= 24) {
    state.z = Number(z);
  }

  const sounding = readStringParam(params, "sounding");
  if (sounding) {
    const [rawLat, rawLon] = sounding.split(",");
    const soundingLat = Number(rawLat);
    const soundingLon = Number(rawLon);
    if (
      Number.isFinite(soundingLat) && soundingLat >= -90 && soundingLat <= 90 &&
      Number.isFinite(soundingLon) && soundingLon >= -180 && soundingLon <= 180
    ) {
      state.sounding = { lat: soundingLat, lon: soundingLon };
    }
  }

  return state;
}
