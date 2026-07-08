export type MeteogramPoint = {
  fh: number;
  valid_time: string | null;
  value: number | null;
};

/** One ensemble member's series inside a variable's `members` block. */
export type MeteogramMemberSeries = {
  points: MeteogramPoint[] | null;
};

export type MeteogramVariable = {
  units: string;
  points: MeteogramPoint[] | null;
  error?: string;
  /**
   * Present only when the request set `include_members` and the model
   * publishes per-member data (member pipeline Phase 5). Keys: "mean"
   * (mirrors `points`), "control", "m01".."mNN". Members without published
   * frames carry `points: null`.
   */
  members?: Record<string, MeteogramMemberSeries>;
};

export type MeteogramSeriesStatus = "ok" | "partial" | "unavailable" | "not_entitled";

export type MeteogramSeries = {
  status: MeteogramSeriesStatus;
  run_id?: string | null;
  run_time?: string | null;
  /**
   * Newest complete run for this model, independent of pins and of the
   * members-ready run preference — the authoritative ceiling for run
   * selectors. `run_id` is the run actually SERVED (a pin or an older
   * members-ready run may make it older than this).
   */
  latest_complete_run?: string | null;
  variables?: Record<string, MeteogramVariable>;
};

export type MeteogramResponse = {
  location: { lat: number; lon: number };
  generated_at: string;
  run_policy: { type: string };
  series: Record<string, MeteogramSeries>;
};
