export type MeteogramPoint = {
  fh: number;
  valid_time: string | null;
  value: number | null;
};

export type MeteogramVariable = {
  units: string;
  points: MeteogramPoint[] | null;
  error?: string;
};

export type MeteogramSeriesStatus = "ok" | "partial" | "unavailable" | "not_entitled";

export type MeteogramSeries = {
  status: MeteogramSeriesStatus;
  run_id?: string | null;
  run_time?: string | null;
  variables?: Record<string, MeteogramVariable>;
};

export type MeteogramResponse = {
  location: { lat: number; lon: number };
  generated_at: string;
  run_policy: { type: string };
  series: Record<string, MeteogramSeries>;
};
