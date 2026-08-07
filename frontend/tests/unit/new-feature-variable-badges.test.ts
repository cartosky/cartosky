import { describe, expect, it } from "vitest";

import {
  NEW_MODEL_BADGES,
  NEW_VARIABLE_BADGES,
  isNewFeature,
  newFeatureForId,
} from "@/lib/new-features";

/**
 * The inline "New" pill on picker rows is driven entirely by these maps plus
 * the ACTIVE_NEW_FEATURES registry: adding a variable is one map entry, and
 * retiring the badge is one registry deletion. These tests pin that wiring
 * (the rendered pill itself is a Playwright concern — vitest runs in node).
 */
describe("newFeatureForId", () => {
  it("badges both SST variables while sst-layer is active", () => {
    expect(NEW_VARIABLE_BADGES.sst).toBe("sst-layer");
    expect(NEW_VARIABLE_BADGES.sst_anom).toBe("sst-layer");
    expect(isNewFeature("sst-layer")).toBe(true);
    expect(newFeatureForId(NEW_VARIABLE_BADGES, "sst")).toBe("sst-layer");
    expect(newFeatureForId(NEW_VARIABLE_BADGES, "sst_anom")).toBe("sst-layer");
  });

  it("returns null for unmapped and empty ids", () => {
    expect(newFeatureForId(NEW_VARIABLE_BADGES, "tmp2m")).toBeNull();
    expect(newFeatureForId(NEW_VARIABLE_BADGES, "")).toBeNull();
    expect(newFeatureForId(NEW_VARIABLE_BADGES, null)).toBeNull();
    expect(newFeatureForId(NEW_VARIABLE_BADGES, undefined)).toBeNull();
  });

  it("gates on the registry, so an inactive mapping renders nothing", () => {
    // Simulates retiring "sst-layer" from ACTIVE_NEW_FEATURES: the map entry
    // survives, the badge does not.
    expect(newFeatureForId({ sst: "unshipped" as never }, "sst")).toBeNull();
  });

  it("supports model rows through the same helper", () => {
    expect(newFeatureForId(NEW_MODEL_BADGES, "sst")).toBeNull();
    expect(newFeatureForId({ sst: "sst-layer" }, "sst")).toBe("sst-layer");
  });

  it("does not use prototype keys as badge mappings", () => {
    expect(newFeatureForId(NEW_VARIABLE_BADGES, "toString")).toBeNull();
  });
});
