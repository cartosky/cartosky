export type NewFeatureId = "sounding" | "globe-view" | "coverage" | "sst-layer";

/**
 * Product-managed list of features that should carry the global "New" badge.
 * Remove an id from this set to retire its badge on every surface.
 */
const ACTIVE_NEW_FEATURES = new Set<NewFeatureId>([
  "sounding",
  "globe-view",
  "coverage",
  "sst-layer",
]);

export function isNewFeature(feature: NewFeatureId): boolean {
  return ACTIVE_NEW_FEATURES.has(feature);
}

/**
 * Variable ids that carry an inline "New" pill in the variable picker.
 * Adding a newly shipped variable is one entry here; retiring the badge is
 * removing its feature id from ACTIVE_NEW_FEATURES (no edit needed here).
 */
export const NEW_VARIABLE_BADGES: Record<string, NewFeatureId> = {
  sst: "sst-layer",
  sst_anom: "sst-layer",
};

/** Same shape for model rows — empty until a new model ships. */
export const NEW_MODEL_BADGES: Record<string, NewFeatureId> = {};

/** Active feature id for a variable/model row, or null when it earns no badge. */
export function newFeatureForId(
  badges: Record<string, NewFeatureId>,
  id: string | null | undefined,
): NewFeatureId | null {
  if (!id) return null;
  const feature = badges[id];
  return feature && isNewFeature(feature) ? feature : null;
}
