/**
 * Every product event the app may emit. This array is the runtime source of
 * truth (the type is derived from it) so a unit test can assert that each name
 * is also on the PostHog allowlist — a name added here but not to
 * `ALLOWED_EVENT_NAMES` is silently dropped at capture time.
 */
export const ANALYTICS_EVENT_NAMES = [
  "viewer_opened",
  "viewer_session_ended",
  "forecast_page_viewed",
  "share_initiated",
  "share_completed",
  "pro_gate_hit",
  "model_loaded",
  "variable_changed",
  "frame_scrubbed",
  "model_selected",
  "variable_selected",
  "region_selected",
  // Coverage (data-domain) toggle flips. `effective_domain` also rides on the
  // viewer session events — global go-live design §7 instrumentation, the data
  // the global-by-default flip decision is made on.
  "coverage_selected",
  // Globe-view toggle flips (Globe v1 / Phase G2), carrying from_projection /
  // to_projection. `effective_projection` also rides on the viewer session
  // events, so adoption can be read without joining on the toggle event.
  "projection_selected",
  "animation_started",
  "legend_opened",
  "share_clicked",
  "auth_load_failed",
] as const;

export type AnalyticsEventName = (typeof ANALYTICS_EVENT_NAMES)[number];
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