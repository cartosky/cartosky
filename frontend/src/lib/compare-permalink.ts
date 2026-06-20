export type ComparePermalinkState = {
  lm?: string;   // left model
  lv?: string;   // left variable
  lr?: string;   // left run
  rm?: string;   // right model
  rv?: string;   // right variable
  rr?: string;   // right run
  fh?: number;   // shared forecast hour
  lat?: number;
  lon?: number;
  z?: number;
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

  return state;
}

export function buildComparePermalinkSearch(state: ComparePermalinkState): string {
  const params = new URLSearchParams();

  if (state.lm) {
    params.set("lm", state.lm);
  }
  if (state.lv) {
    params.set("lv", state.lv);
  }
  if (state.lr) {
    params.set("lr", state.lr);
  }
  if (state.rm) {
    params.set("rm", state.rm);
  }
  if (state.rv) {
    params.set("rv", state.rv);
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
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}
