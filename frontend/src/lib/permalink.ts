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

export function replaceUrlQuery(search: string): void {
  if (typeof window === "undefined") {
    return;
  }
  const normalizedSearch = search || "";
  const { pathname, hash } = window.location;
  window.history.replaceState(null, "", `${pathname}${normalizedSearch}${hash}`);
}
