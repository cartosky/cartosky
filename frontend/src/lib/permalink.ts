import type { PermalinkState } from "@/lib/permalink-read";

export type { PermalinkState } from "@/lib/permalink-read";
export { readPermalink } from "@/lib/permalink-read";

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
  if (Number.isFinite(state.lat) && Number(state.lat) >= -90 && Number(state.lat) <= 90) {
    params.set("lat", fixed(Number(state.lat), 5));
  }
  if (Number.isFinite(state.lon) && Number(state.lon) >= -180 && Number(state.lon) <= 180) {
    params.set("lon", fixed(Number(state.lon), 5));
  }
  if (Number.isFinite(state.z) && Number(state.z) >= 0 && Number(state.z) <= 24) {
    params.set("z", fixed(Number(state.z), 2));
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
