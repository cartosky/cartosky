export type AnalyticsEventName =
  | "viewer_opened"
  | "viewer_session_ended"
  | "forecast_page_viewed"
  | "share_initiated"
  | "share_completed"
  | "pro_gate_hit"
  | "model_loaded"
  | "variable_changed"
  | "frame_scrubbed"
  | "model_selected"
  | "variable_selected"
  | "region_selected"
  | "animation_started"
  | "legend_opened"
  | "share_clicked";

export type AnalyticsEventProperties = Record<
  string,
  string | number | boolean | null | undefined
>;