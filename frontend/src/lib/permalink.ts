import type { PermalinkState } from "@/lib/permalink-read";

export type { PermalinkState } from "@/lib/permalink-read";
export { readPermalink } from "@/lib/permalink-read";

/**
 * Lowest zoom a permalink may carry.
 *
 * NOT zero. MapLibre's globe camera expresses zoom in a latitude-adjusted frame
 * (`zoom + log2(cos lat)` is what holds the disc's screen size constant as the
 * centre moves), so the cameras the globe exists for are legitimately NEGATIVE.
 * A `z >= 0` guard silently dropped the param for exactly those, and a reloaded
 * polar link landed on the region default instead of the shared camera.
 *
 * The bound is DERIVED from the globe camera's own floor rather than picked:
 * `constrainGlobeCamera` (globe-map.ts) allows `min(minZoom, 0) + log2(cos lat)`
 * down to lat +-89.9, and every region preset's minZoom is >= 0, so the lowest
 * reachable zoom is `log2(cos 89.9) = -9.1623`. -10 covers that with margin.
 *
 *   lat 20    (whole disc)  -0.0897
 *   lat 85.05 (old clamp)   -3.5350
 *   lat 89.5                -6.8404
 *   lat 89.9  (new clamp)   -9.1623
 *
 * -2 is NOT enough: it covers only to about lat 75, so both of the cameras this
 * fix exists for would still be dropped.
 *
 * Widening this does not change the flat map. Region presets all set minZoom
 * >= 0 and the transform's constrain enforces it, so flat cannot PRODUCE a
 * negative z; a hand-edited one is accepted here and then clamped on apply.
 */
const MIN_PERMALINK_ZOOM = -10;

/**
 * Fixed-precision without the padding: `39.83` stays `39.83` instead of
 * becoming `39.83000`, so a hand-written or shared permalink round-trips
 * byte-identically and the first URL write-back is a no-op rather than
 * gratuitous churn.
 */
function fixed(value: number, digits: number): string {
  return Number(value).toFixed(digits).replace(/\.?0+$/, "");
}

export function buildPermalinkSearch(state: PermalinkState): string {
  const params = new URLSearchParams();

  if (state.model) {
    params.set("m", state.model);
  }
  if (state.run) {
    params.set("r", state.run);
  }
  if (state.var) {
    params.set("v", state.var);
  }
  if (state.ensembleView) {
    params.set("ev", state.ensembleView);
  }
  if (state.product && state.product !== "mean") {
    params.set("product", state.product);
  }
  if (Number.isFinite(state.fh) && Number(state.fh) >= 0) {
    params.set("fh", String(Math.round(Number(state.fh))));
  }
  if (state.region) {
    params.set("reg", state.region);
  }
  if (state.domain) {
    params.set("domain", state.domain);
  }
  if (state.projection === "globe") {
    params.set("proj", state.projection);
  }
  if (Number.isFinite(state.lat) && Number(state.lat) >= -90 && Number(state.lat) <= 90) {
    params.set("lat", fixed(Number(state.lat), 5));
  }
  if (Number.isFinite(state.lon) && Number(state.lon) >= -180 && Number(state.lon) <= 180) {
    params.set("lon", fixed(Number(state.lon), 5));
  }
  if (Number.isFinite(state.z) && Number(state.z) >= MIN_PERMALINK_ZOOM && Number(state.z) <= 24) {
    params.set("z", fixed(Number(state.z), 2));
  }
  const sounding = state.sounding;
  if (
    sounding &&
    Number.isFinite(sounding.lat) && sounding.lat >= -90 && sounding.lat <= 90 &&
    Number.isFinite(sounding.lon) && sounding.lon >= -180 && sounding.lon <= 180
  ) {
    params.set("sounding", `${fixed(sounding.lat, 3)},${fixed(sounding.lon, 3)}`);
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export type ViewerPermalinkSelection = {
  model?: string | null;
  run?: string | null;
  variable?: string | null;
  ensembleView?: string | null;
  product?: string | null;
  region?: string | null;
  /** RESOLVED data domain (Phase 2B); null/"" = canonical, param omitted. */
  dataDomain?: string | null;
  /**
   * Globe v1 (Phase G2). `true` = the globe camera, serialized as `proj=globe`;
   * false/undefined = the flat default, param omitted.
   */
  globeProjection?: boolean | null;
  /** Forecast-hour candidate; non-finite values are dropped. */
  fh?: number | null;
  lat?: number;
  lon?: number;
  z?: number;
};

/**
 * Single source of truth for turning viewer selection state into a
 * PermalinkState for outbound surfaces — share modal, compare handoff,
 * feedback page context — so a new param can't be silently omitted from
 * one of them. The viewer's own address-bar write-back
 * (use-permalink-sync.ts) still builds its literal by hand; when adding a
 * param here, add it there too.
 */
export function viewerPermalinkStateFromSelection(selection: ViewerPermalinkSelection): PermalinkState {
  return {
    model: selection.model || undefined,
    run: selection.run || undefined,
    var: selection.variable || undefined,
    ensembleView: selection.ensembleView || undefined,
    product: selection.product || undefined,
    fh: selection.fh != null && Number.isFinite(selection.fh) ? Number(selection.fh) : undefined,
    region: selection.region || undefined,
    domain: selection.dataDomain || undefined,
    projection: selection.globeProjection ? "globe" : undefined,
    lat: selection.lat,
    lon: selection.lon,
    z: selection.z,
  };
}

export function replaceUrlQuery(search: string): void {
  if (typeof window === "undefined") {
    return;
  }
  const normalizedSearch = search || "";
  const { pathname, hash } = window.location;
  window.history.replaceState(null, "", `${pathname}${normalizedSearch}${hash}`);
}
