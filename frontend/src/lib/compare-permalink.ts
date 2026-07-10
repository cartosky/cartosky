export type ComparePermalinkState = {
  lm?: string;   // left model
  lv?: string;   // left variable
  lp?: string;   // left ensemble stats product key (stats design §7); "mean"/absent = mean
  lr?: string;   // left run
  rm?: string;   // right model
  rv?: string;   // right variable
  rp?: string;   // right ensemble stats product key
  rr?: string;   // right run
  fh?: number;   // shared forecast hour
  lat?: number;
  lon?: number;
  z?: number;
  mode?: "split" | "diff";  // compare mode (default "split")
};

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

export function readComparePermalink(): ComparePermalinkState {
  if (typeof window === "undefined") {
    return {};
  }

  const params = new URLSearchParams(window.location.search);
  const state: ComparePermalinkState = {};

  const leftModel = readStringParam(params, "lm");
  if (leftModel) {
    state.lm = leftModel;
  }

  const leftVar = readStringParam(params, "lv");
  if (leftVar) {
    state.lv = leftVar;
  }

  const leftProduct = readStringParam(params, "lp");
  if (leftProduct) {
    state.lp = leftProduct.toLowerCase();
  }
  const leftRun = readStringParam(params, "lr");
  if (leftRun) {
    state.lr = leftRun;
  }

  const rightModel = readStringParam(params, "rm");
  if (rightModel) {
    state.rm = rightModel;
  }

  const rightVar = readStringParam(params, "rv");
  if (rightVar) {
    state.rv = rightVar;
  }

  const rightProduct = readStringParam(params, "rp");
  if (rightProduct) {
    state.rp = rightProduct.toLowerCase();
  }
  const rightRun = readStringParam(params, "rr");
  if (rightRun) {
    state.rr = rightRun;
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
  if (Number.isFinite(z) && Number(z) >= 0 && Number(z) <= 24) {
    state.z = Number(z);
  }

  const mode = readStringParam(params, "mode");
  state.mode = mode === "diff" ? "diff" : "split";

  return state;
}

export function buildComparePermalinkSearch(state: ComparePermalinkState): string {
  const params = new URLSearchParams();

  // In diff mode the variable is shared: lv and rv must serialize as equal
  // values. lv is the source of truth (falling back to rv if only it is set).
  const isDiff = state.mode === "diff";
  const sharedVar = isDiff ? (state.lv ?? state.rv) : undefined;
  const leftVar = isDiff ? sharedVar : state.lv;
  const rightVar = isDiff ? sharedVar : state.rv;
  // Product is shared in diff mode exactly like the variable (stats design:
  // the diff answers "same field, two runs/models"). "mean" never serializes.
  const sharedProduct = isDiff ? (state.lp ?? state.rp) : undefined;
  const leftProduct = isDiff ? sharedProduct : state.lp;
  const rightProduct = isDiff ? sharedProduct : state.rp;

  if (state.lm) {
    params.set("lm", state.lm);
  }
  if (leftVar) {
    params.set("lv", leftVar);
  }
  if (leftProduct && leftProduct !== "mean") {
    params.set("lp", leftProduct);
  }
  if (state.lr) {
    params.set("lr", state.lr);
  }
  if (state.rm) {
    params.set("rm", state.rm);
  }
  if (rightVar) {
    params.set("rv", rightVar);
  }
  if (rightProduct && rightProduct !== "mean") {
    params.set("rp", rightProduct);
  }
  if (state.rr) {
    params.set("rr", state.rr);
  }
  if (Number.isFinite(state.fh) && Number(state.fh) >= 0) {
    params.set("fh", String(Math.round(Number(state.fh))));
  }
  if (Number.isFinite(state.lat) && Number(state.lat) >= -90 && Number(state.lat) <= 90) {
    params.set("lat", Number(state.lat).toFixed(5));
  }
  if (Number.isFinite(state.lon) && Number(state.lon) >= -180 && Number(state.lon) <= 180) {
    params.set("lon", Number(state.lon).toFixed(5));
  }
  if (Number.isFinite(state.z) && Number(state.z) >= 0 && Number(state.z) <= 24) {
    params.set("z", Number(state.z).toFixed(2));
  }
  if (isDiff) {
    params.set("mode", "diff");
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

/** Query keys the compare page owns (writes) in its permalink. */
const COMPARE_PERMALINK_KEYS = new Set([
  "lm", "lv",
  "lp", "lr", "rm", "rv",
  "rp", "rr", "fh", "lat", "lon", "z", "mode",
]);

/**
 * Merge params this page does NOT own (e.g. `?screenshot=1`, `legend`,
 * `basemap`, UTM tags) from the live URL into a freshly built permalink
 * search, so address-bar writes never strip them. Share links are built
 * WITHOUT this on purpose — foreign params must not leak into shared URLs.
 */
export function withForeignSearchParams(search: string): string {
  if (typeof window === "undefined") {
    return search;
  }
  const params = new URLSearchParams(search);
  for (const [key, value] of new URLSearchParams(window.location.search)) {
    if (!COMPARE_PERMALINK_KEYS.has(key)) {
      params.append(key, value);
    }
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}
