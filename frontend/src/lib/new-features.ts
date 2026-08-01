export type NewFeatureId = "sounding" | "globe-view";

/**
 * Product-managed list of features that should carry the global "New" badge.
 * Remove an id from this set to retire its badge on every surface.
 */
const ACTIVE_NEW_FEATURES = new Set<NewFeatureId>([
  "sounding",
  "globe-view",
]);

export function isNewFeature(feature: NewFeatureId): boolean {
  return ACTIVE_NEW_FEATURES.has(feature);
}
