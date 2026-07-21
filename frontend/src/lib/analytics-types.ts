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
  | "share_clicked"
  | "auth_load_failed";

export type AnalyticsEventProperties = Record<
  string,
  string | number | boolean | null | undefined
>;

// Channel taxonomy for share_completed (share modal overhaul Phase 0).
// native_share and gif land with the Phase 1/3 UI; the values are fixed now so
// dashboards can segment the funnel without a later rename.
export type ShareChannel =
  | "download"
  | "copy"
  | "native_share"
  | "twf_post"
  | "gif";